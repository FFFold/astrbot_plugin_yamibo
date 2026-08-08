import pytest

from yamibo.hotpush import IncrState
from yamibo.models import HotItem, SignStatus
from yamibo.scheduler import RANK_UPDATE_GRACE, Scheduler
from yamibo.utils import cfg_get

UMO = "aiocqhttp:group:111"


@pytest.fixture
def make_sched():
    def build(**overrides):
        cfg = {
            "sign": {"enable": True, "time": "10:00"},
            "hot_push": {
                "enable": True,
                "count": 10,
                "daily": {"enable": True, "time": "20:00"},
                "incremental": {"enable": True, "interval_min": 60},
            },
            "subscription": {"check_interval_min": 30, "text_max_len": 2000, "image_max": 50},
            "limits": {"skip_hidden_content": True, "notify_auth_fail": False},
        }
        for dotted, value in overrides.items():
            cur = cfg
            parts = dotted.split(".")
            for part in parts[:-1]:
                cur = cur.setdefault(part, {})
            cur[parts[-1]] = value

        class FakeClient:
            def __init__(self):
                self.signed_today = False
                self.hot_items = [HotItem(tid=1, title="A"), HotItem(tid=2, title="B")]
                self.next_update = None

            async def get_sign_status(self):
                return "<html>", SignStatus(signed_today=self.signed_today)

            async def sign(self):
                self.signed_today = True

            async def get_hot_rank(self, n):
                return self.hot_items[:n], self.next_update

            async def get_hot_threads(self, n):
                return self.hot_items[:n]

        class FakeSub:
            def __init__(self):
                self.subs = []
                self.saved_incr = []
                self.saved_daily = []

            async def all(self):
                return self.subs

            async def update_baseline(self, tid, *, floor, pid):
                pass

            async def bump_fail(self, tid):
                pass

            async def reset_fail(self, tid):
                pass

            async def hot_targets(self):
                return [UMO]

            async def save_hot_incr_state(self, st):
                self.saved_incr.append(st)

            async def get_hot_incr_state(self):
                return None

            async def save_hot_daily_state(self, d):
                self.saved_daily.append(d)

            async def get_hot_daily_state(self):
                return None

        class Recorder:
            def __init__(self):
                self.sent = []

            async def send(self, umo, text):
                self.sent.append((umo, text))

        client = FakeClient()
        sub = FakeSub()
        rec = Recorder()
        s = Scheduler(client, sub, lambda k, d=None: cfg_get(cfg, k, d), rec.send)
        return s, client, sub, rec

    return build


# ---- 全量日报 ----

async def test_daily_hot_before_time_no_push(make_sched):
    s, client, sub, rec = make_sched()
    await s._maybe_daily_hot_push(today="2026-08-08", now_time="19:00")
    assert rec.sent == []


async def test_daily_hot_at_time_pushes_full_list(make_sched):
    s, client, sub, rec = make_sched()
    await s._maybe_daily_hot_push(today="2026-08-08", now_time="20:00")
    assert len(rec.sent) == 1
    assert rec.sent[0][0] == UMO
    assert "今日热度榜" in rec.sent[0][1]
    assert "A" in rec.sent[0][1] and "B" in rec.sent[0][1]
    assert sub.saved_daily == ["2026-08-08"]


async def test_daily_hot_same_day_not_repeat(make_sched):
    s, client, sub, rec = make_sched()
    await s._maybe_daily_hot_push(today="2026-08-08", now_time="20:00")
    await s._maybe_daily_hot_push(today="2026-08-08", now_time="23:00")
    assert len(rec.sent) == 1


async def test_daily_hot_next_day_pushes_again(make_sched):
    s, client, sub, rec = make_sched()
    await s._maybe_daily_hot_push(today="2026-08-08", now_time="20:00")
    await s._maybe_daily_hot_push(today="2026-08-09", now_time="20:00")
    assert len(rec.sent) == 2


async def test_daily_hot_disabled(make_sched):
    s, client, sub, rec = make_sched(**{"hot_push.daily.enable": False})
    await s._maybe_daily_hot_push(today="2026-08-08", now_time="20:00")
    assert rec.sent == []


# ---- 增量雷达 ----

async def test_incr_first_run_baseline_only(make_sched):
    s, client, sub, rec = make_sched()
    await s._maybe_incr_hot_push(today="2026-08-08")
    assert rec.sent == []
    assert sub.saved_incr[-1].date == "2026-08-08"
    assert sub.saved_incr[-1].last_tids == [1, 2]


async def test_incr_new_thread_pushed(make_sched):
    s, client, sub, rec = make_sched()
    await s._maybe_incr_hot_push(today="2026-08-08")
    client.hot_items = [HotItem(tid=1, title="A"), HotItem(tid=3, title="C", reply_count=5)]
    await s._maybe_incr_hot_push(today="2026-08-08")
    assert len(rec.sent) == 1
    assert "新上榜" in rec.sent[0][1]
    assert "C" in rec.sent[0][1]


async def test_incr_no_new_thread_no_push(make_sched):
    s, client, sub, rec = make_sched()
    await s._maybe_incr_hot_push(today="2026-08-08")
    await s._maybe_incr_hot_push(today="2026-08-08")
    assert rec.sent == []


async def test_incr_same_day_dedup(make_sched):
    s, client, sub, rec = make_sched()
    await s._maybe_incr_hot_push(today="2026-08-08")
    client.hot_items = [HotItem(tid=1, title="A"), HotItem(tid=3, title="C")]
    await s._maybe_incr_hot_push(today="2026-08-08")
    assert len(rec.sent) == 1
    client.hot_items = [HotItem(tid=1, title="A")]
    await s._maybe_incr_hot_push(today="2026-08-08")
    client.hot_items = [HotItem(tid=1, title="A"), HotItem(tid=3, title="C")]
    await s._maybe_incr_hot_push(today="2026-08-08")
    assert len(rec.sent) == 1


async def test_incr_next_day_resets(make_sched):
    s, client, sub, rec = make_sched()
    await s._maybe_incr_hot_push(today="2026-08-08")
    client.hot_items = [HotItem(tid=1, title="A"), HotItem(tid=3, title="C")]
    await s._maybe_incr_hot_push(today="2026-08-08")
    assert len(rec.sent) == 1
    # 跨天后 3 未掉榜（昨日最后在榜）→ 不重推；9 新进榜 → 推
    client.hot_items = [HotItem(tid=3, title="C"), HotItem(tid=9, title="I")]
    await s._maybe_incr_hot_push(today="2026-08-09")
    assert len(rec.sent) == 2
    assert "I" in rec.sent[-1][1]
    assert "C" not in rec.sent[-1][1]


async def test_incr_recovers_state_from_kv(make_sched):
    s, client, sub, rec = make_sched()

    async def recover():
        return IncrState(date="2026-08-08", pushed_tids=[1], last_tids=[1, 2])

    sub.get_hot_incr_state = recover
    await s._recover_hot_states()
    assert s._hot_incr_state is not None and s._hot_incr_state.pushed_tids == [1]
    client.hot_items = [HotItem(tid=1, title="A"), HotItem(tid=2, title="B"), HotItem(tid=5, title="E")]
    await s._maybe_incr_hot_push(today="2026-08-08")
    assert len(rec.sent) == 1  # 只推 5，不重推 1/2
    assert "E" in rec.sent[0][1]


async def test_incr_disabled(make_sched):
    s, client, sub, rec = make_sched(**{"hot_push.incremental.enable": False})
    await s._maybe_incr_hot_push(today="2026-08-08")
    assert rec.sent == []


# ---- 调度等待 ----

async def test_sleep_until_uses_next_update(make_sched):
    s, client, sub, rec = make_sched()
    sleeps: list[float] = []

    async def fake_sleep(seconds):
        sleeps.append(seconds)

    s._sleep = fake_sleep
    from datetime import timedelta

    from yamibo.scheduler import _now

    n = _now()
    await s._sleep_until(n + timedelta(hours=2))
    assert 2 * 3600 + RANK_UPDATE_GRACE - 10 <= sleeps[-1] <= 2 * 3600 + RANK_UPDATE_GRACE + 10


async def test_sleep_until_fallback_on_no_time(make_sched):
    s, client, sub, rec = make_sched()
    sleeps: list[float] = []

    async def fake_sleep(seconds):
        sleeps.append(seconds)

    s._sleep = fake_sleep
    await s._sleep_until(None)
    assert sleeps[-1] == 60 * 60
