import pytest
from aiohttp import web

from yamibo.client import ForumClient, NotLoggedInError

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
