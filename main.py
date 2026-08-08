"""百合会论坛助手插件。"""

import asyncio
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, MessageChain, filter
from astrbot.api.star import Context, Star

from yamibo.client import ForumClient, NotLoggedInError, WafError
from yamibo.models import HotItem
from yamibo.packager import Packager, build_forward_chunks, ensure_safe_filename
from yamibo.parser import (
    parse_forum_threads,
    parse_my_records,
    parse_search_results,
    parse_thread,
)
from yamibo.scheduler import Scheduler
from yamibo.subscriber import Subscriber
from yamibo.utils import cfg_get, cooldown_ok, parse_tid_input

FORUM_NAMES = {
    "5": "動漫區", "13": "貼圖區", "33": "海域區", "49": "文學區",
    "44": "遊戲區", "379": "影視區", "19": "資源交流區", "16": "管理版",
}

DEFAULT_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
)


def fmt_hot(items: list[HotItem]) -> str:
    lines = ["【百合会热帖】"]
    for i in items:
        reply = f"（回复 {i.reply_count}）" if i.reply_count else ""
        lines.append(f"{i.title}{reply} https://bbs.yamibo.com/thread-{i.tid}-1-1.html")
    return "\n".join(lines)


def fmt_thread_list(items) -> str:
    lines = ["【帖子列表】"]
    for i in items:
        lines.append(f"{i.tid} | {i.title} | {i.author} | {i.last_reply_time}")
    return "\n".join(lines)


class AstrBotPlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.config = config
        self.client: ForumClient | None = None
        self.subscriber = Subscriber(_AstrBotKVStore(self))
        self.scheduler: Scheduler | None = None
        self.packager: Packager | None = None
        self._cool: dict = {}
        self._tid_locks: dict[int, asyncio.Lock] = {}
        self._startup_task = asyncio.create_task(self._init_async())

    # ---- 初始化 ----
    async def _init_async(self) -> None:
        try:
            if not cfg_get(self.config, "login.auth") or not cfg_get(self.config, "login.saltkey"):
                logger.warning("yamibo: 未配置 auth/saltkey，功能不可用")
                return
            self.client = ForumClient(
                auth=str(cfg_get(self.config, "login.auth", "")),
                saltkey=str(cfg_get(self.config, "login.saltkey", "")),
                user_agent=str(cfg_get(self.config, "login.user_agent", "")) or DEFAULT_UA,
                proxy=str(cfg_get(self.config, "login.proxy", "")),
            )
            await self.client.start()
            manual_token = str(cfg_get(self.config, "login.manual_nox_token", "")).strip()
            if manual_token:
                self.client.set_manual_token(manual_token)
            from astrbot.core.utils.astrbot_path import get_astrbot_data_path

            data_dir = Path(get_astrbot_data_path()) / "plugin_data" / "astrbot_plugin_yamibo"
            data_dir.mkdir(parents=True, exist_ok=True)
            self.packager = Packager(
                self.client.session,
                concurrency=int(cfg_get(self.config, "comic.download_concurrency", 4)),
                workdir=data_dir,
            )
            self.scheduler = Scheduler(
                self.client, self.subscriber,
                lambda key, default=None: cfg_get(self.config, key, default),
                self._push,
            )
            self.scheduler.start()
            logger.info("yamibo: 初始化完成")
        except Exception as e:
            logger.error(f"yamibo: 初始化失败: {e}")

    async def _push(self, umo: str, text: str) -> None:
        chain = MessageChain().message(text)
        await self.context.send_message(umo, chain)

    # ---- 指令：签到（管理员） ----
    @filter.command_group("yamibo")
    def yamibo(self):
        pass

    @yamibo.command("签到")
    @filter.permission_type(filter.PermissionType.ADMIN)
    async def sign_now(self, event: AstrMessageEvent, force: bool = False):
        """手动签到（管理员）。"""
        if not self.client:
            yield event.plain_result("插件未初始化")
            return
        try:
            html, status = await self.client.get_sign_status()
            if status.signed_today and not force:
                yield event.plain_result("今日已签到。")
                return
            await self.client.sign(html=html)
            _, status2 = await self.client.get_sign_status()
            yield event.plain_result("签到完成：" + ("今日已签到" if status2.signed_today else "状态未知"))
        except NotLoggedInError as e:
            yield event.plain_result(str(e))
        except Exception as e:
            logger.error(f"yamibo sign error: {e}")
            yield event.plain_result(f"签到失败: {e}")

    @yamibo.command("签到状态")
    @filter.permission_type(filter.PermissionType.ADMIN)
    async def sign_status(self, event: AstrMessageEvent):
        """查看签到记录（管理员）。"""
        if not self.client:
            yield event.plain_result("插件未初始化")
            return
        try:
            html, status = await self.client.get_sign_status()
            records_html = await self.client.get_text("/plugin.php?id=zqlj_sign&tb=my")
            rec = parse_my_records(records_html)
            lines = ["【签到状态】", "今日：" + ("已签到" if status.signed_today else "未签到")]
            if rec["last_time"]:
                lines.append(f"最近打卡：{rec['last_time']} 奖励 {rec['last_reward']}")
                lines.append(f"近 15 条内打卡次数：{rec['count']}")
            yield event.plain_result("\n".join(lines))
        except Exception as e:
            logger.error(f"yamibo sign status error: {e}")
            yield event.plain_result(f"查询失败: {e}")

    @yamibo.command("提醒")
    @filter.permission_type(filter.PermissionType.ADMIN)
    async def notices(self, event: AstrMessageEvent):
        """未读提醒（管理员）。"""
        if not self.client:
            yield event.plain_result("插件未初始化")
            return
        try:
            html = await self.client.get_text("/home.php?mod=space&do=notice")
            m = re.search(r"未读提醒[^0-9]*(\d+)", html) or re.search(r'class="ntc_l"[^>]*>(\d+)', html)
            count = m.group(1) if m else "?"
            yield event.plain_result(f"未读提醒：{count}（详情请访问 https://bbs.yamibo.com/home.php?mod=space&do=notice）")
        except Exception as e:
            logger.error(f"yamibo notices error: {e}")
            yield event.plain_result(f"查询失败: {e}")

    @yamibo.command("cookie状态")
    @filter.permission_type(filter.PermissionType.ADMIN)
    async def cookie_status(self, event: AstrMessageEvent):
        """校验 cookie（管理员）。"""
        if not self.client:
            yield event.plain_result("插件未初始化")
            return
        try:
            html = await self.client.get_text("/forum.php")
            if self.client.is_logged_in(html):
                uid_m = re.search(r"discuz_uid\s*=\s*'(\d+)'", html)
                yield event.plain_result(f"cookie 有效（uid={uid_m.group(1) if uid_m else '?'}）")
            else:
                yield event.plain_result("cookie 无效或已过期，请重新填写")
        except WafError:
            yield event.plain_result("WAF 挑战失败，请重新提取 cookie 或配置 manual_nox_token")
        except Exception as e:
            yield event.plain_result(f"校验失败: {e}")

    # ---- 指令：公开 ----
    @yamibo.command("热帖")
    async def hot_now(self, event: AstrMessageEvent, n: int = 10):
        """本周热帖。"""
        if not self.client:
            yield event.plain_result("插件未初始化")
            return
        n = max(1, min(n, 30))
        try:
            items = await self.client.get_hot_threads(n)
            yield event.plain_result(fmt_hot(items) if items else "暂无数据")
        except Exception as e:
            logger.error(f"yamibo hot error: {e}")
            yield event.plain_result(f"获取失败: {e}")

    @yamibo.command("订阅热帖")
    async def bind_hot(self, event: AstrMessageEvent):
        """绑定本会话为热帖推送目标。"""
        await self.subscriber.add_hot_target(event.unified_msg_origin)
        yield event.plain_result("本会话已订阅热帖推送。")

    @yamibo.command("取消热帖")
    async def unbind_hot(self, event: AstrMessageEvent):
        """解除热帖推送绑定。"""
        await self.subscriber.remove_hot_target(event.unified_msg_origin)
        yield event.plain_result("已取消热帖推送。")

    @yamibo.command("版块")
    async def forum_list(self, event: AstrMessageEvent, fid: str = "13", sort: str = "hot", page: int = 1):
        """版块帖子列表。fid 支持数字或名称，sort=hot|new。"""
        if not self.client:
            yield event.plain_result("插件未初始化")
            return
        fid_num = fid if str(fid).isdigit() else next((k for k, v in FORUM_NAMES.items() if v == str(fid)), "13")
        page = max(1, min(page, 50))
        sort_param = "heat" if sort == "hot" else "lastpost"
        try:
            html = await self.client.get_text(
                f"/forum.php?mod=forumdisplay&fid={fid_num}&filter={sort_param}&page={page}"
            )
            items = parse_forum_threads(html)
            name = FORUM_NAMES.get(str(fid_num), f"fid{fid_num}")
            result = [f"【{name}】第{page}页"]
            result.extend(fmt_thread_list(items).splitlines()[1:])
            yield event.plain_result("\n".join(result) if items else f"【{name}】暂无帖子")
        except Exception as e:
            logger.error(f"yamibo forum error: {e}")
            yield event.plain_result(f"获取失败: {e}")

    @yamibo.command("搜索")
    async def search(self, event: AstrMessageEvent, keyword: str):
        """全站搜索帖子。"""
        if not self.client:
            yield event.plain_result("插件未初始化")
            return
        if not cooldown_ok(
            self._cool, f"search:{event.unified_msg_origin}",
            int(cfg_get(self.config, "limits.search_cooldown_sec", 30)),
        ):
            yield event.plain_result("搜索冷却中，请稍后再试。")
            return
        try:
            formhash = await self.client.get_formhash()
            if not formhash:
                yield event.plain_result("获取 formhash 失败")
                return
            from urllib.parse import quote

            html = await self.client.get_text(
                f"/search.php?mod=forum&searchsubmit=yes&srchtxt={quote(keyword)}&srchtype=title&formhash={formhash}"
            )
            items = parse_search_results(html)
            yield event.plain_result(fmt_thread_list(items[:10]) if items else "无搜索结果")
        except Exception as e:
            logger.error(f"yamibo search error: {e}")
            yield event.plain_result(f"搜索失败: {e}")

    @yamibo.command("帖子")
    async def thread_preview(self, event: AstrMessageEvent, tid: str):
        """帖子内容预览：标题/作者/正文摘要/前几张图。"""
        if not self.client:
            yield event.plain_result("插件未初始化")
            return
        tid_num = parse_tid_input(tid)
        if not tid_num:
            yield event.plain_result("无效的 tid 或链接")
            return
        try:
            html = await self.client.get_text(f"/forum.php?mod=viewthread&tid={tid_num}")
            tc = parse_thread(html, tid_num)
            if not tc.floors:
                yield event.plain_result("帖子不可见或需要更高权限")
                return
            first = tc.floors[0]
            summary = (first.text or "")[:300]
            lines = [f"【{tc.title}】", f"作者：{tc.author_name}  时间：{first.time}", ""]
            lines.append(summary)
            if first.images:
                lines.append("")
                lines.append(f"图片 {len(first.images)} 张，发送前 {min(3, len(first.images))} 张预览：")
            yield event.plain_result("\n".join(lines))
            for url in first.images[:3]:
                yield event.image_result(url)
        except Exception as e:
            logger.error(f"yamibo thread error: {e}")
            yield event.plain_result(f"获取失败: {e}")

    @yamibo.command("漫画")
    async def comic(self, event: AstrMessageEvent, tid: str, mode: str = ""):
        """解析漫画帖并打包发送。mode=pdf|fwd。"""
        if not self.client or not self.packager:
            yield event.plain_result("插件未初始化")
            return
        tid_num = parse_tid_input(tid)
        if not tid_num:
            yield event.plain_result("无效的 tid 或链接")
            return
        if not cooldown_ok(
            self._cool, f"comic:{event.unified_msg_origin}",
            int(cfg_get(self.config, "limits.comic_cooldown_sec", 60)),
        ):
            yield event.plain_result("漫画命令冷却中，请稍后再试。")
            return
        lock = self._tid_locks.setdefault(tid_num, asyncio.Lock())
        async with lock:
            try:
                yield event.plain_result("正在解析帖子并下载图片，请稍候…")
                html = await self.client.get_text(f"/forum.php?mod=viewthread&tid={tid_num}&ordertype=1")
                tc = parse_thread(html, tid_num)
                urls = tc.op_images()
                if not urls:
                    yield event.plain_result("该帖没有可下载的图片")
                    return
                urls = urls[: int(cfg_get(self.config, "comic.max_pages", 300))]
                yield event.plain_result(f"共 {len(urls)} 张图片，开始下载…")
                files = await self.packager.download_images(
                    urls, f"comic_{tid_num}", referer=f"https://bbs.yamibo.com/thread-{tid_num}-1-1.html"
                )
                if not files:
                    yield event.plain_result("图片下载失败")
                    return
                deliver = mode or str(cfg_get(self.config, "comic.deliver_mode", "auto"))
                is_aiocq = event.get_platform_name() == "aiocqhttp"
                try:
                    if deliver == "fwd" or (deliver == "auto" and is_aiocq):
                        await self._send_forward(event, files, tid_num, tc.title)
                    else:
                        await self._send_pdf(event, files, tid_num, tc.title)
                finally:
                    self.packager.cleanup(files[0].parent)
            except Exception as e:
                logger.error(f"yamibo comic error: {e}")
                yield event.plain_result(f"打包失败: {e}")

    async def _send_pdf(self, event, files, tid_num: int, title: str):
        import astrbot.api.message_components as Comp

        out = files[0].parent / f"{ensure_safe_filename(title or str(tid_num))}.pdf"
        Packager.build_pdf(files, out)
        if out.stat().st_size > int(cfg_get(self.config, "comic.max_file_size_mb", 45)) * 1024 * 1024:
            for f in files[:20]:
                yield event.image_result(str(f))
            yield event.plain_result("PDF 超过大小限制，已改为发送前 20 张图片")
            return
        yield event.chain_result([Comp.File(file=str(out), name=out.name)])

    async def _send_forward(self, event, files, tid_num: int, title: str):
        import astrbot.api.message_components as Comp

        chunks = build_forward_chunks(files)
        sender_name = f"百合会-{title[:20]}" if title else f"百合会-{tid_num}"
        self_id = getattr(event, "get_self_id", lambda: 10000)() or 10000
        for i, chunk in enumerate(chunks):
            nodes = [
                Comp.Node(uin=self_id, name=sender_name, content=[Comp.Image.fromFileSystem(str(f))])
                for f in chunk
            ]
            yield event.chain_result(nodes)
            if i < len(chunks) - 1:
                await asyncio.sleep(2)

    # ---- 指令：订阅 ----
    @yamibo.command("订阅")
    async def subscribe(self, event: AstrMessageEvent, tid: str):
        """订阅帖子更新（只看楼主）。"""
        if not self.client:
            yield event.plain_result("插件未初始化")
            return
        tid_num = parse_tid_input(tid)
        if not tid_num:
            yield event.plain_result("无效的 tid 或链接")
            return
        try:
            html = await self.client.get_text(f"/forum.php?mod=viewthread&tid={tid_num}&ordertype=1")
            tc = parse_thread(html, tid_num)
            if not tc.floors or not tc.author_uid:
                yield event.plain_result("帖子不可见，无法订阅")
                return
            sub = await self.subscriber.subscribe(
                tid_num, event.unified_msg_origin,
                title=tc.title or str(tid_num), op_uid=tc.author_uid, op_name=tc.author_name,
            )
            if sub is None:
                yield event.plain_result("已在订阅列表中。")
                return
            last = max(f.floor for f in tc.floors)
            last_pid = max(f.pid for f in tc.floors)
            await self.subscriber.update_baseline(tid_num, floor=last, pid=last_pid)
            yield event.plain_result(f"已订阅《{tc.title}》，从楼主最新楼层（L{last}）开始跟踪。")
        except Exception as e:
            logger.error(f"yamibo subscribe error: {e}")
            yield event.plain_result(f"订阅失败: {e}")

    @yamibo.command("订阅列表")
    async def sub_list(self, event: AstrMessageEvent):
        """查看本会话订阅。"""
        subs = await self.subscriber.list_for(event.unified_msg_origin)
        if not subs:
            yield event.plain_result("暂无订阅。")
            return
        lines = ["【我的订阅】"]
        for s in subs:
            state = "（已暂停）" if s.paused else ""
            lines.append(f"{s.tid} | {s.title}{state} | 跟踪至 L{s.last_floor}")
        yield event.plain_result("\n".join(lines))

    @yamibo.command("取消订阅")
    async def sub_remove(self, event: AstrMessageEvent, tid: str):
        """取消订阅。"""
        tid_num = parse_tid_input(tid)
        if not tid_num:
            yield event.plain_result("无效的 tid 或链接")
            return
        ok = await self.subscriber.unsubscribe(tid_num, event.unified_msg_origin)
        yield event.plain_result("已取消订阅。" if ok else "未找到该订阅。")

    @yamibo.command("帮助")
    async def help(self, event: AstrMessageEvent):
        """使用说明。"""
        text = (
            "【百合会助手】\n"
            "管理员：/yamibo 签到 | 签到状态 | 提醒 | cookie状态\n"
            "公开：/yamibo 热帖 [N] | 版块 [fid|名称] [hot|new] [页] | 搜索 关键词\n"
            "      /yamibo 帖子 <tid|链接> | 漫画 <tid|链接> [pdf|fwd]\n"
            "      /yamibo 订阅 <tid|链接> | 订阅列表 | 取消订阅 <tid>\n"
            "      /yamibo 订阅热帖 | 取消热帖\n"
            "cookie 获取：DevTools → Application → Cookies → bbs.yamibo.com，\n"
            "复制 EeqY_2132_saltkey 与 EeqY_2132_auth 填入插件配置。"
        )
        yield event.plain_result(text)

    async def terminate(self):
        if self.scheduler:
            await self.scheduler.stop()
        if self.client:
            await self.client.close()
        if self._startup_task and not self._startup_task.done():
            self._startup_task.cancel()


class _AstrBotKVStore:
    """适配 AstrBot KV API（put_kv_data/get_kv_data，>= v4.9.2）。"""

    def __init__(self, plugin) -> None:
        self._plugin = plugin

    async def get(self, key: str, default=None):
        return await self._plugin.get_kv_data(key, default)

    async def set(self, key: str, value) -> None:
        await self._plugin.put_kv_data(key, value)
