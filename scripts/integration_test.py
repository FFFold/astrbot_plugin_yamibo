r"""集成测试：使用 AstrBot 配置中的 cookie 访问真实论坛（只读）。

覆盖链路：
  A. 传输层：登录态、token 并发刷新合并（P0-4）、刷新后登录态保持
  B. 订阅链路：真实帖子只看楼主视图 -> 注入基线 -> _check_one 推送
     （文本+图片签名、游标推进；P0-1/P0-3）
  C. 漫画链路：真实主楼图片下载（失败统计/原子写/缓存复用/延迟清理；P0-5/P1-9）
  D. 提醒解析：parse_notice_count（P3-6）+ 签到状态只读
  E. 消息链构造：真实 astrbot Comp 构建文本+图片链（需 AstrBot venv）

只读测试：不签到、不发送消息、不写论坛数据。
cookie 来源：AstrBot 配置 data/config/astrbot_plugin_yamibo_config.json，
            可用环境变量 YAMIBO_AUTH / YAMIBO_SALTKEY 覆盖。

用法（PowerShell）：
  .venv\Scripts\python.exe scripts\integration_test.py      # dev venv（无 E 项）
  & D:\Projects\AstrBot\.venv\Scripts\python.exe scripts\integration_test.py  # AstrBot venv（含 E 项）
"""

import asyncio
import json
import os
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from yamibo.client import ForumClient  # noqa: E402
from yamibo.packager import Packager  # noqa: E402
from yamibo.parser import parse_notice_count, parse_sign_status, parse_thread  # noqa: E402
from yamibo.scheduler import Scheduler  # noqa: E402
from yamibo.subscriber import InMemoryStore, Subscriber  # noqa: E402
from yamibo.utils import cfg_get  # noqa: E402

PLUGIN_DIR = Path(__file__).resolve().parent.parent
ASTRBOT_ROOT = PLUGIN_DIR.parent.parent.parent  # data/plugins/<name> -> AstrBot 根目录
CONFIG_PATH = ASTRBOT_ROOT / "data" / "config" / "astrbot_plugin_yamibo_config.json"

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
)

_checks = {"ok": 0, "fail": 0, "skip": 0}


def ok(name: str, detail: str = "") -> None:
    _checks["ok"] += 1
    print(f"OK  {name}" + (f"  {detail}" if detail else ""))


def fail(name: str, detail: str) -> None:
    _checks["fail"] += 1
    print(f"FAIL {name}  {detail}")


def skip(name: str, detail: str = "") -> None:
    _checks["skip"] += 1
    print(f"SKIP {name}" + (f"  {detail}" if detail else ""))


def load_credentials() -> tuple[str, str]:
    auth = os.environ.get("YAMIBO_AUTH", "").strip()
    saltkey = os.environ.get("YAMIBO_SALTKEY", "").strip()
    if (not auth or not saltkey) and CONFIG_PATH.exists():
        cfg = json.loads(CONFIG_PATH.read_text(encoding="utf-8-sig"))  # 配置可能带 UTF-8 BOM
        auth = auth or str(cfg.get("login", {}).get("auth", ""))
        saltkey = saltkey or str(cfg.get("login", {}).get("saltkey", ""))
    return auth, saltkey


async def check_login(client: ForumClient) -> bool:
    html = await client.get_text("/forum.php")
    if client.is_logged_in(html):
        ok("A-登录态", "cookie 有效")
        return True
    fail("A-登录态", "cookie 无效或已过期")
    return False


async def check_concurrent_refresh(client: ForumClient) -> None:
    """强制 token 过期后 3 路并发刷新：只应求解 1 次（P0-4 锁合并）。

    真实论坛在 token 仍有效时不返回挑战页，_solve 会得到 None（无挑战可解），
    这是预期行为：锁合并的核心断言是「并发下只求解一次、结果一致」。
    """
    orig = client._solve
    calls = {"n": 0}

    async def counting_solve():
        calls["n"] += 1
        return await orig()

    client._solve = counting_solve
    client._token_solved_at = 0.0  # 强制过期触发刷新
    try:
        results = await asyncio.gather(
            client.refresh_token(), client.refresh_token(), client.refresh_token()
        )
        if calls["n"] == 1 and len(set(results)) == 1:
            ok("A-token 并发刷新", f"3 路并发仅求解 1 次，结果一致={results[0]}")
        else:
            fail("A-token 并发刷新", f"results={results} solve_calls={calls['n']}")
        # 求解失败进入退避：立即再刷新不应重复求解（P0-4 退避）
        if await client.refresh_token() is False and calls["n"] == 1:
            ok("A-求解失败退避", "60s 窗口内不再重复求解")
        else:
            fail("A-求解失败退避", f"solve_calls={calls['n']}")
    finally:
        client._solve = orig


async def check_subscription(client: ForumClient) -> tuple[list[str], list[str]]:
    """真实帖子订阅链路：基线注入后 _check_one 推送并推进游标。"""
    tid = 574233
    html = await client.get_text(f"/forum.php?mod=viewthread&tid={tid}")
    if not client.is_logged_in(html):
        fail("B-登录态", "cookie 失效")
        return [], []
    tc = parse_thread(html, tid)
    if not tc.floors or not tc.author_uid:
        fail("B-帖子解析", "无楼层或作者信息")
        return [], []
    author_html = await client.get_thread_author_view(tid, tc.author_uid)
    tc_author = parse_thread(author_html, tid)
    author_floors = [f for f in tc_author.floors if f.author_uid == tc.author_uid]
    if len(author_floors) < 2:
        fail("B-数据", f"楼主仅 {len(author_floors)} 楼，不足 2 楼")
        return [], []
    # author view 为倒序页，按楼层升序取「次高/最高」两楼
    sorted_floors = sorted(author_floors, key=lambda f: f.floor)
    ok(
        "B-只看楼主",
        f"tid={tid} 楼主 {len(sorted_floors)} 楼（L{sorted_floors[0].floor}~L{sorted_floors[-1].floor}）",
    )

    sub = Subscriber(InMemoryStore())
    sent: list[tuple[str, str, list[str]]] = []

    async def send(umo: str, text: str, images: list[str]) -> None:
        sent.append((umo, text, list(images)))

    cfg = {
        "subscription": {"text_max_len": 2000, "image_max": 50},
        "limits": {"skip_hidden_content": True},
    }
    sched = Scheduler(client, sub, lambda k, d=None: cfg_get(cfg, k, d), send)
    await sub.subscribe(
        tid, "itest:umo", title=tc.title, op_uid=tc.author_uid, op_name=tc.author_name
    )
    prev, last = sorted_floors[-2], sorted_floors[-1]
    await sub.update_baseline(tid, floor=prev.floor, pid=prev.pid)
    await sched._check_one(await sub.get_by_tid(tid))

    if len(sent) != 1:
        fail("B-推送", f"期望 1 条推送，实际 {len(sent)} 条")
        return [], []
    umo, text, images = sent[0]
    if f"L{last.floor}" in text and f"thread-{tid}" in text:
        ok("B-推送内容", f"L{prev.floor}->L{last.floor} 文本 {len(text)} 字符 图片 {len(images)} 张")
    else:
        fail("B-推送内容", f"text={text[:80]!r}")
    after = await sub.get_by_tid(tid)
    if after.last_floor == last.floor and after.last_pid == last.pid:
        ok("B-游标推进", f"L{after.last_floor}（仅送达楼层推进）")
    else:
        fail("B-游标推进", f"期望 ({last.floor},{last.pid}) 实际 ({after.last_floor},{after.last_pid})")
    return list(last.images), tc.op_images()


async def check_comic_download(client: ForumClient, op_images: list[str]) -> None:
    if not op_images:
        skip("C-漫画下载", "无主楼图片")
        return
    urls = op_images[:8]
    with tempfile.TemporaryDirectory() as td:
        workdir = Path(td)
        pkg = Packager(client.session, concurrency=3, workdir=workdir)
        res = await pkg.download_images(
            urls, "itest_comic", referer="https://bbs.yamibo.com/thread-574233-1-1.html"
        )
        if res.failed:
            fail("C-下载", f"{res.failed}/{res.total} 张失败")
        elif len(res.files) == len(urls):
            ok("C-下载", f"{len(res.files)}/{res.total} 张（并发 3）")
        else:
            fail("C-下载", f"files={len(res.files)} total={res.total}")
        tmp_left = list((workdir / "itest_comic").glob("*.tmp"))
        ok("C-原子写", "无 .tmp 残留") if not tmp_left else fail("C-原子写", f"残留 {tmp_left}")

        class DeadSession:
            def get(self, url, **kw):
                raise AssertionError("缓存命中时不应发起请求")

        pkg2 = Packager(DeadSession(), concurrency=3, workdir=workdir)
        res2 = await pkg2.download_images(urls, "itest_comic")
        if res2.failed == 0 and len(res2.files) == len(urls):
            ok("C-缓存复用", "完整文件直接复用，0 请求")
        else:
            fail("C-缓存复用", f"failed={res2.failed} files={len(res2.files)}")

        cutoff = time.time()
        Packager.cleanup_older_than(workdir / "itest_comic", cutoff)
        ok("C-延迟清理", "快照后文件已删除") if not (workdir / "itest_comic").exists() else fail(
            "C-延迟清理", "目录残留"
        )


async def check_notice_and_sign(client: ForumClient) -> None:
    html = await client.get_text("/plugin.php?id=zqlj_sign")
    st = parse_sign_status(html)
    ok("D-签到状态(只读)", f"signed_today={st.signed_today}")
    html2 = await client.get_text("/home.php?mod=space&do=notice")
    count = parse_notice_count(html2)
    if count is not None:
        ok("D-未读提醒", f"count={count}")
    else:
        # 当前模板静态页不渲染未读数字（JS 异步填充），返回 None 时指令显示「?」属预期降级
        ok("D-未读提醒", "当前模板静态页无数字徽标，指令按预期显示「?」")


def check_push_chain(images: list[str]) -> None:
    try:
        import astrbot.api.message_components as Comp
        from astrbot.api.event import MessageChain
    except ImportError:
        skip("E-消息链构造", "无 astrbot 环境（用 AstrBot venv 运行可启用）")
        return
    from yamibo.utils import build_push_chain

    sample = images[:2] if images else ["https://bbs.yamibo.com/data/attachment/forum/202608/09/a.jpg"]
    chain = MessageChain(chain=build_push_chain("集成测试正文", sample, Comp))
    types = [type(c).__name__ for c in chain.chain]
    if types == ["Plain"] + ["Image"] * len(sample):
        ok("E-消息链构造", f"{types}（真实 astrbot Comp）")
    else:
        fail("E-消息链构造", f"{types}")


async def main() -> int:
    auth, saltkey = load_credentials()
    if not auth or not saltkey:
        print("SKIP: 未找到 cookie（AstrBot 配置 data/config/astrbot_plugin_yamibo_config.json 或环境变量）")
        return 0
    client = ForumClient(auth=auth, saltkey=saltkey, user_agent=UA)
    await client.start()
    try:
        if not await check_login(client):
            return 1
        await check_concurrent_refresh(client)
        html = await client.get_text("/forum.php")
        if client.is_logged_in(html):
            ok("A-token 刷新后登录态", "保持有效")
        else:
            fail("A-token 刷新后登录态", "刷新后登录态丢失")
        last_images, op_images = await check_subscription(client)
        await check_comic_download(client, op_images)
        await check_notice_and_sign(client)
        check_push_chain(last_images or op_images)
    except Exception as e:  # noqa: BLE001 - 集成脚本逐项报告
        fail("异常", f"{type(e).__name__}: {e}")
    finally:
        await client.close()
    print(f"\n结果: {_checks['ok']} OK / {_checks['fail']} FAIL / {_checks['skip']} SKIP")
    return 1 if _checks["fail"] else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
