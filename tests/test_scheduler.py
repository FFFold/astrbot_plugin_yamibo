import pytest

from yamibo.client import NotLoggedInError
from yamibo.hotpush import IncrState
from yamibo.models import HotItem, SignStatus, Subscription
from yamibo.scheduler import RANK_UPDATE_GRACE, Scheduler, _parse_hhmm, _target_sleep_seconds
from yamibo.utils import cfg_get

UMO = "aiocqhttp:group:111"

SUB_AUTHOR_HTML = """
<div id="postlist">
<div id="post_2002">
<table><tr>
<td><div id="favatar2002" class="pls"><div class="authi"><a href="space-uid-7.html" target="_blank">op</a></div></div></td>
<td><div id="postnum2002"><em>12</em></div>
<div id="authorposton2002"><span>2026-8-9 10:00</span></div>
<div id="postmessage_2002" class="t_f">楼主新楼层
<img src="data/attachment/forum/202608/09/a.jpg" zoomfile="data/attachment/forum/202608/09/a.jpg" class="zoom">
</div></td></tr></table>
</div>
</div>
"""

SUB_AUTHOR_TWO_IMAGES = SUB_AUTHOR_HTML.replace(
    '<img src="data/attachment/forum/202608/09/a.jpg" zoomfile="data/attachment/forum/202608/09/a.jpg" class="zoom">',
    '<img src="data/attachment/forum/202608/09/a.jpg" zoomfile="data/attachment/forum/202608/09/a.jpg" class="zoom">'
    '<img src="data/attachment/forum/202608/09/b.jpg" zoomfile="data/attachment/forum/202608/09/b.jpg" class="zoom">',
)


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
                self.sub_author_html = SUB_AUTHOR_HTML

            async def get_sign_status(self):
                return "<html>", SignStatus(signed_today=self.signed_today)

            async def sign(self):
                self.signed_today = True

            async def get_hot_rank(self, n):
                return self.hot_items[:n], self.next_update

            async def get_hot_threads(self, n):
                return self.hot_items[:n]

            async def get_thread_author_view(self, tid, author_uid):
                return self.sub_author_html

        class FakeSub:
            def __init__(self):
                self.subs = []
                self.saved_incr = []
                self.saved_daily = []
                self.baseline_updates = []
                self.resets = []
                self.bumps = []

            async def all(self):
                return self.subs

            async def update_baseline(self, tid, *, floor, pid):
                self.baseline_updates.append((tid, floor, pid))

            async def bump_fail(self, tid):
                self.bumps.append(tid)

            async def reset_fail(self, tid):
                self.resets.append(tid)

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

            async def send(self, umo, text, images=None):
                self.sent.append((umo, text, images or []))

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


async def test_daily_hot_master_switch(make_sched):
    s, client, sub, rec = make_sched(**{"hot_push.enable": False})
    await s._maybe_daily_hot_push(today="2026-08-08", now_time="20:00")
    assert rec.sent == []


async def test_daily_hot_unpadded_time(make_sched):
    s, client, sub, rec = make_sched(**{"hot_push.daily.time": "8:00"})
    await s._maybe_daily_hot_push(today="2026-08-08", now_time="08:30")
    assert len(rec.sent) == 1


async def test_daily_hot_unpadded_now_time(make_sched):
    # now_time 非零填充（如 "9:00"）不崩溃，按 9:00 判断
    s, client, sub, rec = make_sched(**{"hot_push.daily.time": "10:00"})
    await s._maybe_daily_hot_push(today="2026-08-08", now_time="9:00")
    assert rec.sent == []
    await s._maybe_daily_hot_push(today="2026-08-08", now_time="9:30")
    assert len(rec.sent) == 0  # 仍早于 10:00
    await s._maybe_daily_hot_push(today="2026-08-08", now_time="10:01")
    assert len(rec.sent) == 1


async def test_daily_hot_empty_list_not_marked_done(make_sched):
    s, client, sub, rec = make_sched()
    client.hot_items = []
    retry_in = await s._maybe_daily_hot_push(today="2026-08-08", now_time="20:00")
    assert retry_in == 60 * 60
    assert rec.sent == []
    assert sub.saved_daily == []
    assert s._hot_daily_date is None


async def test_daily_hot_network_error_retryable(make_sched):
    s, client, sub, rec = make_sched()

    async def boom(n):
        raise RuntimeError("network")

    client.get_hot_threads = boom
    retry_in = await s._maybe_daily_hot_push(today="2026-08-08", now_time="20:00")
    assert retry_in == 15 * 60
    assert s._hot_daily_date is None
    # 恢复后重试成功
    async def ok_fetch(n):
        return client.hot_items[:n]

    client.get_hot_threads = ok_fetch
    retry_in = await s._maybe_daily_hot_push(today="2026-08-08", now_time="20:00")
    assert retry_in is None
    assert len(rec.sent) == 1


async def test_daily_hot_send_failure_not_marked_done(make_sched):
    s, client, sub, rec = make_sched()

    async def bad_send(umo, text):
        raise RuntimeError("send failed")

    s._send = bad_send
    retry_in = await s._maybe_daily_hot_push(today="2026-08-08", now_time="20:00")
    assert retry_in == 15 * 60
    assert s._hot_daily_date is None
    assert sub.saved_daily == []
    # 恢复后重试成功
    s._send = rec.send
    retry_in = await s._maybe_daily_hot_push(today="2026-08-08", now_time="20:00")
    assert retry_in is None
    assert len(rec.sent) == 1


async def test_daily_hot_no_targets_marked_done(make_sched):
    s, client, sub, rec = make_sched()

    async def no_targets():
        return []

    sub.hot_targets = no_targets
    retry_in = await s._maybe_daily_hot_push(today="2026-08-08", now_time="20:00")
    assert retry_in is None
    assert s._hot_daily_date == "2026-08-08"
    assert sub.saved_daily == ["2026-08-08"]
    assert rec.sent == []


async def test_daily_hot_recovers_state_from_kv(make_sched):
    s, client, sub, rec = make_sched()

    async def recover():
        return "2026-08-08"

    sub.get_hot_daily_state = recover
    await s._recover_hot_states()
    assert s._hot_daily_date == "2026-08-08"
    # 20:00 后重启恢复：同天不再重复推送
    retry_in = await s._maybe_daily_hot_push(today="2026-08-08", now_time="21:00")
    assert retry_in is None
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


async def test_incr_push_failure_not_marked_pushed(make_sched):
    s, client, sub, rec = make_sched()
    await s._maybe_incr_hot_push(today="2026-08-08")  # 基线 [1,2]
    client.hot_items = [HotItem(tid=1, title="A"), HotItem(tid=3, title="C")]

    async def bad_send(umo, text):
        raise RuntimeError("send failed")

    s._send = bad_send
    retry_in = await s._maybe_incr_hot_push(today="2026-08-08")
    assert retry_in == 60 * 60  # 短间隔重试（interval_min 兜底），而非等下次榜单刷新
    # 未送达任何会话 → 不保存已推状态
    assert sub.saved_incr[-1].pushed_tids == []
    # 恢复正常后重试，3 仍被推送
    s._send = rec.send
    await s._maybe_incr_hot_push(today="2026-08-08")
    assert len(rec.sent) == 1
    assert "C" in rec.sent[0][1]


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


async def test_incr_master_switch(make_sched):
    s, client, sub, rec = make_sched(**{"hot_push.enable": False})
    await s._maybe_incr_hot_push(today="2026-08-08")
    assert rec.sent == []


async def test_hot_count_clamped(make_sched):
    s, client, sub, rec = make_sched(**{"hot_push.count": 999})
    fetched: dict = {}

    async def record(n):
        fetched["n"] = n
        return client.hot_items, None

    s._client.get_hot_rank = record
    await s._maybe_incr_hot_push(today="2026-08-08")
    assert fetched["n"] == 30


async def test_hot_count_zero_clamped(make_sched):
    s, client, sub, rec = make_sched(**{"hot_push.count": 0})
    fetched: dict = {}

    async def record(n):
        fetched["n"] = n
        return client.hot_items, None

    s._client.get_hot_rank = record
    await s._maybe_incr_hot_push(today="2026-08-08")
    assert fetched["n"] == 1


async def test_auth_fail_notify_deduped(make_sched):
    s, client, sub, rec = make_sched(**{"limits.notify_auth_fail": True})
    await s._notify_auth_fail()
    await s._notify_auth_fail()
    await s._notify_auth_fail()
    assert len(rec.sent) == 1
    assert "cookie" in rec.sent[0][1]


async def test_auth_fail_notify_disabled(make_sched):
    s, client, sub, rec = make_sched(**{"limits.notify_auth_fail": False})
    await s._notify_auth_fail()
    assert rec.sent == []


# ---- 订阅轮询 ----

def _sub(tid: int = 574233, last_floor: int = 3, subscribers=None) -> Subscription:
    return Subscription(
        id="s1", tid=tid, title="T", op_uid=7, op_name="op",
        last_floor=last_floor, last_pid=5, only_op=True,
        subscribers=subscribers or [UMO],
    )


async def test_sub_check_sends_text_and_images(make_sched):
    s, client, sub, rec = make_sched()
    await s._check_one(_sub())
    assert len(rec.sent) == 1
    umo, text, images = rec.sent[0]
    assert umo == UMO
    assert "【T】op 更新 L12" in text
    assert "楼主新楼层" in text
    assert "https://bbs.yamibo.com/thread-574233-1-1.html" in text
    assert "含图片 1 张" in text
    assert images == ["https://bbs.yamibo.com/data/attachment/forum/202608/09/a.jpg"]
    assert sub.baseline_updates == [(574233, 12, 2002)]
    assert sub.resets == [574233]


async def test_sub_check_image_max_caps_sent_images(make_sched):
    s, client, sub, rec = make_sched(**{"subscription.image_max": 1})
    client.sub_author_html = SUB_AUTHOR_TWO_IMAGES
    await s._check_one(_sub())
    assert len(rec.sent) == 1
    umo, text, images = rec.sent[0]
    assert len(images) == 1
    assert "含图片 1 张" in text


async def test_sub_check_no_new_floors_no_send(make_sched):
    s, client, sub, rec = make_sched()
    await s._check_one(_sub(last_floor=12))
    assert rec.sent == []
    assert sub.resets == [574233]
    assert sub.baseline_updates == []


async def test_sub_check_all_send_failures_keep_baseline(make_sched, caplog):
    s, client, sub, rec = make_sched()

    async def bad_send(umo, text, images=None):
        raise RuntimeError("boom")

    s._send = bad_send
    with caplog.at_level("WARNING"):
        await s._check_one(_sub())
    assert rec.sent == []
    assert sub.baseline_updates == []
    assert sub.resets == []
    assert "boom" in caplog.text


async def test_sub_check_partial_delivery_advances_baseline(make_sched):
    s, client, sub, rec = make_sched()

    async def bad_send(umo, text, images=None):
        if umo == "telegram:chat:999":
            raise RuntimeError("boom")
        await rec.send(umo, text, images)

    s._send = bad_send
    sub_model = _sub(subscribers=[UMO, "telegram:chat:999"])
    await s._check_one(sub_model)
    assert len(rec.sent) == 1  # 一个会话送达即算送达
    assert sub.baseline_updates == [(574233, 12, 2002)]
    assert sub.resets == [574233]


async def test_maybe_check_subs_continue_after_auth_fail(make_sched):
    s, client, sub, rec = make_sched()
    sub.subs = [_sub(tid=1), _sub(tid=2)]
    checked = []

    async def fake_check(model):
        checked.append(model.tid)
        if model.tid == 1:
            raise NotLoggedInError("cookie 失效")

    s._check_one = fake_check
    await s._maybe_check_subs()
    assert checked == [1, 2]  # 第 1 个订阅 cookie 失效不再中断本轮其余订阅


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


# ---- 定时目标计算 ----

def _t(h, m, s=0):
    from datetime import datetime, timedelta, timezone

    return datetime(2026, 8, 8, h, m, s, tzinfo=timezone(timedelta(hours=8)))


async def test_parse_hhmm():
    assert _parse_hhmm("20:00") == (20, 0)
    assert _parse_hhmm("8:00") == (8, 0)
    assert _parse_hhmm("24:00") is None
    assert _parse_hhmm("20:00:00") is None
    assert _parse_hhmm("abc") is None


async def test_target_sleep_seconds_same_day():
    # 19:00:30 启动、target 20:00 → 当天 20:00（3600 - 30 秒）
    sec = _target_sleep_seconds(_t(19, 0, 30), "20:00")
    assert sec == 3600 - 30


async def test_target_sleep_seconds_next_day():
    # 21:00 启动、target 20:00 → 明天 20:00（23h）
    sec = _target_sleep_seconds(_t(21, 0), "20:00")
    assert sec == 23 * 3600


async def test_target_sleep_seconds_unpadded():
    # 07:30 启动、target "8:00"（非零填充）→ 30 分钟
    sec = _target_sleep_seconds(_t(7, 30), "8:00")
    assert sec == 1800


async def test_target_sleep_seconds_malformed():
    assert _target_sleep_seconds(_t(10, 0), "20:00:00") is None
