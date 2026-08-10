from datetime import datetime
from pathlib import Path

from yamibo.models import HotItem, ThreadSummary
from yamibo.parser import TZ
from yamibo.utils import (
    FORUM_ALIASES,
    FORUM_NAMES,
    AsyncLockRegistry,
    build_push_chain,
    cfg_get,
    clamp_int,
    cooldown_ok,
    fmt_comic_header,
    fmt_list,
    fmt_time,
    normalize_deliver_mode,
    parse_tid_input,
    resolve_comic_workdir,
    resolve_fid,
    truncate,
)


class _StubImage:
    @classmethod
    def fromURL(cls, url):
        return ("img", url)


class _StubComp:
    @staticmethod
    def Plain(text):
        return ("plain", text)

    Image = _StubImage


def test_parse_tid_input():
    assert parse_tid_input("574233") == 574233
    assert parse_tid_input("https://bbs.yamibo.com/thread-574233-1-1.html") == 574233
    assert parse_tid_input("forum.php?mod=viewthread&tid=574233") == 574233
    assert parse_tid_input("https://evil.com/thread-1-1.html") is None
    assert parse_tid_input("abc") is None


def test_resolve_fid():
    assert FORUM_NAMES["13"] == "貼圖區"
    assert FORUM_ALIASES["动漫区"] == "5"
    assert resolve_fid("13") == "13"
    assert resolve_fid("999") is None  # 数字但不在已知版块
    assert resolve_fid("贴图区") == "13"
    assert resolve_fid("貼圖區") == "13"
    assert resolve_fid("動漫區") == "5"
    assert resolve_fid(" 动漫 ") == "5"
    assert resolve_fid("") is None
    assert resolve_fid("未知") is None


def test_normalize_deliver_mode():
    assert normalize_deliver_mode("fwd") == "fwd"
    assert normalize_deliver_mode("merge_forward") == "fwd"  # 旧配置值兼容
    assert normalize_deliver_mode("FORWARD") == "fwd"
    assert normalize_deliver_mode("pdf") == "pdf"
    assert normalize_deliver_mode("auto") == "auto"
    assert normalize_deliver_mode("zip") == "zip"
    assert normalize_deliver_mode("") == ""
    assert normalize_deliver_mode(None) == ""


def test_cooldown_ok():
    assert cooldown_ok({}, "key", 60, now=100.0) is True
    assert cooldown_ok({"key": 100.0}, "key", 60, now=150.0) is False
    assert cooldown_ok({"key": 100.0}, "key", 60, now=160.0) is True


def test_cfg_get_nested():
    cfg = {
        "login": {"auth": "abc", "saltkey": "def"},
        "comic": {"max_pages": 300},
        "limits": {},
    }
    assert cfg_get(cfg, "login.auth") == "abc"
    assert cfg_get(cfg, "login.missing", "x") == "x"
    assert cfg_get(cfg, "comic.max_pages") == 300
    assert cfg_get(cfg, "limits.skip_hidden_content", True) is True
    assert cfg_get(cfg, "nope.nope", 1) == 1
    assert cfg_get({}, "a.b.c", None) is None


def test_clamp_int():
    assert clamp_int(5, 1, 10, 7) == 5
    assert clamp_int(0, 1, 10, 7) == 1
    assert clamp_int(99, 1, 10, 7) == 10
    assert clamp_int(None, 1, 10, 7) == 7
    assert clamp_int("abc", 1, 10, 7) == 7
    assert clamp_int("", 1, 10, 7) == 7
    assert clamp_int("8", 1, 10, 7) == 8


def test_build_push_chain():
    chain = build_push_chain("正文", ["u1", "u2"], _StubComp)
    assert chain == [("plain", "正文"), ("img", "u1"), ("img", "u2")]
    assert build_push_chain("正文", [], _StubComp) == [("plain", "正文")]
    assert build_push_chain("正文", None, _StubComp) == [("plain", "正文")]


def test_resolve_comic_workdir():
    default = Path("data") / "plugin_data" / "astrbot_plugin_yamibo"
    assert resolve_comic_workdir({}, default) == default
    cfg = {"comic": {"workdir": "/data/shared/yamibo"}}
    assert resolve_comic_workdir(cfg, default) == Path("/data/shared/yamibo")
    cfg_blank = {"comic": {"workdir": "   "}}
    assert resolve_comic_workdir(cfg_blank, default) == default


def test_truncate():
    assert truncate("一二三四五", 3) == "一二三…"
    assert truncate("abc", 10) == "abc"
    assert truncate("", 5) == ""


def test_fmt_time():
    assert fmt_time("2026-8-7 18:21") == "08-07"
    assert fmt_time("2020-5-11 18:00") == "2020-05-11"
    assert fmt_time("") == ""


def test_fmt_time_uses_bbs_tz_year():
    """当年/跨年判定必须基于东八区年份，而非服务器本地年份。"""
    cur = datetime.now(TZ).strftime("%Y")
    expected = "08-07" if cur == "2026" else "2026-08-07"
    assert fmt_time("2026-8-7 18:21") == expected
    # 同年 → MM-DD；上一年 → YYYY-MM-DD
    assert fmt_time(f"{cur}-1-5 08:00") == "01-05"
    prev = str(int(cur) - 1)
    assert fmt_time(f"{prev}-1-5 08:00") == f"{prev}-01-05"


def test_lock_registry_reuses_same_lock():
    reg = AsyncLockRegistry(max_size=4)
    assert reg.get(1) is reg.get(1)


def test_lock_registry_evicts_unlocked_beyond_cap():
    reg = AsyncLockRegistry(max_size=4)
    for i in range(10):
        reg.get(i)
    assert len(reg._locks) <= 4


async def test_lock_registry_keeps_held_lock():
    reg = AsyncLockRegistry(max_size=2)
    l0 = reg.get(0)
    async with l0:
        for i in range(1, 8):
            reg.get(i)
        # 持有中的锁不会被淘汰（否则同一 tid 会拿到不同的锁绕过互斥）
        assert reg.get(0) is l0
        assert l0.locked()
        assert len(reg._locks) <= 2


def test_fmt_comic_header():
    out = fmt_comic_header("某漫画", 574233)
    assert "【某漫画】" in out
    assert "https://bbs.yamibo.com/thread-574233-1-1.html" in out
    assert out.splitlines()[0] == "【某漫画】"
    # 无标题/纯空白标题时兜底为帖子编号
    assert fmt_comic_header("", 7).startswith("【帖子 7】")
    assert fmt_comic_header("   ", 7).startswith("【帖子 7】")


def test_fmt_list_threads():
    items = [
        ThreadSummary(tid=1, title="短标题", author="甲", last_reply_time="2026-8-7 18:21"),
        ThreadSummary(tid=2, title="标题", author="乙", last_reply_time="2020-5-11 18:00"),
    ]
    out = fmt_list("貼圖區 · 第1页", items)
    assert "【貼圖區 · 第1页】" in out
    assert "1. 短标题 — 甲 (08-07) https://bbs.yamibo.com/thread-1-1-1.html" in out
    assert "2. 标题 — 乙 (2020-05-11) https://bbs.yamibo.com/thread-2-1-1.html" in out


def test_fmt_list_hot():
    items = [HotItem(tid=519989, title="汇总", author="hongyuny", reply_count=138)]
    out = fmt_list("今日热度榜", items, hot=True)
    assert "1. 汇总（热度 138） https://bbs.yamibo.com/thread-519989-1-1.html" in out


def test_fmt_list_limit():
    items = [ThreadSummary(tid=i, title=f"T{i}", author="a", last_reply_time="") for i in range(20)]
    out = fmt_list("X", items, limit=15)
    lines = out.splitlines()
    assert len(lines) == 17  # 标题 + 15 条 + 提示
    assert "仅显示前 15 条" in out
