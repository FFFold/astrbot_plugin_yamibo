from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from yamibo.utils import (
    AutoLinkPreview,
    auto_parse_group_links,
    build_auto_preview,
    extract_yamibo_tids,
    is_comic_thread,
    should_auto_parse,
)

URL = "https://bbs.yamibo.com/thread-123-1-1.html"


@pytest.mark.parametrize("url", [
    URL, "HTTP://BBS.YAMIBO.COM/thread-123-2-1.html#pid100",
    "https://bbs.yamibo.com/forum.php?mobile=2&tid=123&mod=viewthread&page=2",
    "https://bbs.yamibo.com/forum.php?mod=viewthread&amp;tid=123",
])
def test_supported_links_in_prose(url):
    assert extract_yamibo_tids(f"推荐：（{url}）。") == [123]
    assert extract_yamibo_tids(f"[链接]({url})") == [123]


@pytest.mark.parametrize("url", [
    "123", "thread-123-1-1.html", "forum.php?mod=viewthread&tid=123",
    "https://bbs.yamibo.com.evil.com/thread-123-1-1.html",
    "https://evil.com/bbs.yamibo.com/thread-123-1-1.html",
    "https://bbs.yamibo.com@evil.com/thread-123-1-1.html",
    "https://evil.com@bbs.yamibo.com/thread-123-1-1.html",
    "https://bbs.yamibo.com:bad/thread-123-1-1.html",
    "https://bbs.yamibo.com/forum.php?mod=forumdisplay&tid=123",
    "https://bbs.yamibo.com/forum.php?mod=viewthread&tid=123bad",
    "https://bbs.yamibo.com/forum.php?mod=viewthread&tid=123&tid=456",
    "https://bbs.yamibo.com/thread-0-1-1.html",
    "https://bbs.yamibo.com/thread-123-1-1.html.evil",
])
def test_non_thread_links_ignored(url):
    assert extract_yamibo_tids(url) == []


def test_deduplicate_by_tid_in_order():
    text = f"{URL}\nhttps://bbs.yamibo.com/forum.php?mod=viewthread&tid=123\n{URL.replace('123', '456')}"
    assert extract_yamibo_tids(text) == [123, 456]


@pytest.mark.parametrize("suffix", ["好看", "」", "”", "：", "…", "👍"])
def test_link_stops_before_cjk_and_fullwidth_punctuation(suffix):
    # 中文用户常不打空格，或把链接包在「」/“” 里；URL 后面紧跟非 ASCII 字符不能吞掉链接。
    assert extract_yamibo_tids(f"看这个{URL}{suffix}") == [123]
    assert extract_yamibo_tids(f"「{URL}」") == [123]


def test_classification_uses_navigation_only():
    assert is_comic_thread('<div id="pt"><a href="forum-30-1.html">中文百合漫画区</a></div>')
    assert is_comic_thread('<div id="pt"><a href="forum-31-1.html">漫畫區</a></div>')
    assert not is_comic_thread('<div id="pt"><a href="forum-49-1.html">文學區</a></div><p>漫画</p><img src="cover.jpg">')
    assert not is_comic_thread('<a href="forum-30-1.html">漫画区</a>')
    assert not is_comic_thread("登录已失效")


def event(text=URL, group="42", sender="2", bot="1", origin="qq:GroupMessage:42", commands=None):
    return SimpleNamespace(
        get_group_id=lambda: group, get_sender_id=lambda: sender, get_self_id=lambda: bot,
        get_message_str=lambda: text, unified_msg_origin=origin,
        get_extra=lambda key, default=None: commands or default,
    )


def page(*, comic=False, title="测试帖子", body="小说正文", extra="", floor=1):
    name = "中文百合漫画区" if comic else "文學區"
    return f"""
    <div id="pt"><a href="forum-30-1.html">{name}</a>
    <a href="thread-123-1-1.html">{title}</a></div>
    <span id="thread_subject">{title}</span>
    <div id="postlist"><div id="post_100">
    <div id="postnum100"><em>{floor}</em></div>
    <div id="postmessage_100">{body}</div>
    </div>{extra}</div>
    """


def forum_client(html=None):
    return SimpleNamespace(get_text=AsyncMock(return_value=html or page()), is_logged_in=lambda html: True)


async def collect(ev, config, state, client):
    return [r async for r in auto_parse_group_links(ev, config, state, client)]


@pytest.mark.parametrize("ev,config", [
    (event(), {}), (event(), {"auto_parse": {"enable": False}}),
    (event(group=""), {"auto_parse": {"enable": True}}),
    (event(sender="1"), {"auto_parse": {"enable": True}}),
    (event(text=f"/yamibo 漫画 {URL} pdf"), {"auto_parse": {"enable": True}}),
    (event(text=f"yamibo 订阅 {URL}"), {"auto_parse": {"enable": True}}),
    (event(commands={"other.command": {"url": URL}}), {"auto_parse": {"enable": True}}),
    (event(text="普通聊天"), {"auto_parse": {"enable": True}}),
    (event(), {"auto_parse": {"enable": True, "group_whitelist": ["43"]}}),
    (event(), {"auto_parse": {"enable": True, "group_whitelist": "42"}}),
])
async def test_guard_does_not_fetch_or_consume_cooldown(ev, config):
    assert not should_auto_parse(ev, config)
    client = forum_client()
    state = {}
    assert await collect(ev, config, state, client) == []
    client.get_text.assert_not_awaited()
    assert state == {}


def test_filter_observes_config_toggle_without_side_effects():
    config = {"auto_parse": {"enable": False}}
    assert not should_auto_parse(event(), config)
    config["auto_parse"]["enable"] = True
    assert should_auto_parse(event(), config)
    config["auto_parse"]["enable"] = False
    assert not should_auto_parse(event(), config)


@pytest.mark.parametrize("whitelist", [[], ["42"], [42], [" 42 ", "43"]])
def test_whitelist_matches_group_id(whitelist):
    config = {"auto_parse": {"enable": True, "group_whitelist": whitelist}}
    assert should_auto_parse(event(), config)


def test_whitelist_changes_take_effect_without_reload():
    config = {"auto_parse": {"enable": True, "group_whitelist": ["42"]}}
    assert should_auto_parse(event(), config)
    config["auto_parse"]["group_whitelist"] = ["43"]
    assert not should_auto_parse(event(), config)


async def test_no_client_is_silent():
    assert await collect(event(), {"auto_parse": {"enable": True}}, {}, None) == []


async def test_fetches_once_and_limits_links():
    client = forum_client(page(comic=True, body='<img src="https://img.test/first.jpg">'))
    config = {"auto_parse": {"enable": True}}
    ev = event(text=f"{URL} {URL.replace('123', '456')}")
    assert await collect(ev, config, {}, client) == [AutoLinkPreview("【测试帖子】", ["https://img.test/first.jpg"])]
    client.get_text.assert_awaited_once_with("/forum.php?mod=viewthread&tid=123")


async def test_first_unique_comic_ignores_manual_cooldown_and_removed_settings():
    client = forum_client()
    client.get_text.side_effect = [page(comic=True, title="漫画一"), page(comic=True, title="漫画二")]
    config = {"auto_parse": {"enable": True, "max_links": 3}, "limits": {"comic_cooldown_sec": 9999}}
    state = {"comic:qq:GroupMessage:42": 10**20}
    ev = event(text=f"{URL} {URL} {URL.replace('123', '456')}")
    assert await collect(ev, config, state, client) == [AutoLinkPreview("【漫画一】")]
    assert client.get_text.await_count == 1
    assert state["comic:qq:GroupMessage:42"] == 10**20


async def test_cooldown_shared_by_group_but_separate_platform_and_group():
    client = forum_client()
    config = {"auto_parse": {"enable": True}}
    state = {}
    expected = [AutoLinkPreview("【测试帖子】\n小说正文")]
    assert await collect(event(), config, state, client) == expected
    assert await collect(event(origin="qq:GroupMessage:3_42"), config, state, client) == []
    assert await collect(event(group="43"), config, state, client) == expected
    assert await collect(event(origin="qq2:GroupMessage:42"), config, state, client) == expected


async def test_fixed_cooldown_ignores_removed_setting(monkeypatch):
    clock = Mock(return_value=100)
    monkeypatch.setattr("yamibo.utils.time.monotonic", clock)
    client = forum_client()
    config = {"auto_parse": {"enable": True, "cooldown_sec": 0}}
    state = {}
    assert await collect(event(), config, state, client)
    clock.return_value = 159
    assert await collect(event(), config, state, client) == []
    clock.return_value = 160
    assert await collect(event(), config, state, client)
    assert client.get_text.await_count == 2


@pytest.mark.parametrize("failure", ["fetch", "login_check", "parse"])
async def test_failure_is_silent_and_logs_only_exception_type(failure, monkeypatch, caplog):
    client = forum_client()
    error = RuntimeError("private auth/saltkey values and raw page")
    if failure == "fetch":
        client.get_text.side_effect = error
    elif failure == "login_check":
        client.is_logged_in = Mock(side_effect=error)
    else:
        monkeypatch.setattr("yamibo.utils.build_auto_preview", Mock(side_effect=error))
    assert await collect(event(), {"auto_parse": {"enable": True}}, {}, client) == []
    assert "tid=123" in caplog.text and "RuntimeError" in caplog.text
    assert str(error) not in caplog.text


@pytest.mark.parametrize("nav", [
    '<a href="forum-30-1.html">漫画区</a>',
    '<a href="/forum.php?mod=forumdisplay&amp;fid=30">漫畫區</a>',
    '<a href="https://bbs.yamibo.com/forum-30-1.html">漫画区</a><a href="forum-31-1.html">汉化作品</a>',
])
def test_classification_forum_links(nav):
    assert is_comic_thread(f'<div id="pt">{nav}</div>')


@pytest.mark.parametrize("link", [
    '<a href="thread-123-1-1.html">漫画改编的小说</a>',
    '<a href="forum.php?mod=viewthread&amp;tid=123">漫画改编的小说</a>',
    '<a href="https://evil.test/forum-30-1.html">漫画区</a>',
    '<a href="forum.php?mod=forumdisplay">漫画区</a>',
    '<a href="forum.php?mod=forumdisplay&amp;fid=bad">漫画区</a>',
])
def test_classification_excludes_titles_and_non_forum_links(link):
    assert not is_comic_thread(f'<div id="pt"><a href="forum-49-1.html">文学区</a>{link}</div>')


def test_comic_first_image_only_and_no_body():
    html = page(comic=True, body='正文<img src="https://img.test/1.jpg"><img src="https://img.test/2.jpg">')
    assert build_auto_preview(html, 123, {}) == AutoLinkPreview("【测试帖子】", ["https://img.test/1.jpg"])


def test_novel_first_50_characters_no_images_or_author():
    body = "小说正文" * 30
    html = page(title="漫画改编的小说", body=f'{body}<img src="https://img.test/cover.jpg">')
    assert build_auto_preview(html, 123, {}) == AutoLinkPreview(f"【漫画改编的小说】\n{body[:50]}…")


@pytest.mark.parametrize("length,expected", [(1, "甲…"), (3, "甲 乙…"), (0, ""), ("0", ""), (50, "甲 乙 丙"), ("bad", "甲 乙 丙")])
def test_novel_own_length_and_whitespace(length, expected):
    config = {"auto_parse": {"novel": {"text_max_len": length}}}
    result = build_auto_preview(page(body=" 甲\n\n 乙 <br> 丙 "), 123, config)
    assert result == AutoLinkPreview("【测试帖子】" + (f"\n{expected}" if expected else ""))


@pytest.mark.parametrize("comic", [True, False])
def test_ignores_manual_and_subscription_config(comic):
    html = page(comic=comic, body='正文<img src="https://img.test/1.jpg">')
    config = {
        "comic": {"deliver_mode": "zip", "max_pages": 0, "max_file_size_mb": 0},
        "subscription": {"text_max_len": 1, "image_max": 0},
        "limits": {"comic_cooldown_sec": 9999, "skip_hidden_content": False},
    }
    assert build_auto_preview(html, 123, config) == build_auto_preview(html, 123, {})


@pytest.mark.parametrize("comic", [True, False])
def test_empty_content_title_only(comic):
    assert build_auto_preview(page(comic=comic, body=" \n "), 123, {}) == AutoLinkPreview("【测试帖子】")


def test_missing_title_fallback_and_inaccessible_page(caplog):
    assert build_auto_preview(page(title="", body=""), 123, {}) == AutoLinkPreview("【帖子 123】")
    assert build_auto_preview("需要更高权限", 123, {}) is None
    assert "tid=123" in caplog.text and "post unavailable" in caplog.text


@pytest.mark.parametrize("comic", [True, False])
def test_hidden_content_and_attachments_excluded(comic):
    body = '<div class="locked">秘密<img src="https://img.test/secret.jpg"></div>公开<img src="https://img.test/public.jpg">'
    html = page(comic=comic, body=body)
    assert build_auto_preview(html, 123, {}) == (
        AutoLinkPreview("【测试帖子】", ["https://img.test/public.jpg"]) if comic else AutoLinkPreview("【测试帖子】\n公开")
    )
    # 附件位于 postmessage 外；手动 parser 的正文去隐藏处理无法覆盖这里。
    html = page(comic=comic, body="").replace(
        '<div id="postmessage_100"></div>',
        '<div id="postmessage_100"></div><div class="locked"><div class="pattl"><img src="https://img.test/secret.jpg"></div></div>',
    )
    assert build_auto_preview(html, 123, {}) == AutoLinkPreview("【测试帖子】")


def test_visible_attachment_as_first_image():
    html = page(comic=True, body="").replace(
        '<div id="postmessage_100"></div>',
        '<div id="postmessage_100"></div><div class="pattl"><img src="https://img.test/attachment.jpg"></div>',
    )
    assert build_auto_preview(html, 123, {}) == AutoLinkPreview("【测试帖子】", ["https://img.test/attachment.jpg"])


@pytest.mark.parametrize("comic", [True, False])
def test_quoted_content_excluded_from_preview(comic):
    body = (
        '<div class="quote"><blockquote>引用的旧回复'
        '<img src="https://img.test/quoted.jpg"></blockquote></div>'
        '正文<img src="https://img.test/own.jpg">'
    )
    expected = (
        AutoLinkPreview("【测试帖子】", ["https://img.test/own.jpg"])
        if comic
        else AutoLinkPreview("【测试帖子】\n正文")
    )
    assert build_auto_preview(page(comic=comic, body=body), 123, {}) == expected


def test_long_quote_does_not_consume_excerpt():
    body = '<div class="quote"><blockquote>' + "引用" * 30 + "</blockquote></div>正文"
    config = {"auto_parse": {"novel": {"text_max_len": 10}}}
    assert build_auto_preview(page(body=body), 123, config) == AutoLinkPreview("【测试帖子】\n正文")


@pytest.mark.parametrize("comic", [True, False])
def test_missing_main_floor_never_uses_reply(comic):
    reply = '<div id="post_200"><div id="postnum200"><em>2</em></div><div id="postmessage_200">回复<img src="https://img.test/reply.jpg"></div></div>'
    html = page(comic=comic, body="", extra=reply).replace('<div id="postmessage_100"></div>', "")
    assert build_auto_preview(html, 123, {}) == AutoLinkPreview("【测试帖子】")


def test_main_floor_without_number():
    assert build_auto_preview(page(floor=""), 123, {}) == AutoLinkPreview("【测试帖子】\n小说正文")


@pytest.mark.parametrize("comic", [True, False])
async def test_removed_category_switches_are_ignored(comic):
    config = {"auto_parse": {
        "enable": True, "comic": {"enable": False}, "novel": {"enable": False, "text_max_len": 2},
    }}
    html = page(comic=comic, body='小说正文<img src="https://img.test/first.jpg">')
    expected = AutoLinkPreview("【测试帖子】", ["https://img.test/first.jpg"]) if comic else AutoLinkPreview("【测试帖子】\n小说…")
    assert await collect(event(), config, {}, forum_client(html)) == [expected]
    config["auto_parse"]["enable"] = False
    client = forum_client(html)
    assert await collect(event(), config, {}, client) == []
    client.get_text.assert_not_awaited()


async def test_auth_failure_is_silent(caplog):
    client = forum_client()
    client.is_logged_in = lambda html: False
    config = {"auto_parse": {"enable": True}}
    ev = event(text=f"{URL} {URL.replace('123', '456')}")
    assert await collect(ev, config, {}, client) == []
    assert client.get_text.await_count == 1
    assert "tid=123" in caplog.text and "login expired" in caplog.text


async def test_inaccessible_post_is_silent(caplog):
    assert await collect(event(), {"auto_parse": {"enable": True}}, {}, forum_client("需要更高权限")) == []
    assert "post unavailable" in caplog.text


async def test_failed_attempt_still_consumes_cooldown():
    client = forum_client()
    client.get_text.side_effect = RuntimeError("offline")
    config = {"auto_parse": {"enable": True}}
    state = {}
    assert await collect(event(), config, state, client) == []
    client.get_text.side_effect = None
    assert await collect(event(), config, state, client) == []
    client.get_text.assert_awaited_once()


@pytest.mark.parametrize("title", ["小说", "普通帖子"])
async def test_zero_length_sends_title_only(title):
    config = {"auto_parse": {"enable": True, "novel": {"text_max_len": 0}}}
    client = forum_client(page(title=title, body='正文<img src="https://img.test/cover.jpg">'))
    assert await collect(event(), config, {}, client) == [AutoLinkPreview(f"【{title}】")]
