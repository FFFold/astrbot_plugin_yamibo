from yamibo.models import PostFloor, SignStatus, Subscription, ThreadSummary


def test_thread_summary_fields():
    t = ThreadSummary(tid=574233, title="标题", author="crystar23", last_reply_time="2026-8-7 18:21")
    assert t.tid == 574233
    assert t.title == "标题"


def test_post_floor_floor_ordering():
    f1 = PostFloor(pid=1, floor=1, author_uid=731857, author_name="op", time="2026-1-1", text="", images=[])
    f2 = PostFloor(pid=2, floor=2, author_uid=1, author_name="x", time="2026-1-2", text="", images=[])
    assert f1.floor < f2.floor


def test_subscription_defaults():
    s = Subscription(
        id="s1", tid=574233, title="t", op_uid=1, op_name="op", last_floor=0, last_pid=0,
        only_op=True, subscribers=["umo1"], created_at=0,
    )
    assert s.paused is False
    assert s.fail_count == 0


def test_sign_status():
    s = SignStatus(signed_today=True)
    assert s.signed_today
    assert s.total_days == 0
