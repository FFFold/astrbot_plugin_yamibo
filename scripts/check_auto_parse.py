"""Offline integration of the group-link handler with real AstrBot imports.

Run in an AstrBot Python environment, from any working directory:
  python scripts/check_auto_parse.py
  python scripts/check_auto_parse.py --astrbot-root /path/to/AstrBot

The optional root adds an AstrBot source checkout to the import path; its
dependencies must already be installed. Run in a fresh process. No bot instance,
forum requests, credentials, or real messages are used. The forum client and
event transport are stubs; message components and results come from AstrBot.
Result hooks and protocol support need a running-bot check.
"""

import argparse
import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

PLUGIN_DIR = Path(__file__).resolve().parent.parent
URL = "https://bbs.yamibo.com/thread-123-1-1.html"
TITLE = "\u3010Preview\u3011"
PAGE = (
    '<div id="pt"><a href="forum-49-1.html">Literature</a></div>'
    '<span id="thread_subject">Preview</span>'
    '<div id="postlist"><div id="post_1">'
    '<span id="postnum1"><em>1</em></span>'
    '<div id="postmessage_1">First paragraph.</div></div></div>'
)
COMIC_PAGE = PAGE.replace("Literature", "&#28459;&#30011;").replace(
    "First paragraph.",
    '<img src="https://example.invalid/first.jpg">'
    '<img src="https://example.invalid/second.jpg">',
)
_checks = {"ok": 0, "fail": 0}


def ok(name: str) -> None:
    _checks["ok"] += 1
    print(f"OK  {name}")


def setup(
    runtime,
    *,
    bypass: bool = False,
    whitelist: list[str] | None = None,
    html: str = PAGE,
    authenticated: bool = True,
    links: int = 1,
    text_limit: int = 0,
):
    config = {
        "auto_parse": {
            "enable": True,
            "bypass_reply_quote": bypass,
            "group_whitelist": whitelist or [],
            "novel": {"enable": True, "text_max_len": text_limit},
            "comic": {"enable": True},
        }
    }
    event = SimpleNamespace(
        get_group_id=lambda: "42",
        get_sender_id=lambda: "2",
        get_self_id=lambda: "1",
        get_message_str=lambda: " ".join(URL.replace("123", str(123 + i)) for i in range(links)),
        get_extra=lambda key, default=None: default,
        unified_msg_origin="qq:GroupMessage:42",
        send=AsyncMock(),
    )
    event.chain_result = lambda chain: runtime.AstrMessageEvent.chain_result(event, chain)
    event.plain_result = lambda text: runtime.AstrMessageEvent.plain_result(event, text)
    instance = SimpleNamespace(
        config=config,
        _cool={},
        client=SimpleNamespace(
            get_text=AsyncMock(return_value=html),
            is_logged_in=lambda page: authenticated,
        ),
    )
    return instance, event


async def consume(runtime, instance, event) -> list:
    # Keep yielded results distinct from direct sends; AstrBot hooks are not run.
    return [result async for result in runtime.plugin.AstrBotPlugin.auto_parse_links(instance, event)]


async def check_auto_parse(runtime) -> None:
    for bypass in (False, True):
        instance, event = setup(runtime, bypass=bypass)
        results = await consume(runtime, instance, event)
        assert len(results) == (0 if bypass else 1)
        assert event.send.await_count == (1 if bypass else 0)
        result = event.send.await_args.args[0] if bypass else results[0]
        assert len(result.chain) == 1
        assert isinstance(result.chain[0], runtime.Plain) and result.chain[0].text == TITLE
        assert not any(isinstance(component, runtime.Reply) for component in result.chain)
        instance.client.get_text.assert_awaited_once_with("/forum.php?mod=viewthread&tid=123")
        ok(f"{'direct send' if bypass else 'yield result'} branch and title-only preview")

    instance, event = setup(runtime, text_limit=5)
    results = await consume(runtime, instance, event)
    assert len(results) == 1 and results[0].chain[0].text == f"{TITLE}\nFirst\u2026"
    ok("novel excerpt uses the independent text limit")

    instance, event = setup(runtime, html=COMIC_PAGE)
    results = await consume(runtime, instance, event)
    assert len(results) == 1 and len(results[0].chain) == 2
    assert isinstance(results[0].chain[0], runtime.Plain) and results[0].chain[0].text == TITLE
    image = results[0].chain[1]
    assert isinstance(image, runtime.Image) and image == runtime.Image.fromURL("https://example.invalid/first.jpg")
    ok("comic preview contains only the first main-post image")

    instance, event = setup(runtime, links=3)
    assert len(await consume(runtime, instance, event)) == 1
    assert instance.client.get_text.await_count == 1
    event.send.assert_not_awaited()
    ok("multiple links produce only one preview")

    for bypass in (False, True):
        for options in ({"authenticated": False}, {"html": "Permission denied"}):
            instance, event = setup(runtime, bypass=bypass, **options)
            assert await consume(runtime, instance, event) == []
            event.send.assert_not_awaited()
    ok("login expiry and inaccessible posts are silent in both send branches")

    for kind in ("comic", "novel"):
        instance, event = setup(runtime, html=COMIC_PAGE if kind == "comic" else PAGE)
        instance.config["auto_parse"][kind]["enable"] = False
        assert await consume(runtime, instance, event) == []
        event.send.assert_not_awaited()
    ok("disabled preview types never send")

    instance, event = setup(runtime, whitelist=["43"])
    assert await consume(runtime, instance, event) == []
    instance.client.get_text.assert_not_awaited()
    event.send.assert_not_awaited()
    assert instance._cool == {}
    ok("a group outside the whitelist has no fetch, send, or cooldown")

    for bypass in (False, True):
        instance, event = setup(runtime, bypass=bypass)
        instance.client.get_text.side_effect = RuntimeError("offline")
        assert await consume(runtime, instance, event) == []
        event.send.assert_not_awaited()
        for target in ("yamibo.utils.build_auto_preview", "main.auto_parse_group_links"):
            instance, event = setup(runtime, bypass=bypass)
            with patch(target, side_effect=RuntimeError("parse failed")):
                assert await consume(runtime, instance, event) == []
            event.send.assert_not_awaited()
    ok("fetch, parser, and entrypoint exceptions are silent in both send branches")

    instance, event = setup(runtime, bypass=True)
    event.send.side_effect = RuntimeError("send failed")
    assert await consume(runtime, instance, event) == []
    assert event.send.await_count == 1
    ok("a direct-send failure does not retry with an error message")

    gate = runtime.plugin._GroupLinkFilter()
    original_config = runtime.plugin._GroupLinkFilter.plugin_config
    try:
        runtime.plugin._GroupLinkFilter.plugin_config = {}
        assert not gate.filter(event, {})
        runtime.plugin._GroupLinkFilter.plugin_config = instance.config
        assert gate.filter(event, {})
        instance.config["auto_parse"]["group_whitelist"] = ["43"]
        assert not gate.filter(event, {})
    finally:
        runtime.plugin._GroupLinkFilter.plugin_config = original_config
    ok("real AstrBot custom filter reads live plugin configuration")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--astrbot-root", type=Path, help="optional AstrBot source checkout root")
    args = parser.parse_args()
    if args.astrbot_root:
        root = args.astrbot_root.expanduser().resolve()
        if not (root / "astrbot").is_dir():
            parser.error("--astrbot-root must contain the astrbot package directory")
        sys.path.insert(0, str(root))
    sys.path.insert(0, str(PLUGIN_DIR))
    try:
        from astrbot.api.event import AstrMessageEvent
        from astrbot.api.message_components import Image, Plain, Reply

        import main as plugin

        assert Path(plugin.__file__).resolve() == PLUGIN_DIR / "main.py"
        ok("plugin imports against real AstrBot")
        runtime = SimpleNamespace(
            plugin=plugin, AstrMessageEvent=AstrMessageEvent, Image=Image, Plain=Plain, Reply=Reply
        )
        asyncio.run(check_auto_parse(runtime))
    except Exception as error:
        _checks["fail"] += 1
        print(f"FAIL {type(error).__name__}: {error}")
        if isinstance(error, ImportError):
            print("Use an AstrBot Python environment with plugin dependencies installed.")
        else:
            import traceback

            traceback.print_exc()
    print(f"\nResult: {_checks['ok']} OK / {_checks['fail']} FAIL")
    return 1 if _checks["fail"] else 0


if __name__ == "__main__":
    sys.exit(main())
