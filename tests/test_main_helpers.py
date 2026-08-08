from pathlib import Path

from yamibo.models import HotItem, ThreadSummary
from yamibo.utils import (
    cfg_get,
    cooldown_ok,
    fmt_list,
    fmt_time,
    parse_tid_input,
    resolve_comic_workdir,
    truncate,
)


def test_parse_tid_input():
    assert parse_tid_input("574233") == 574233
    assert parse_tid_input("https://bbs.yamibo.com/thread-574233-1-1.html") == 574233
    assert parse_tid_input("forum.php?mod=viewthread&tid=574233") == 574233
    assert parse_tid_input("https://evil.com/thread-1-1.html") is None
    assert parse_tid_input("abc") is None


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
