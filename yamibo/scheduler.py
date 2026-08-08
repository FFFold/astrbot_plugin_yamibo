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
from yamibo.parser import TZ, parse_thread
from yamibo.subscriber import Subscriber

RANK_UPDATE_GRACE = 300  # 秒；榜单缓存更新时刻后等 5 分钟再抓，防源站 cron 延迟
SendFn = Callable[[str, str], Awaitable[None]]
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
    ) -> None:
        self._client = client
        self._sub = sub
        self._cfg_get = config_get
        self._send = send
        self._clock: Callable[[], float] = time.monotonic
        self._hot_incr_state: IncrState | None = None
        self._hot_daily_date: str | None = None
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
            if int(current_time[:2]) * 60 + int(current_time[3:]) < hour * 60 + minute:
                return
            _, status = await self._client.get_sign_status()
            if not status.signed_today:
                await self._client.sign()
        except Exception as e:
            logger.error("sign failed: %s", e)

    # ---- 热帖：全量日报 ----
    async def _maybe_daily_hot_push(self, *, today: str, now_time: str) -> int | None:
        """推送全量日报。

        返回 None = 无需短间隔重试（已送达/无订阅会话/未到点/已推/禁用）；
        返回秒数 = 该间隔后重试（空榜/普通异常 15 分钟，cookie 失效 60 分钟）。
        """
        if not self._cfg_get("hot_push.enable", True) or not self._cfg_get("hot_push.daily.enable", True):
            return None
        target = str(self._cfg_get("hot_push.daily.time", "20:00"))
        hm = _parse_hhmm(target)
        if hm is None:
            logger.error("hot push: daily.time 配置格式错误: %r", target)
            return None
        hour, minute = hm
        if int(now_time[:2]) * 60 + int(now_time[3:]) < hour * 60 + minute:
            return None
        if self._hot_daily_date == today:
            return None
        try:
            items = await self._client.get_hot_threads(int(self._cfg_get("hot_push.count", 10)))
            if not items:
                logger.warning("hot push: 全量推送时榜单为空，15 分钟后重试")
                return 15 * 60
            from yamibo.utils import fmt_list

            text = fmt_list("百合会 · 今日热度榜", items, hot=True)
            targets = await self._sub.hot_targets()
            if not targets:
                self._hot_daily_date = today
                await self._sub.save_hot_daily_state(today)
                logger.info("hot push: 全量日报无订阅会话，标记当日完成")
                return None
            delivered = await self._push_to_targets(text, targets)
            if not delivered:
                logger.warning("hot push: 全量日报未送达任何会话，15 分钟后重试（不标记已推）")
                return 15 * 60
            self._hot_daily_date = today
            await self._sub.save_hot_daily_state(today)
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
        """抓榜、差分、推送；返回 (下次缓存刷新时间 or None)。"""
        if not self._cfg_get("hot_push.enable", True) or not self._cfg_get("hot_push.incremental.enable", True):
            return None
        try:
            items, next_time = await self._client.get_hot_rank(int(self._cfg_get("hot_push.count", 10)))
            new_state, fresh = compute_incremental(self._hot_incr_state, items, today)
            if not fresh:
                self._hot_incr_state = new_state
                await self._sub.save_hot_incr_state(self._hot_incr_state)
                logger.info("hot push: 增量无新进榜（当前 %d 条在榜）", len(items))
                return next_time
            from yamibo.utils import fmt_list

            text = fmt_list("百合会 · 今日热度新上榜", fresh, hot=True)
            delivered = await self._push_to_targets(text)
            if delivered:
                self._hot_incr_state = new_state
                await self._sub.save_hot_incr_state(self._hot_incr_state)
                logger.info("hot push: 增量推送 %d 条新进榜: %s", len(fresh), [i.tid for i in fresh])
            else:
                logger.warning("hot push: 增量推送未送达任何会话，不标记已推（下次重试）")
            return next_time
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
                next_time = await self._maybe_incr_hot_push(today=n.strftime("%Y-%m-%d"))
                await self._sleep_until(next_time)
            except Exception:
                await self._sleep(300)

    async def _push_to_targets(self, text: str, targets: list[str] | None = None) -> bool:
        """推送订阅会话。返回是否至少送达一个会话。"""
        delivered = False
        for umo in targets if targets is not None else await self._sub.hot_targets():
            try:
                await self._send(umo, text)
                delivered = True
            except Exception as exc:
                logger.warning("hot push: failed to send to target %r: %r", umo, exc)
        return delivered

    async def _notify_auth_fail(self) -> None:
        if not self._cfg_get("limits.notify_auth_fail", False):
            return
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
                return
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
        max_floor = new_floors[-1].floor
        max_pid = new_floors[-1].pid
        for f in new_floors:
            text = f.text[: int(self._cfg_get("subscription.text_max_len", 2000))]
            header = f"【{s.title}】{s.op_name} 更新 L{f.floor}"
            body = text if text.strip() else "(无文本)"
            url = f"https://bbs.yamibo.com/thread-{s.tid}-1-1.html"
            lines = [header, body, url]
            images = f.images[: int(self._cfg_get("subscription.image_max", 50))]
            if images:
                lines.append(f"（含图片 {len(images)} 张）")
            for umo in list(s.subscribers):
                try:
                    await self._send(umo, "\n".join(lines))
                except Exception:
                    pass
        await self._sub.update_baseline(s.tid, floor=max_floor, pid=max_pid)
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
