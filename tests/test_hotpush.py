from yamibo.hotpush import compute_incremental
from yamibo.models import HotItem


def _items(*tids: int) -> list[HotItem]:
    return [HotItem(tid=t, title=f"T{t}") for t in tids]


async def test_first_run_baseline_only_no_push():
    state, fresh = compute_incremental(None, _items(1, 2, 3), today="2026-08-08")
    assert fresh == []
    assert state.date == "2026-08-08"
    assert state.last_tids == [1, 2, 3]
    assert state.pushed_tids == []


async def test_new_thread_same_day_pushed():
    state, _ = compute_incremental(None, _items(1, 2), today="2026-08-08")
    state, fresh = compute_incremental(state, _items(1, 2, 3), today="2026-08-08")
    assert [i.tid for i in fresh] == [3]
    assert state.pushed_tids == [3]


async def test_already_pushed_same_day_not_repushed():
    state, _ = compute_incremental(None, _items(1, 2), today="2026-08-08")
    state, fresh = compute_incremental(state, _items(1, 2, 3), today="2026-08-08")
    assert [i.tid for i in fresh] == [3]
    # 掉榜又回来，同天内不重推
    state, fresh = compute_incremental(state, _items(1, 2), today="2026-08-08")
    assert fresh == []
    state, fresh = compute_incremental(state, _items(1, 2, 3), today="2026-08-08")
    assert fresh == []


async def test_ranking_shuffle_without_new_tid_no_push():
    state, _ = compute_incremental(None, _items(1, 2, 3), today="2026-08-08")
    state, fresh = compute_incremental(state, _items(3, 1, 2), today="2026-08-08")
    assert fresh == []


async def test_next_day_resets_dedup():
    state, _ = compute_incremental(None, _items(1, 2), today="2026-08-08")
    state, fresh = compute_incremental(state, _items(1, 2, 3), today="2026-08-08")
    assert [i.tid for i in fresh] == [3]
    # 当天 3 掉榜
    state, fresh = compute_incremental(state, _items(1, 2), today="2026-08-08")
    assert fresh == []
    # 跨天后 3 重新进榜 → 昨日去重已清零，可再推；4 新进榜也推
    state, fresh = compute_incremental(state, _items(2, 3, 4), today="2026-08-09")
    assert [i.tid for i in fresh] == [3, 4]
    assert state.pushed_tids == [3, 4]


async def test_cross_day_without_leave_no_repush():
    state, _ = compute_incremental(None, _items(1, 2), today="2026-08-08")
    state, fresh = compute_incremental(state, _items(1, 2, 3), today="2026-08-08")
    assert [i.tid for i in fresh] == [3]
    # 昨天推过 3；今天 3 仍在榜（未掉榜）→ 不算新进榜
    state, fresh = compute_incremental(state, _items(2, 3), today="2026-08-09")
    assert [i.tid for i in fresh] == []


async def test_cross_day_keeps_last_tids_as_baseline():
    state, _ = compute_incremental(None, _items(1, 2), today="2026-08-08")
    state, fresh = compute_incremental(state, _items(2, 3), today="2026-08-09")
    # 1 掉榜、3 新进榜：只推 3
    assert [i.tid for i in fresh] == [3]


async def test_empty_ranklist_keeps_baseline():
    state, _ = compute_incremental(None, _items(1), today="2026-08-08")
    # 空榜（解析回归/登录页）→ 保留旧基线、不推送
    state, fresh = compute_incremental(state, [], today="2026-08-08")
    assert fresh == []
    assert state.last_tids == [1]
    # 恢复后只推真正新进榜的
    state, fresh = compute_incremental(state, _items(1, 2), today="2026-08-08")
    assert [i.tid for i in fresh] == [2]


async def test_empty_ranklist_first_run_keeps_no_baseline():
    # 首次运行即空榜：不建基线；恢复后先建基线不推（防轰炸）
    state, fresh = compute_incremental(None, [], today="2026-08-08")
    assert fresh == []
    assert state.last_tids == []
    state, fresh = compute_incremental(state, _items(1, 2), today="2026-08-08")
    assert fresh == []
    assert state.last_tids == [1, 2]
