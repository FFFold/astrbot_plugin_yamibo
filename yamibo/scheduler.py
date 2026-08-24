"""定时任务：每日签到、热帖全量/增量推送、订阅轮询。逻辑方法可注入 now/sleep 便于测试。"""

import asyncio
import logging
import random
import time
from collections.abc import Awaitable, Callable
from datetime import datetime
from typing import Any

from yamibo.client import NotLoggedInError
from yamibo.hotpush import IncrState, compute_incremental
from yamibo.models import PostFloor
from yamibo.parser import TZ, parse_thread
from yamibo.subscriber import Subscriber
from yamibo.utils import (
    FORWARD_CHUNK,
    build_sub_payload,
    chunk_list,
    clamp_int,
    fmt_sub_notice,
    split_plain_batches,
)

RANK_UPDATE_GRACE = 300  # 秒；榜单缓存更新时刻后等 5 分钟再抓，防源站 cron 延迟
# images 为该楼层的图片 URL 列表（已按 image_max 截断）；发送方负责按平台发送
SendFn = Callable[[str, str, list[str]], Awaitable[None]]
# 订阅内容推送：payload 由 utils.build_sub_payload 组装（转发节点列表或整批直发文本）
SubSendFn = Callable[[str, Any], Awaitable[None]]
# 判断某个 umo 会话是否运行在支持合并转发的平台上
ForwardCheck = Callable[[str], bool]
ConfigGet = Callable[[str, Any], Any]

logger = logging.getLogger("yamibo")


def _now() -> datetime:
    return datetime.now(TZ)


def _parse_hhmm(text: str) -> tuple[int, int] | None:
    """解析 HH:MM（小时允许不补零）；格式非法或越界返回 None。"""
    try:
        hour, minute = (int(x) for x in str(text).split(":"))
    except ValueError:
        return None
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        return None
    return hour, minute


def _target_sleep_seconds(now: datetime, target: str) -> int | None:
    """距下次 target 时刻（HH:MM）的秒数：当天未过则当天，已过则明天。解析失败返回 None。"""
    hm = _parse_hhmm(target)
    if hm is None:
        return None
    hour, minute = hm
    delta = (hour * 60 + minute) - (now.hour * 60 + now.minute)
    if delta <= 0:
        delta += 24 * 60
    return max(60, delta * 60 - now.second)


class Scheduler:
    def __init__(
        self,
        client,
        sub: Subscriber,
        config_get: ConfigGet,
        send: SendFn,
        *,
        send_sub: SubSendFn,
        forward_check: ForwardCheck | None = None,
    ) -> None:
        self._client = client
        self._sub = sub
        self._cfg_get = config_get
        self._send = send
        self._send_sub = send_sub
        self._forward_check = forward_check
        self._clock: Callable[[], float] = time.monotonic
        self._hot_incr_state: IncrState | None = None
        self._hot_daily_date: str | None = None
        self._auth_fail_notified_at: float | None = None  # 上次 cookie 失效告警时间（monotonic），None=从未告警
        self._tasks: list[asyncio.Task] = []
        self._stop = asyncio.Event()
        self._running = False

    def set_clock_now(self, fn: Callable[[], float]) -> None:
        self._clock = fn

    async def _sleep(self, seconds: float) -> None:
        try:
            await asyncio.wait_for(self._stop.wait(), timeout=seconds)
        except asyncio.TimeoutError:
            pass

    # ---- 签到 ----
    async def _maybe_sign(self, *, now: str, today: str) -> None:
        if not self._cfg_get("sign.enable", True):
            return
        try:
            target = str(self._cfg_get("sign.time", "10:00"))
            current_time = now.split()[1] if " " in now else now
            hm = _parse_hhmm(target)
            if hm is None:
                logger.error("sign failed: sign.time 配置格式错误: %r", target)
                return
            hour, minute = hm
            now_hm = _parse_hhmm(current_time)
            if now_hm is None or now_hm[0] * 60 + now_hm[1] < hour * 60 + minute:
                return
            _, status = await self._client.get_sign_status()
            if not status.signed_today:
                await self._client.sign()
        except Exception as e:
            logger.error("sign failed: %s", e)

    @staticmethod
    def _hot_count(cfg_get) -> int:
        """榜单条数，clamp 到 1~30（配置可能填 0 或超大值）。"""
        return clamp_int(cfg_get("hot_push.count", 10), 1, 30, 10)

    # ---- 热帖：全量日报 ----
    async def _maybe_daily_hot_push(self, *, today: str, now_time: str) -> int | None:
        """推送全量日报。

        返回 None = 无需短间隔重试（已送达/无订阅会话/未到点/已推/禁用）；
        返回秒数 = 该间隔后重试（空榜/cookie 失效 60 分钟，其余异常 15 分钟）。
        """
        if not self._cfg_get("hot_push.enable", True) or not self._cfg_get("hot_push.daily.enable", True):
            return None
        target = str(self._cfg_get("hot_push.daily.time", "20:00"))
        hm = _parse_hhmm(target)
        if hm is None:
            logger.error("hot push: daily.time 配置格式错误: %r", target)
            return None
        hour, minute = hm
        now_hm = _parse_hhmm(now_time)
        if now_hm is None or now_hm[0] * 60 + now_hm[1] < hour * 60 + minute:
            return None
        if self._hot_daily_date == today:
            return None
        try:
            items = await self._client.get_hot_threads(self._hot_count(self._cfg_get))
            if not items:
                logger.warning("hot push: 全量推送时榜单为空，60 分钟后重试")
                return 60 * 60
            from yamibo.utils import fmt_list

            text = fmt_list("百合会 · 今日热度榜", items, hot=True)
            targets = await self._sub.hot_targets()
            if not targets:
                await self._sub.save_hot_daily_state(today)
                self._hot_daily_date = today
                logger.info("hot push: 全量日报无订阅会话，标记当日完成")
                return None
            delivered = await self._push_to_targets(text, targets)
            if not delivered:
                logger.warning("hot push: 全量日报未送达任何会话，15 分钟后重试（不标记已推）")
                return 15 * 60
            await self._sub.save_hot_daily_state(today)
            self._hot_daily_date = today
            logger.info("hot push: 全量日报已推送 %d 条", len(items))
            return None
        except NotLoggedInError:
            logger.error("hot push failed: cookie 失效")
            await self._notify_auth_fail()
            return 60 * 60
        except Exception as e:
            logger.error("hot push failed: %s", e)
            return 15 * 60

    # ---- 热帖：增量雷达 ----
    async def _maybe_incr_hot_push(self, *, today: str):
        """抓榜、差分、推送。

        返回 (下次缓存刷新时间 or None)；送达失败时返回短间隔重试秒数，
        避免等 5 小时错过仍在榜的新进榜帖子。
        """
        if not self._cfg_get("hot_push.enable", True) or not self._cfg_get("hot_push.incremental.enable", True):
            return None
        try:
            items, next_time = await self._client.get_hot_rank(self._hot_count(self._cfg_get))
            new_state, fresh = compute_incremental(self._hot_incr_state, items, today)
            if not fresh:
                await self._sub.save_hot_incr_state(new_state)
                self._hot_incr_state = new_state
                logger.info("hot push: 增量无新进榜（当前 %d 条在榜）", len(items))
                return next_time
            from yamibo.utils import fmt_list

            text = fmt_list("百合会 · 今日热度新上榜", fresh, hot=True)
            delivered = await self._push_to_targets(text)
            if delivered:
                await self._sub.save_hot_incr_state(new_state)
                self._hot_incr_state = new_state
                logger.info("hot push: 增量推送 %d 条新进榜: %s", len(fresh), [i.tid for i in fresh])
                return next_time
            retry_sec = int(self._cfg_get("hot_push.incremental.interval_min", 60)) * 60
            logger.warning("hot push: 增量推送未送达任何会话，%.0f 分钟后重试（不标记已推）", retry_sec / 60)
            return retry_sec
        except NotLoggedInError:
            logger.error("hot push failed: cookie 失效")
            await self._notify_auth_fail()
            return None
        except Exception as e:
            logger.error("hot push failed: %s", e)
            return None

    async def _recover_hot_states(self) -> None:
        if self._hot_incr_state is None:
            self._hot_incr_state = await self._sub.get_hot_incr_state()
            if self._hot_incr_state:
                logger.info(
                    "hot push: 已恢复增量状态 date=%s 已推=%d 上轮=%d",
                    self._hot_incr_state.date,
                    len(self._hot_incr_state.pushed_tids),
                    len(self._hot_incr_state.last_tids),
                )
        if self._hot_daily_date is None:
            self._hot_daily_date = await self._sub.get_hot_daily_state()
            if self._hot_daily_date:
                logger.info("hot push: 已恢复全量日报状态 date=%s", self._hot_daily_date)

    async def _sleep_until(self, next_time: datetime | None) -> None:
        """sleep 到榜单下次缓存更新 + 5 分钟；解析失败用兜底间隔。

        next_time 为 aware datetime（东八区，parser.TZ），来自榜单页脚
        「下次将于 … 进行更新」的缓存提示；为 None 表示未解析到。
        """
        interval_min = int(self._cfg_get("hot_push.incremental.interval_min", 60))
        if next_time is not None:
            delta = (next_time - _now()).total_seconds() + RANK_UPDATE_GRACE
            if delta > 0:
                logger.info(
                    "hot push: 下次榜单刷新 %s，%.0f 分钟后轮询",
                    next_time.strftime("%m-%d %H:%M"), delta / 60,
                )
                await self._sleep(delta)
                return
            logger.warning(
                "hot push: 榜单刷新时间 %s 已过，%.0f 分钟兜底轮询",
                next_time.strftime("%m-%d %H:%M"), interval_min,
            )
            await self._sleep(interval_min * 60)
            return
        logger.warning("hot push: 无法解析榜单刷新时间，%.0f 分钟兜底轮询", interval_min)
        await self._sleep(interval_min * 60)

    async def _run_daily_hot_loop(self) -> None:
        while not self._stop.is_set():
            try:
                await self._recover_hot_states()  # 幂等；防 20:00 后重启当天重复推送
                n = _now()
                retry_in = await self._maybe_daily_hot_push(
                    today=n.strftime("%Y-%m-%d"), now_time=n.strftime("%H:%M")
                )
                if retry_in is not None:
                    await self._sleep(retry_in)
                    continue
                next_sec = _target_sleep_seconds(n, str(self._cfg_get("hot_push.daily.time", "20:00")))
                if next_sec is None:
                    logger.error("hot push: daily.time 配置格式错误，1 小时后重试")
                    next_sec = 3600
                await self._sleep(next_sec + random.uniform(0, 300))
            except Exception:
                await self._sleep(300)

    async def _run_incr_hot_loop(self) -> None:
        while not self._stop.is_set():
            try:
                if not self._cfg_get("hot_push.enable", True) or not self._cfg_get(
                    "hot_push.incremental.enable", True
                ):
                    logger.info("hot push: 增量推送已禁用，6 小时后再次检查")
                    await self._sleep(6 * 3600)
                    continue
                await self._recover_hot_states()
                n = _now()
                next_sleep = await self._maybe_incr_hot_push(today=n.strftime("%Y-%m-%d"))
                if isinstance(next_sleep, (int, float)):
                    await self._sleep(next_sleep)  # 送达失败：短间隔重试
                elif next_sleep is None:
                    # 异常/cookie 失效：静默兜底重试（失败原因已在 _maybe_incr_hot_push 内记录）
                    await self._sleep(int(self._cfg_get("hot_push.incremental.interval_min", 60)) * 60)
                else:
                    await self._sleep_until(next_sleep)
            except Exception:
                await self._sleep(300)

    async def _push_to_targets(self, text: str, targets: list[str] | None = None) -> bool:
        """推送订阅会话。返回是否至少送达一个会话。"""
        delivered = False
        for umo in targets if targets is not None else await self._sub.hot_targets():
            try:
                await self._send(umo, text, [])
                delivered = True
            except Exception as exc:
                logger.warning("hot push: failed to send to target %r: %r", umo, exc)
        return delivered

    async def _notify_auth_fail(self) -> None:
        """cookie 失效告警（1 小时内去重，避免多循环各自触发轰炸）。

        初始为 None（从未告警）：首次告警不因 monotonic 起始值小而被窗口吞掉。
        """
        if not self._cfg_get("limits.notify_auth_fail", False):
            return
        now = time.monotonic()
        if self._auth_fail_notified_at is not None and now - self._auth_fail_notified_at < 3600:
            return
        self._auth_fail_notified_at = now
        await self._push_to_targets("【百合会助手】cookie 已失效，请管理员在插件配置中更新 auth/saltkey")

    # ---- 订阅轮询 ----
    async def _maybe_check_subs(self) -> None:
        subs = await self._sub.all()
        for s in subs:
            if s.paused:
                continue
            try:
                await self._check_one(s)
            except NotLoggedInError:
                logger.error("sub check %s failed: cookie 失效", s.tid)
                await self._notify_auth_fail()
                continue  # 只跳过该订阅，其余订阅本轮继续检查
            except Exception as e:
                logger.error("sub check %s failed: %s", s.tid, e)
                await self._sub.bump_fail(s.tid)

    async def _check_one(self, s) -> None:
        html = await self._client.get_thread_author_view(s.tid, s.op_uid)
        tc = parse_thread(html, s.tid, skip_hidden=self._cfg_get("limits.skip_hidden_content", True))
        new_floors = [f for f in tc.floors if f.floor > s.last_floor and f.author_uid == s.op_uid]
        if not new_floors:
            await self._sub.reset_fail(s.tid)
            return
        new_floors.sort(key=lambda f: f.floor)
        text_max = clamp_int(self._cfg_get("subscription.text_max_len", 2000), 1, 100_000, 2000)
        image_max = clamp_int(self._cfg_get("subscription.image_max", 50), 0, 500, 50)
        subs = list(s.subscribers)
        # 送达语义：楼层按 FORWARD_CHUNK 分批。某批至少一个会话送达才视为该批送达；
        # 遇到全员失败批次立即停止（后续批次下轮重试），游标推进到最后一成功批次末楼层。
        # 全部批次成功后才对「成功送达的会话」发一条通知（失败仅记录日志，不阻塞游标）。
        deliver_ok: set[str] = set()
        delivered: PostFloor | None = None
        for chunk in chunk_list(new_floors, FORWARD_CHUNK):
            any_ok = False
            for umo in subs:
                # 非转发平台按字符预算拆子批（合并超限平台单条上限会必然失败）；
                # 批次/载荷构建异常属于程序错误，向上抛由 _maybe_check_subs 计数暂停，不作静默重试
                mode = "forward" if self._forward_check is not None and self._forward_check(umo) else "plain"
                batches = [chunk] if mode == "forward" else split_plain_batches(
                    s.title, s.op_name, s.tid, chunk, text_max=text_max,
                )
                umo_ok = False
                for sub_chunk in batches:
                    payload = build_sub_payload(
                        s.title, s.op_name, s.tid, sub_chunk,
                        mode=mode, text_max=text_max, image_max=image_max,
                    )
                    try:
                        await self._send_sub(umo, payload)
                        umo_ok = True
                    except Exception as exc:
                        logger.warning(
                            "sub check %s: 发送 L%d-L%d 到 %r 失败: %r",
                            s.tid, sub_chunk[0].floor, sub_chunk[-1].floor, umo, exc,
                        )
                        break
                if umo_ok:
                    any_ok = True
                    deliver_ok.add(umo)
            if not any_ok:
                break
            delivered = chunk[-1]
        else:
            notice = fmt_sub_notice(s.title, s.op_name, (f.floor for f in new_floors))
            for umo in sorted(deliver_ok):
                try:
                    await self._send(umo, notice, [])
                except Exception as exc:
                    logger.warning("sub check %s: 通知发送到 %r 失败: %r", s.tid, umo, exc)
        if delivered is None:
            logger.warning("sub check %s: 全部订阅会话发送失败，保留游标，下轮重试", s.tid)
            return
        await self._sub.update_baseline(s.tid, floor=delivered.floor, pid=delivered.pid)
        await self._sub.reset_fail(s.tid)

    # ---- 循环主体 ----
    async def _run_sign_loop(self) -> None:
        while not self._stop.is_set():
            try:
                n = _now()
                await self._maybe_sign(now=n.strftime("%Y-%m-%d %H:%M"), today=n.strftime("%Y-%m-%d"))
                next_sec = _target_sleep_seconds(n, str(self._cfg_get("sign.time", "10:00")))
                if next_sec is None:
                    logger.error("sign failed: sign.time 配置格式错误，1 小时后重试")
                    next_sec = 3600
                await self._sleep(next_sec + random.uniform(0, 300))
            except Exception:
                await self._sleep(300)

    async def _run_sub_loop(self) -> None:
        while not self._stop.is_set():
            try:
                await self._maybe_check_subs()
                await self._sleep(int(self._cfg_get("subscription.check_interval_min", 30)) * 60)
            except Exception:
                await self._sleep(300)

    def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._stop = asyncio.Event()
        self._tasks = [
            asyncio.create_task(self._run_sign_loop(), name="yamibo-sign"),
            asyncio.create_task(self._run_daily_hot_loop(), name="yamibo-hot-daily"),
            asyncio.create_task(self._run_incr_hot_loop(), name="yamibo-hot-incr"),
            asyncio.create_task(self._run_sub_loop(), name="yamibo-sub"),
        ]

    async def stop(self) -> None:
        self._stop.set()
        for t in self._tasks:
            t.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks = []
        self._running = False
