"""定时任务：每日签到、热帖轮询、订阅轮询。逻辑方法可注入 now/sleep 便于测试。"""

import asyncio
import logging
import random
import time
from collections.abc import Awaitable, Callable
from datetime import datetime
from typing import Any

from yamibo.client import NotLoggedInError
from yamibo.parser import parse_thread
from yamibo.subscriber import Subscriber

TZ = "Asia/Shanghai"
SendFn = Callable[[str, str], Awaitable[None]]
ConfigGet = Callable[[str, Any], Any]

logger = logging.getLogger("yamibo")


def _now() -> datetime:
    from zoneinfo import ZoneInfo

    return datetime.now(ZoneInfo(TZ))


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
        self._hot_state: tuple[str, list[int]] | None = None
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
            if current_time < target:
                return
            _, status = await self._client.get_sign_status()
            if not status.signed_today:
                await self._client.sign()
        except Exception as e:
            logger.error("sign failed: %s", e)

    # ---- 热帖 ----
    async def _maybe_hot_push(self, *, today: str) -> None:
        if not self._cfg_get("hot_push.enable", True):
            return
        try:
            items = await self._client.get_hot_threads(int(self._cfg_get("hot_push.count", 10)))
            tids = [i.tid for i in items]
            if self._hot_state and self._hot_state[0] == today:
                _, prev_tids = self._hot_state
                fresh = [i for i in items if i.tid not in prev_tids]
                if fresh:
                    lines = ["【百合会热帖】"]
                    for i in fresh:
                        reply = f"（回复 {i.reply_count}）" if i.reply_count else ""
                        lines.append(f"{i.title}{reply} https://bbs.yamibo.com/thread-{i.tid}-1-1.html")
                    text = "\n".join(lines)
                    await self._push_to_targets(text)
            self._hot_state = (today, tids)
            await self._sub.save_hot_state(today, tids)
        except NotLoggedInError:
            logger.error("hot push failed: cookie 失效")
            await self._notify_auth_fail()
        except Exception as e:
            logger.error("hot push failed: %s", e)

    async def _push_to_targets(self, text: str) -> None:
        for umo in await self._sub.hot_targets():
            try:
                await self._send(umo, text)
            except Exception:
                pass

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
                target = self._cfg_get("sign.time", "10:00")
                hour, minute = (int(x) for x in str(target).split(":"))
                next_sec = ((23 - n.hour) * 3600 + (59 - n.minute) * 60 + (60 - n.second)) % 86400
                next_sec += hour * 3600 + minute * 60
                await self._sleep(max(60, next_sec) + random.uniform(0, 300))
            except Exception:
                await self._sleep(300)

    async def _run_hot_loop(self) -> None:
        while not self._stop.is_set():
            try:
                n = _now()
                await self._maybe_hot_push(today=n.strftime("%Y-%m-%d"))
                await self._sleep(int(self._cfg_get("hot_push.interval_min", 60)) * 60)
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
            asyncio.create_task(self._run_hot_loop(), name="yamibo-hot"),
            asyncio.create_task(self._run_sub_loop(), name="yamibo-sub"),
        ]

    async def stop(self) -> None:
        self._stop.set()
        for t in self._tasks:
            t.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks = []
        self._running = False
