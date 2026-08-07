import pytest

from yamibo.models import Subscription
from yamibo.subscriber import InMemoryStore, Subscriber

UMO_A = "aiocqhttp:group:111"
UMO_B = "telegram:chat:222"


@pytest.fixture
async def store():
    return InMemoryStore()


@pytest.fixture
async def sub(store):
    return Subscriber(store)


async def _mk(sub: Subscriber, tid: int, umo: str, op_uid: int = 1) -> Subscription | None:
    return await sub.subscribe(tid=tid, umo=umo, title=f"T{tid}", op_uid=op_uid, op_name="op")


async def test_subscribe_and_duplicate(store, sub):
    s1 = await _mk(sub, 574233, UMO_A)
    assert s1 is not None
    dup = await sub.subscribe(574233, UMO_A, title="T574233", op_uid=1, op_name="op")
    assert dup is None
    s2 = await _mk(sub, 574999, UMO_A)
    assert s2 is not None and s2.tid == 574999


async def test_many_subscribers_one_thread(store, sub):
    await _mk(sub, 574233, UMO_A)
    s = await _mk(sub, 574233, UMO_B)
    assert s is not None
    assert set(s.subscribers) == {UMO_A, UMO_B}


async def test_unsubscribe_removes_entry(store, sub):
    await _mk(sub, 574233, UMO_A)
    await _mk(sub, 574233, UMO_B)
    assert await sub.unsubscribe(574233, UMO_A) is True
    remaining = await sub.get_by_tid(574233)
    assert remaining is not None and remaining.subscribers == [UMO_B]
    assert await sub.unsubscribe(574233, UMO_B) is True
    assert await sub.get_by_tid(574233) is None
    assert await sub.unsubscribe(574233, UMO_A) is False


async def test_list_only_own(store, sub):
    await _mk(sub, 574233, UMO_A)
    await _mk(sub, 574999, UMO_B)
    own = await sub.list_for(UMO_A)
    assert [s.tid for s in own] == [574233]


async def test_baseline_and_fail(store, sub):
    await _mk(sub, 574233, UMO_A)
    await sub.update_baseline(574233, floor=12, pid=345)
    s2 = await sub.get_by_tid(574233)
    assert s2 is not None and s2.last_floor == 12 and s2.last_pid == 345
    for _ in range(3):
        await sub.bump_fail(574233)
    assert (await sub.get_by_tid(574233)).paused is True
    await sub.reset_fail(574233)
    s3 = await sub.get_by_tid(574233)
    assert s3.paused is False and s3.fail_count == 0


async def test_hot_targets(store, sub):
    assert await sub.add_hot_target(UMO_A) is True
    assert await sub.add_hot_target(UMO_A) is False
    assert await sub.hot_targets() == [UMO_A]
    assert await sub.remove_hot_target(UMO_A) is True


async def test_hot_state(store, sub):
    await sub.save_hot_state("2026-08-07", [1, 2])
    assert await sub.get_hot_state() == ("2026-08-07", [1, 2])
