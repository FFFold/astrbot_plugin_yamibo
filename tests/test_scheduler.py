import pytest

from yamibo.models import HotItem, SignStatus
from yamibo.scheduler import Scheduler

UMO = "aiocqhttp:group:111"


@pytest.fixture
def make_sched():
    def build(**overrides):
        cfg = {
            "sign_enable": True, "sign_time": "10:00",
            "hot_push_enable": True, "hot_push_interval_min": 60, "hot_push_count": 10,
            "sub_check_interval_min": 30, "sub_text_max_len": 2000, "sub_image_max": 50,
            "notify_auth_fail": False, "skip_hidden_content": True,
        }
        cfg.update(overrides)

        class FakeClient:
            def __init__(self):
                self.signed_today = False
                self.hot_items = [HotItem(tid=1, title="A"), HotItem(tid=2, title="B")]

            async def get_sign_status(self):
                return "<html>", SignStatus(signed_today=self.signed_today)

            async def sign(self):
                self.signed_today = True

            async def get_hot_threads(self, n):
                return self.hot_items

        class FakeSub:
            def __init__(self):
                self.subs = []

            async def all(self):
                return self.subs

            async def update_baseline(self, tid, *, floor, pid):
                pass

            async def bump_fail(self, tid):
                pass

            async def reset_fail(self, tid):
                pass

            async def get_hot_state(self):
                return None

            async def save_hot_state(self, date, tids):
                pass

            async def hot_targets(self):
                return [UMO]

        class Recorder:
            def __init__(self):
                self.sent = []

            async def send(self, umo, text):
                self.sent.append((umo, text))

        client = FakeClient()
        sub = FakeSub()
        rec = Recorder()
        s = Scheduler(client, sub, cfg, rec.send)
        return s, client, sub, rec

    return build


async def test_sign_loop_when_due(make_sched):
    s, client, sub, rec = make_sched()
    await s._maybe_sign(now="2026-08-08 10:00", today="2026-08-08")
    assert client.signed_today is True


async def test_sign_skip_when_not_due(make_sched):
    s, client, sub, rec = make_sched()
    await s._maybe_sign(now="2026-08-08 09:00", today="2026-08-08")
    assert client.signed_today is False


async def test_hot_push_first_run_no_blast(make_sched):
    s, client, sub, rec = make_sched()
    await s._maybe_hot_push(today="2026-08-08")
    assert rec.sent == []
    assert s._hot_state == ("2026-08-08", [1, 2])


async def test_hot_push_second_run_sends(make_sched):
    s, client, sub, rec = make_sched()
    await s._maybe_hot_push(today="2026-08-08")
    client.hot_items = [HotItem(tid=3, title="C", reply_count=5), HotItem(tid=1, title="A")]
    await s._maybe_hot_push(today="2026-08-08")
    assert len(rec.sent) == 1
    assert rec.sent[0][0] == UMO
    assert "C" in rec.sent[0][1]
