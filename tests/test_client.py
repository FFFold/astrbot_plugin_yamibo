import asyncio
import time

import pytest
from aiohttp import web

from yamibo.client import SOLVE_BACKOFF_SEC, TOKEN_TTL, ForumClient, NotLoggedInError, WafError

CHALLENGE_405 = """<!DOCTYPE html><html><head><script>window.__noxExpire=30;</script>
<script src="/sd5prgymvjlf4cklsqkz91do2mhorb/static/wb/2.1/nox_20260413.js"></script>
</head><body></body></html>"""

CHALLENGE_JS = """
!function () {
  var name = 'nox_jst_v1';
  window.document.cookie = name + '=2.0_1234_testtoken;path=/';
}();
"""

LOGGED_IN = """<script>var discuz_uid = '621168';</script><div id="postlist">x</div>"""
NOT_LOGGED_IN = """<script>var discuz_uid = '0';</script>"""


@pytest.fixture
async def server(aiohttp_server):
    calls = {"count": 0, "cookies_seen": []}

    async def page_handler(request):
        calls["count"] += 1
        cookie = request.headers.get("Cookie", "")
        calls["cookies_seen"].append(cookie)
        if calls["count"] <= 1:
            return web.Response(status=405, text=CHALLENGE_405)
        return web.Response(text=LOGGED_IN)

    async def js_handler(request):
        return web.Response(text=CHALLENGE_JS)

    async def root_handler(request):
        return web.Response(status=405, text=CHALLENGE_405)

    app = web.Application()
    app.router.add_get("/page", page_handler)
    app.router.add_get("/", root_handler)
    app.router.add_get("/sd5prgymvjlf4cklsqkz91do2mhorb/static/wb/2.1/nox_20260413.js", js_handler)
    return await aiohttp_server(app), calls


async def test_405_triggers_solve_and_retry(server):
    srv, calls = server
    client = ForumClient(
        auth="AUTH", saltkey="SALT",
        user_agent="Mozilla/5.0 Test UA",
        base_url=f"http://{srv.host}:{srv.port}",
    )
    await client.start()
    try:
        html = await client.get_text("/page")
        assert "discuz_uid" in html
        assert calls["count"] == 2
        assert any("nox_jst_v1=2.0_1234_testtoken" in c for c in calls["cookies_seen"])
    finally:
        await client.close()


async def test_is_logged_in_check(server):
    client = ForumClient(auth="a", saltkey="b", user_agent="ua")
    assert client.is_logged_in(LOGGED_IN)
    assert not client.is_logged_in(NOT_LOGGED_IN)


async def test_ensure_logged_in_raises(server):
    client = ForumClient(auth="a", saltkey="b", user_agent="ua")
    with pytest.raises(NotLoggedInError):
        client.ensure_logged_in(NOT_LOGGED_IN)


RANKLIST_SAMPLE = """
<script>var discuz_uid = '621168';</script>
<div class="tl"><table><tbody><tr class="th"><td>&nbsp;</td><th>主题</th><td>版块</td><td>作者</td><td>热度</td></tr></tbody>
<tbody><tr><th><a href="thread-519989-1-1.html">汇总</a></th><td class="by"><cite><a href="space-uid-1.html">hongyuny</a></cite></td><td><a href="thread-519989-1-1.html">3365</a></td></tr></tbody></table></div>
<div class="notice">排行榜数据已被缓存，上次于 2026-8-8 14:39 被更新，下次将于 2026-8-8 19:39 进行更新</div>
"""

RANKLIST_NOT_LOGGED_IN = RANKLIST_SAMPLE.replace("var discuz_uid = '621168';", "var discuz_uid = '0';")


async def test_get_hot_rank_uses_heats_today_url(monkeypatch):
    captured: dict = {}

    async def fake_get_text(path, **kw):
        captured["path"] = path
        return RANKLIST_SAMPLE

    client = ForumClient(auth="a", saltkey="b", user_agent="ua")
    monkeypatch.setattr(client, "get_text", fake_get_text)
    items, next_time = await client.get_hot_rank(5)
    assert "view=heats" in captured["path"]
    assert "orderby=today" in captured["path"]
    assert "thisweek" not in captured["path"]
    assert items[0].tid == 519989
    assert items[0].reply_count == 3365
    assert next_time is not None and next_time.minute == 39
    items2 = await client.get_hot_threads(2)
    assert items2[0].tid == 519989


async def test_get_hot_rank_raises_when_not_logged_in(monkeypatch):
    async def fake_get_text(path, **kw):
        return RANKLIST_NOT_LOGGED_IN

    client = ForumClient(auth="a", saltkey="b", user_agent="ua")
    monkeypatch.setattr(client, "get_text", fake_get_text)
    with pytest.raises(NotLoggedInError):
        await client.get_hot_rank(5)


async def test_concurrent_refresh_coalesces(monkeypatch):
    """并发刷新 token：只求解一次，其余协程复用同一结果。"""
    client = ForumClient(auth="a", saltkey="b", user_agent="ua")
    calls = {"n": 0}

    async def fake_solve():
        calls["n"] += 1
        await asyncio.sleep(0.05)
        return "tok1"

    monkeypatch.setattr(client, "_solve", fake_solve)
    monkeypatch.setattr(client, "_apply_static_cookies", lambda: None)
    results = await asyncio.gather(client.refresh_token(), client.refresh_token(), client.refresh_token())
    assert results == [True, True, True]
    assert calls["n"] == 1
    assert client._nox_token == "tok1"


async def test_refresh_skipped_when_token_fresh(monkeypatch):
    client = ForumClient(auth="a", saltkey="b", user_agent="ua")
    calls = {"n": 0}

    async def fake_solve():
        calls["n"] += 1
        return "tok"

    monkeypatch.setattr(client, "_solve", fake_solve)
    monkeypatch.setattr(client, "_apply_static_cookies", lambda: None)
    assert await client.refresh_token() is True
    assert await client.refresh_token() is True
    assert calls["n"] == 1


async def test_solve_failure_backoff(monkeypatch):
    """求解失败记录失败时刻，退避期内不重复求解。"""
    client = ForumClient(auth="a", saltkey="b", user_agent="ua")
    calls = {"n": 0}

    async def fake_solve():
        calls["n"] += 1
        return None

    monkeypatch.setattr(client, "_solve", fake_solve)
    assert await client.refresh_token() is False
    assert await client.refresh_token() is False
    assert calls["n"] == 1


async def test_backoff_with_stale_token_returns_false(monkeypatch):
    """退避期内即使持有过期 token，refresh_token 也不该宣称拿到新 token。"""
    client = ForumClient(auth="a", saltkey="b", user_agent="ua")
    client._nox_token = "stale"
    client._token_solved_at = time.monotonic() - TOKEN_TTL - 10
    client._solve_fail_at = time.monotonic()
    monkeypatch.setattr(client, "_apply_static_cookies", lambda: None)
    assert time.monotonic() - client._solve_fail_at < SOLVE_BACKOFF_SEC
    assert await client.refresh_token() is False


async def test_solve_backoff_expires_then_retries(monkeypatch):
    client = ForumClient(auth="a", saltkey="b", user_agent="ua")
    calls = {"n": 0}

    async def fake_solve():
        calls["n"] += 1
        return "tok" if calls["n"] >= 2 else None

    monkeypatch.setattr(client, "_solve", fake_solve)
    monkeypatch.setattr(client, "_apply_static_cookies", lambda: None)
    assert await client.refresh_token() is False
    monkeypatch.setattr(client, "_solve_fail_at", time.monotonic() - 61)
    assert await client.refresh_token() is True
    assert calls["n"] == 2


async def test_get_text_waf_fail_respects_backoff(server, monkeypatch):
    """求解失败 + 页面 405：退避期内不再重复求解，直接抛 WafError，不放大请求量。"""
    srv, calls = server
    client = ForumClient(
        auth="AUTH", saltkey="SALT",
        user_agent="Mozilla/5.0 Test UA",
        base_url=f"http://{srv.host}:{srv.port}",
    )

    async def fake_solve():
        return None

    monkeypatch.setattr(client, "_solve", fake_solve)
    monkeypatch.setattr(client, "_apply_static_cookies", lambda: None)
    await client.start()
    try:
        with pytest.raises(WafError):
            await client.get_text("/page")
        assert calls["count"] == 1  # 只打了 1 次请求，没有 405 后的重复轰炸
    finally:
        await client.close()

