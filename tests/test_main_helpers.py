from yamibo.utils import cfg_get, cooldown_ok, parse_tid_input


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
