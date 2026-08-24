"""订阅数据维护：多对多、只看楼主游标、热帖目标。存储抽象便于测试与 AstrBot KV 适配。"""

import time
import uuid
from typing import Any

from yamibo.models import Subscription

SCHEMA = 1
_KEY_SUBS = "subs"
_KEY_HOT_TARGETS = "hot_targets"
_KEY_HOT_DAILY = "hot_daily_state"
_KEY_HOT_INCR = "hot_incr_state"


class KVStore:
    """异步 KV 接口。"""

    async def get(self, key: str, default: Any = None) -> Any:
        raise NotImplementedError

    async def set(self, key: str, value: Any) -> None:
        raise NotImplementedError


class InMemoryStore(KVStore):
    def __init__(self) -> None:
        self._data: dict[str, Any] = {}

    async def get(self, key: str, default: Any = None) -> Any:
        return self._data.get(key, default)

    async def set(self, key: str, value: Any) -> None:
        self._data[key] = value


class Subscriber:
    """订阅管理。数据格式带 schema 版本，损坏时重建。"""

    def __init__(self, store: KVStore) -> None:
        self._store = store

    # ---- 内部存取 ----
    async def _load(self, key: str) -> dict:
        raw = await self._store.get(key)
        if not isinstance(raw, dict) or raw.get("schema") != SCHEMA:
            return {"schema": SCHEMA, "items": []}
        return raw

    async def _save(self, key: str, data: dict) -> None:
        await self._store.set(key, data)

    async def _load_subs(self) -> list[dict]:
        data = await self._load(_KEY_SUBS)
        items = data.get("items", [])
        if not isinstance(items, list):
            return []
        return [i for i in items if isinstance(i, dict)]

    async def _save_subs(self, items: list[dict]) -> None:
        await self._save(_KEY_SUBS, {"schema": SCHEMA, "items": items})

    @staticmethod
    def _to_model(d: dict) -> Subscription:
        return Subscription(
            id=d.get("id", ""), tid=int(d.get("tid", 0)), title=d.get("title", ""),
            op_uid=int(d.get("op_uid", 0)), op_name=d.get("op_name", ""),
            last_floor=int(d.get("last_floor", 0)), last_pid=int(d.get("last_pid", 0)),
            subscribers=list(d.get("subscribers", [])),
            created_at=int(d.get("created_at", 0)),
            paused=bool(d.get("paused", False)),
            fail_count=int(d.get("fail_count", 0)),
        )

    @staticmethod
    def _to_dict(s: Subscription) -> dict:
        return {
            "id": s.id, "tid": s.tid, "title": s.title, "op_uid": s.op_uid,
            "op_name": s.op_name, "last_floor": s.last_floor, "last_pid": s.last_pid,
            "subscribers": list(s.subscribers),
            "created_at": s.created_at, "paused": s.paused, "fail_count": s.fail_count,
        }

    # ---- 异步 API ----
    async def subscribe(self, tid: int, umo: str, *, title: str, op_uid: int, op_name: str) -> Subscription | None:
        items = await self._load_subs()
        for d in items:
            if int(d.get("tid", 0)) == tid:
                if umo not in d["subscribers"]:
                    d["subscribers"].append(umo)
                    await self._save_subs(items)
                    return self._to_model(d)
                return None
        s = Subscription(
            id=uuid.uuid4().hex[:12], tid=tid, title=title, op_uid=op_uid, op_name=op_name,
            last_floor=0, last_pid=0, subscribers=[umo],
            created_at=int(time.time()),
        )
        items.append(self._to_dict(s))
        await self._save_subs(items)
        return s

    async def unsubscribe(self, tid: int, umo: str) -> bool:
        items = await self._load_subs()
        for d in items:
            if int(d.get("tid", 0)) == tid:
                subs = d["subscribers"]
                if umo in subs:
                    subs.remove(umo)
                if not subs:
                    items.remove(d)
                await self._save_subs(items)
                return True
        return False

    async def get_by_tid(self, tid: int) -> Subscription | None:
        items = await self._load_subs()
        for d in items:
            if int(d.get("tid", 0)) == tid:
                return self._to_model(d)
        return None

    async def list_for(self, umo: str) -> list[Subscription]:
        items = await self._load_subs()
        return [self._to_model(d) for d in items if umo in d.get("subscribers", [])]

    async def all(self) -> list[Subscription]:
        items = await self._load_subs()
        return [self._to_model(d) for d in items]

    async def update_baseline(self, tid: int, *, floor: int, pid: int) -> None:
        items = await self._load_subs()
        for d in items:
            if int(d.get("tid", 0)) == tid:
                d["last_floor"] = int(floor)
                d["last_pid"] = int(pid)
                break
        await self._save_subs(items)

    async def bump_fail(self, tid: int) -> None:
        items = await self._load_subs()
        for d in items:
            if int(d.get("tid", 0)) == tid:
                d["fail_count"] = int(d.get("fail_count", 0)) + 1
                if d["fail_count"] >= 3:
                    d["paused"] = True
                break
        await self._save_subs(items)

    async def reset_fail(self, tid: int) -> None:
        items = await self._load_subs()
        for d in items:
            if int(d.get("tid", 0)) == tid:
                d["fail_count"] = 0
                d["paused"] = False
                break
        await self._save_subs(items)

    async def resume_all(self) -> int:
        """恢复所有被暂停的订阅（fail_count 清零）。返回恢复数量。"""
        items = await self._load_subs()
        n = 0
        for d in items:
            if bool(d.get("paused", False)):
                d["paused"] = False
                d["fail_count"] = 0
                n += 1
        if n:
            await self._save_subs(items)
        return n

    async def add_hot_target(self, umo: str) -> bool:
        data = await self._load(_KEY_HOT_TARGETS)
        items = data.get("items", [])
        if umo in items:
            return False
        items.append(umo)
        await self._save(_KEY_HOT_TARGETS, {"schema": SCHEMA, "items": items})
        return True

    async def remove_hot_target(self, umo: str) -> bool:
        data = await self._load(_KEY_HOT_TARGETS)
        items = data.get("items", [])
        if umo not in items:
            return False
        items.remove(umo)
        await self._save(_KEY_HOT_TARGETS, {"schema": SCHEMA, "items": items})
        return True

    async def hot_targets(self) -> list[str]:
        data = await self._load(_KEY_HOT_TARGETS)
        return list(data.get("items", []))

    async def save_hot_daily_state(self, date: str) -> None:
        await self._save(_KEY_HOT_DAILY, {"schema": SCHEMA, "date": date})

    async def get_hot_daily_state(self) -> str | None:
        data = await self._load(_KEY_HOT_DAILY)
        return data.get("date") or None

    async def save_hot_incr_state(self, state) -> None:
        await self._save(
            _KEY_HOT_INCR,
            {
                "schema": SCHEMA,
                "date": state.date,
                "pushed_tids": list(state.pushed_tids),
                "last_tids": list(state.last_tids),
            },
        )

    async def get_hot_incr_state(self):
        from yamibo.hotpush import IncrState

        data = await self._load(_KEY_HOT_INCR)
        if not data.get("date"):
            return None
        return IncrState(
            date=data["date"],
            pushed_tids=list(data.get("pushed_tids", [])),
            last_tids=list(data.get("last_tids", [])),
        )
