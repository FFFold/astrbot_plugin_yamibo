from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

from yamibo.models import HotItem, PostFloor, ThreadSummary
from yamibo.parser import TZ
from yamibo.utils import (
    FORUM_ALIASES,
    FORUM_NAMES,
    AsyncLockRegistry,
    build_push_chain,
    build_sub_payload,
    cfg_get,
    clamp_int,
    cooldown_ok,
    fmt_comic_header,
    fmt_floor_range,
    fmt_list,
    fmt_sub_floor_text,
    fmt_sub_notice,
    fmt_time,
    is_aiocqhttp_target,
    normalize_deliver_mode,
    parse_tid_input,
    resolve_comic_workdir,
    resolve_fid,
    split_plain_batches,
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
    cur = datetime.now(TZ).strftime("%Y")
    assert fmt_time("2026-8-7 18:21") == ("08-07" if cur == "2026" else "2026-08-07")
    prev = str(int(cur) - 1)
    assert fmt_time(f"{prev}-5-11 18:00") == f"{prev}-05-11"
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


async def test_lock_registry_new_key_never_orphaned():
    """容量满且全部锁被持有时，新 key 的锁不能被淘汰成孤儿（否则并发拿不到同一把锁）。"""
    reg = AsyncLockRegistry(max_size=2)
    l0, l1 = reg.get(0), reg.get(1)
    async with l0, l1:
        l2a = reg.get(2)
        l2b = reg.get(2)
        assert l2a is l2b  # 同一 key 必须始终返回同一把锁
        assert reg._locks[2] is l2a
        assert len(reg._locks) == 3  # 全部持有中时允许临时超出容量


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


# ---- 订阅推送组装 ----

def _floor(floor: int, *, text: str = "内容", images: list[str] | None = None, pid: int) -> PostFloor:
    return PostFloor(pid=pid, floor=floor, author_uid=7, author_name="op", time="", text=text, images=images or [])


def test_fmt_floor_range():
    assert fmt_floor_range([12]) == "L12"
    assert fmt_floor_range([12, 13]) == "L12-L13"
    assert fmt_floor_range([12, 14]) == "L12、L14"
    assert fmt_floor_range([12, 13, 15, 16, 17]) == "L12-L13、L15-L17"
    assert fmt_floor_range([12, 12]) == "L12"  # 去重
    assert fmt_floor_range([]) == ""


def test_fmt_sub_floor_text():
    out = fmt_sub_floor_text("T", "op", 12, "楼主新楼层", "https://bbs.yamibo.com/thread-574233-1-1.html")
    assert out == (
        "【T】op 更新 L12\n"
        "楼主新楼层\n"
        "https://bbs.yamibo.com/thread-574233-1-1.html"
    )


def test_fmt_sub_floor_text_empty_body():
    out = fmt_sub_floor_text("T", "op", 12, "   ", "https://bbs.yamibo.com/thread-574233-1-1.html")
    assert "(无文本)" in out


def test_fmt_sub_notice():
    assert fmt_sub_notice("T", "op", [12]) == "【T】op 更新了 L12"
    assert fmt_sub_notice("T", "op", [12, 13, 15]) == "【T】op 更新了 L12-L13、L15"


def test_build_sub_payload_forward():
    url = "https://bbs.yamibo.com/thread-574233-1-1.html"
    p = build_sub_payload("T", "op", 574233, [_floor(12, pid=2002)], mode="forward", text_max=2000, image_max=50)
    assert p.mode == "forward"
    assert len(p.items) == 1
    it = p.items[0]
    assert it.floor == 12
    assert it.name == "op"
    assert it.text == f"【T】op 更新 L12\n内容\n{url}"
    assert it.image_urls == []


def test_build_sub_payload_plain_same_shape():
    url = "https://bbs.yamibo.com/thread-574233-1-1.html"
    floors = [_floor(12, pid=2002), _floor(13, text="第二层", images=["u1"], pid=2003)]
    p = build_sub_payload("T", "op", 574233, floors, mode="plain", text_max=2000, image_max=50)
    assert p.mode == "plain"
    assert len(p.items) == 2
    assert p.items[1].text == f"【T】op 更新 L13\n第二层\n{url}"
    assert p.items[1].image_urls == ["u1"]


def test_build_sub_payload_applies_text_max():
    p = build_sub_payload(
        "T", "op", 574233, [_floor(12, text="x" * 500, pid=2002)], mode="forward", text_max=100, image_max=50
    )
    assert len(p.items[0].text.splitlines()[1]) == 100


def test_build_sub_payload_applies_image_max():
    p = build_sub_payload(
        "T", "op", 574233,
        [_floor(12, images=["a", "b", "c"], pid=2002)],
        mode="forward", text_max=2000, image_max=1,
    )
    assert p.items[0].image_urls == ["a"]


def test_split_plain_batches_merges_within_budget():
    """短楼层合并进同批；累计超出预算时开新批。"""
    floors = [_floor(12, text="x" * 500, pid=2002), _floor(13, text="y" * 500, pid=2003)
              ] + [_floor(14, text="z" * 500, pid=2004), _floor(15, text="w" * 500, pid=2005)]
    batches = split_plain_batches("T", "op", 574233, floors, text_max=2000, budget=1500)
    assert [len(b) for b in batches] == [2, 2]
    assert batches[0][0].floor == 12 and batches[0][1].floor == 13
    assert batches[1][0].floor == 14


def test_split_plain_batches_lonely_floor_exceeding_budget():
    """单层文本超预算：自成一批，不阻塞后续楼层。"""
    floors = [_floor(12, text="a" * 300, pid=2002), _floor(13, text="b" * 300, pid=2003)]
    batches = split_plain_batches("T", "op", 574233, floors, text_max=2000, budget=350)
    assert [len(b) for b in batches] == [1, 1]


def test_split_plain_batches_budget_by_text_max():
    """floors 的合并成本按 text_max 截断后的文本计。"""
    floors = [_floor(12, text="a" * 9999, pid=2002), _floor(13, text="b" * 9999, pid=2003)]
    batches = split_plain_batches("T", "op", 574233, floors, text_max=600, budget=1200)
    assert [len(b) for b in batches] == [1, 1]


def test_is_aiocqhttp_target():
    class _FakePlatform:
        def __init__(self, name):
            self._name = name

        def meta(self):
            return SimpleNamespace(name=self._name)

    class _FakeContext:
        def __init__(self, plats):
            self._plats = plats

        def get_platform_inst(self, pid):
            return self._plats.get(pid)

    ctx = _FakeContext({"aiocqhttp": _FakePlatform("aiocqhttp"), "tg": _FakePlatform("telegram")})
    assert is_aiocqhttp_target(ctx, "aiocqhttp:group:111") is True
    assert is_aiocqhttp_target(ctx, "telegram:chat:999") is False
    assert is_aiocqhttp_target(ctx, "nope:private:1") is False
    assert is_aiocqhttp_target(None, "aiocqhttp:group:111") is False
