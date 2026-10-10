import asyncio
import re
import time
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime
from html import unescape
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

from bs4 import BeautifulSoup

from yamibo.parser import TZ, parse_thread

TID_URL_RE = re.compile(r"(?:thread-(\d+)-|tid=(\d+))")
TIME_RE = re.compile(r"(\d{4})-(\d{1,2})-(\d{1,2})")
DEFAULT_LIMIT = 15

FORWARD_CHUNK = 100  # 合并转发单批节点数上限；订阅与漫画共用
PLAIN_BATCH_BUDGET = 8000  # 订阅直发单条消息字符预算：合并超出则按楼拆批（防平台长度限制导致必然失败）


def chunk_list(items: list, size: int) -> list[list]:
    return [items[i : i + size] for i in range(0, len(items), size)]

# fid -> 显示名；查找支持繁体/简体/常见简称
FORUM_NAMES: dict[str, str] = {
    "5": "動漫區", "13": "貼圖區", "33": "海域區", "49": "文學區",
    "44": "遊戲區", "379": "影視區", "19": "資源交流區", "16": "管理版",
}
FORUM_ALIASES: dict[str, str] = {
    "动漫区": "5", "動漫區": "5", "动漫": "5",
    "贴图区": "13", "貼圖區": "13", "贴图": "13",
    "海域区": "33", "海域區": "33", "海域": "33",
    "文学区": "49", "文學區": "49", "文学": "49",
    "游戏区": "44", "遊戲區": "44", "游戏": "44",
    "影视区": "379", "影視區": "379", "影视": "379",
    "资源交流区": "19", "資源交流區": "19", "资源": "19",
    "管理版": "16",
}

# 旧配置值（merge_forward）兼容映射到统一名字 fwd
_DELIVER_ALIASES = {"merge_forward": "fwd", "forward": "fwd"}


def resolve_fid(raw: str) -> str | None:
    """版块参数解析：数字 fid、繁体/简体名称/简称。无法识别返回 None。"""
    raw = (raw or "").strip()
    if raw.isdigit():
        return raw if raw in FORUM_NAMES else None
    return FORUM_ALIASES.get(raw)


def normalize_deliver_mode(mode: str | None) -> str:
    """统一漫画发送方式命名：merge_forward/forward 旧值归一为 fwd，其余原样返回。"""
    m = (mode or "").strip().lower()
    return _DELIVER_ALIASES.get(m, m)


def cfg_get(config: dict, path: str, default: Any = None) -> Any:
    """按点号路径读取嵌套配置，如 cfg_get(config, "login.auth")。"""
    cur: Any = config
    for part in str(path).split("."):
        if not isinstance(cur, dict):
            return default
        cur = cur.get(part)
    return cur if cur is not None else default


def clamp_int(value, lo: int, hi: int, default: int) -> int:
    """安全整数钳制：无法解析（None/空串/非数字）时返回 default。"""
    try:
        v = int(value)
    except (TypeError, ValueError):
        return default
    return max(lo, min(v, hi))


def build_push_chain(text: str, images: list[str] | None, comp) -> list:
    """构造推送消息链（文本 + 图片）。

    comp 为 astrbot.api.message_components 模块（或等价的测试桩），
    便于在无 astrbot 依赖的 dev venv 中单测。
    """
    chain = [comp.Plain(text=text)]
    for url in images or []:
        chain.append(comp.Image.fromURL(url))
    return chain


def resolve_comic_workdir(config: dict, default_dir: Path) -> Path:
    """漫画临时目录：优先 comic.workdir（容器部署时与协议端共享），否则默认目录。"""
    override = str(cfg_get(config, "comic.workdir", "") or "").strip()
    return Path(override) if override else default_dir


def truncate(text: str, limit: int) -> str:
    text = (text or "").strip()
    if not text:
        return ""
    if len(text) <= limit:
        return text
    return text[:limit] + "…"


def fmt_time(raw: str) -> str:
    """论坛时间格式化为紧凑显示：当年 MM-DD，跨年 YYYY-MM-DD。

    当年/跨年以论坛时区（东八区，parser.TZ）为准，避免非东八区服务器误判。
    """
    m = TIME_RE.search(raw or "")
    if not m:
        return ""
    year, month, day = m.group(1), m.group(2).zfill(2), m.group(3).zfill(2)
    cur = datetime.now(TZ).strftime("%Y")
    return f"{month}-{day}" if year == cur else f"{year}-{month}-{day}"


def fmt_list(
    title: str,
    items: Iterable,
    *,
    hot: bool = False,
    limit: int = DEFAULT_LIMIT,
    footer: str = "",
) -> str:
    """统一帖子列表输出格式。hot=True 时每行附链接与回复数。"""
    all_rows = list(items)
    rows = all_rows[:limit]
    lines = [f"【{title}】"]
    for i, it in enumerate(rows, 1):
        if hot:
            reply = f"（热度 {it.reply_count}）" if getattr(it, "reply_count", 0) else ""
            url = f"https://bbs.yamibo.com/thread-{it.tid}-1-1.html"
            lines.append(f"{i}. {truncate(it.title, 40)}{reply} {url}")
        else:
            author = getattr(it, "author", "") or ""
            t = fmt_time(getattr(it, "last_reply_time", "") or "")
            line = f"{i}. {truncate(it.title, 40)}"
            if author:
                line += f" — {author}"
            if t:
                line += f" ({t})"
            line += f" https://bbs.yamibo.com/thread-{it.tid}-1-1.html"
            lines.append(line)
    if len(all_rows) > limit:
        lines.append(f"⋯ 共 {len(all_rows)} 条，仅显示前 {limit} 条")
    if footer:
        lines.append(footer)
    return "\n".join(lines)


def fmt_comic_header(title: str, tid_num: int) -> str:
    """漫画合并转发合集首条消息：标题 + 原帖链接。"""
    head = (title or "").strip() or f"帖子 {tid_num}"
    return f"【{head}】\n原帖：https://bbs.yamibo.com/thread-{tid_num}-1-1.html"


# ---- 订阅更新推送 ----

@dataclass
class SubPushItem:
    """订阅更新的单个楼层：一次合并转发中的一条消息节点（_check_one 组装，发方转换 Comp）。"""

    floor: int
    name: str  # 节点昵称（楼主名）
    text: str  # 节点文本（标题/正文/链接）
    image_urls: list[str] = field(default_factory=list)


@dataclass
class SubPushPayload:
    """订阅更新的一次推送：某个订阅的若干楼层（一条合并转发或一条合并直发消息）。"""

    mode: str  # "forward"（合并转发，每楼层一个节点）| "plain"（整批直发合并文本+图片）
    items: list[SubPushItem] = field(default_factory=list)


def fmt_sub_floor_text(title: str, op_name: str, floor: int, body: str, url: str) -> str:
    """订阅楼层节点文本：标题 / 正文 / 帖子链接。正文为空占位（无文本）。"""
    paragraph = str(body) if str(body).strip() else "(无文本)"
    return f"【{title}】{op_name} 更新 L{floor}\n{paragraph}\n{url}"


def fmt_floor_range(floors) -> str:
    """楼层范围展示：单个 L10；连续区间 L10-L12；非连续用、分隔（L10、L15）。"""
    fs = sorted({int(f) for f in floors})
    if not fs:
        return ""
    parts: list[str] = []
    start = prev = fs[0]
    for x in fs[1:]:
        if x == prev + 1:
            prev = x
            continue
        parts.append(f"L{start}" if start == prev else f"L{start}-L{prev}")
        start = prev = x
    parts.append(f"L{start}" if start == prev else f"L{start}-L{prev}")
    return "、".join(parts)


def fmt_sub_notice(title: str, op_name: str, floors) -> str:
    """订阅更新通知：标题 + 楼主 + 更新楼层范围（无链接，纯短文案）。"""
    return f"【{title}】{op_name} 更新了 {fmt_floor_range(floors)}"


def build_sub_payload(
    title: str, op_name: str, tid: int, floors: list, *, mode: str, text_max: int, image_max: int
) -> SubPushPayload:
    """将新楼层列表组装为订阅推送载荷（按 mode 截断正文/图片）。floors: list[PostFloor]。"""
    url = f"https://bbs.yamibo.com/thread-{tid}-1-1.html"
    items = [
        SubPushItem(
            floor=f.floor,
            name=op_name,
            text=fmt_sub_floor_text(title, op_name, f.floor, f.text[:text_max], url),
            image_urls=f.images[:image_max],
        )
        for f in floors
    ]
    return SubPushPayload(mode=mode, items=items)


def split_plain_batches(
    title: str, op_name: str, tid: int, floors: list, *, text_max: int, budget: int = PLAIN_BATCH_BUDGET
) -> list[list]:
    """plain 直发模式按合并文本字符预算拆分楼层批次（每批一条消息）。

    floor 的成本按 text_max 截断后的文本估算；单层超过预算时自成一批，
    保证不会发出必然超过平台消息长度限制的组合。
    """
    url = f"https://bbs.yamibo.com/thread-{tid}-1-1.html"
    batches: list[list] = []
    cur: list = []
    cur_len = 0
    for f in floors:
        cost = len(fmt_sub_floor_text(title, op_name, f.floor, f.text[:text_max], url)) + 2
        if cur and cur_len + cost > budget:
            batches.append(cur)
            cur = []
            cur_len = 0
        cur.append(f)
        cur_len += cost
    if cur:
        batches.append(cur)
    return batches


def is_aiocqhttp_target(context, umo: str) -> bool:
    """umo 目标是否运行在支持合并转发的平台（aiocqhttp）。

    经 context.get_platform_inst 匹配真实平台实例（平台 id 与适配器名可不同），
    拿不到实例（未知平台/context 为 None）一律视为不支持。
    """
    pid = str(umo).split(":", 1)[0]
    if not pid or context is None:
        return False
    try:
        platform = context.get_platform_inst(pid)
    except Exception:
        return False
    return platform is not None and platform.meta().name == "aiocqhttp"


def parse_tid_input(raw: str) -> int | None:
    raw = (raw or "").strip()
    if raw.isdigit():
        return int(raw)
    low = raw.lower()
    if "http" in low and "bbs.yamibo.com" not in low:
        return None
    if "bbs.yamibo.com" in low or "forum.php" in low or low.startswith("thread-"):
        m = TID_URL_RE.search(raw)
        if m:
            return int(m.group(1) or m.group(2))
    return None


def extract_yamibo_tids(text: str) -> list[int]:
    """仅提取论坛完整帖子 URL，保留出现顺序并按 tid 去重。"""
    tids = []
    for match in re.finditer(r"https?://[^\s<>\"'，。！？；、（）【】]+", unescape(text or ""), re.I):
        try:
            url = urlsplit(match.group().rstrip(").,;!]}"))
            if url.hostname != "bbs.yamibo.com" or url.username or url.password or url.port:
                continue
            pretty = re.fullmatch(r"/thread-([0-9]+)-[0-9]+-[0-9]+\.html", url.path)
            query = parse_qs(url.query)
            if pretty:
                raw = pretty.group(1)
            elif url.path == "/forum.php" and query.get("mod") == ["viewthread"]:
                values = query.get("tid", [])
                raw = values[0] if len(values) == 1 else ""
            else:
                continue
            if re.fullmatch(r"[0-9]{1,18}", raw) and int(raw) > 0 and int(raw) not in tids:
                tids.append(int(raw))
        except ValueError:
            continue
    return tids


def is_comic_thread(html: str) -> bool:
    """按导航中真实版块（含父版块）分类，忽略帖子标题及正文中的「漫画」。"""
    soup = BeautifulSoup(html, "html.parser")
    forum_names = []
    for link in soup.select("#pt a[href]"):
        try:
            url = urlsplit(link["href"])
            if url.scheme not in ("", "http", "https") or url.netloc not in ("", "bbs.yamibo.com"):
                continue
            query = parse_qs(url.query)
            path = url.path.lstrip("/")
            if re.fullmatch(r"forum-[0-9]+-[0-9]+\.html", path) or (
                path == "forum.php" and query.get("mod") == ["forumdisplay"]
                and re.fullmatch(r"[0-9]+", query.get("fid", [""])[0])
            ):
                forum_names.append(link.get_text(strip=True))
        except ValueError:
            continue
    return any("漫画" in name or "漫畫" in name for name in forum_names)


@dataclass
class AutoLinkPreview:
    """群链接的一条轻量预览，不生成 PDF/ZIP 或合并转发。"""

    text: str
    images: list[str] = field(default_factory=list)
    parsed: bool = True


def build_auto_preview(html: str, tid: int, config: dict) -> AutoLinkPreview | None:
    """自动预览只读 auto_parse 配置；主楼隐藏内容始终跳过。"""
    comic = is_comic_thread(html)
    kind = "comic" if comic else "novel"
    if not cfg_get(config, f"auto_parse.{kind}.enable", True):
        return None
    # 自动预览还排除正文外的隐藏附件，避免附件扫描重新带回受限图片。
    soup = BeautifulSoup(html, "html.parser")
    for locked in soup.select("#postlist .locked, #postlist blockquote[class*=lock]"):
        locked.decompose()
    thread = parse_thread(str(soup), tid, skip_hidden=True)
    if not thread.floors:
        return AutoLinkPreview("帖子不可见或需要更高权限", parsed=False)
    # Discuz 有时省略主楼编号（解析为 0），但不能把二楼当成主楼预览。
    first = next((floor for floor in thread.floors if floor.is_op), None)
    if first is None and thread.floors[0].floor == 0:
        first = thread.floors[0]
    title = f"【{thread.title.strip() or f'帖子 {tid}'}】"
    if first is None:
        return AutoLinkPreview(title)
    if comic:
        return AutoLinkPreview(title, first.images[:1])
    length = clamp_int(cfg_get(config, "auto_parse.novel.text_max_len", 50), 0, 500, 50)
    body = " ".join(first.text.split())
    if not body or not length:
        return AutoLinkPreview(title)
    summary = body[:length] + ("…" if len(body) > length else "")
    return AutoLinkPreview(f"{title}\n{summary}")


def should_auto_parse(event, config) -> bool:
    """无副作用的入口过滤，关闭时不唤醒 AstrBot 消息流程。"""
    if not cfg_get(config, "auto_parse.enable", False) or not event.get_group_id():
        return False
    whitelist = cfg_get(config, "auto_parse.group_whitelist", [])
    if not isinstance(whitelist, list):
        return False
    groups = {str(group).strip() for group in whitelist if str(group).strip()}
    if groups and str(event.get_group_id()).strip() not in groups:
        return False
    if not any(cfg_get(config, f"auto_parse.{kind}.enable", True) for kind in ("comic", "novel")):
        return False
    if str(event.get_sender_id()) == str(event.get_self_id()):
        return False
    text = event.get_message_str()
    # AstrBot 会去掉唤醒前缀；同时覆盖未剥离 / 的命令和 @ 机器人命令。
    if re.match(r"^\s*/?yamibo(?:\s|$)", text, re.I):
        return False
    if event.get_extra("handlers_parsed_params", {}):
        return False
    return bool(extract_yamibo_tids(text))


async def auto_parse_group_links(event, config, state: dict, client):
    """群聊自动入口；独立冷却、配置和预览，不调用手动命令。"""
    if client is None or not should_auto_parse(event, config):
        return
    tids = extract_yamibo_tids(event.get_message_str())
    # 群开启独立用户会话时，仍以平台实例 + 群号共享冷却。
    key = f"auto:{event.unified_msg_origin.split(':', 1)[0]}:{event.get_group_id()}"
    seconds = clamp_int(cfg_get(config, "auto_parse.cooldown_sec", 60), 0, 86400, 60)
    if not cooldown_ok(state, key, seconds):
        return
    limit = clamp_int(cfg_get(config, "auto_parse.max_links", 1), 1, 5, 1)
    for tid in tids[:limit]:
        html = await client.get_text(f"/forum.php?mod=viewthread&tid={tid}")
        if not client.is_logged_in(html):
            yield AutoLinkPreview("登录 cookie 已失效，请更新 auth/saltkey 后重试", parsed=False)
            return
        preview = build_auto_preview(html, tid, config)
        if preview is not None:
            yield preview


async def react_to_auto_parse_message(event, config) -> bool:
    """QQ 原消息表情回应；未开启或协议端不支持时不额外发送消息。"""
    if not cfg_get(config, "auto_parse.reaction.enable", False):
        return False
    if event.get_platform_name() != "aiocqhttp":
        return False
    bot = getattr(event, "bot", None)
    if not callable(getattr(bot, "call_action", None)):
        return False
    message_id = str(getattr(getattr(event, "message_obj", None), "message_id", "")).strip()
    emoji_id = str(cfg_get(config, "auto_parse.reaction.emoji_id", "128077")).strip()
    if not re.fullmatch(r"-?[0-9]+", message_id) or not re.fullmatch(r"[0-9]+", emoji_id):
        return False
    params = {"message_id": int(message_id), "emoji_id": emoji_id, "set": True}
    raw = getattr(event.message_obj, "raw_message", None)
    if isinstance(raw, dict) and raw.get("self_id"):
        params["self_id"] = raw["self_id"]
    await bot.call_action("set_msg_emoji_like", **params)
    return True


def cooldown_ok(state: dict, key: str, seconds: int, *, now: float | None = None) -> bool:
    now = now if now is not None else time.monotonic()
    if now - state.get(key, 0) < seconds:
        return False
    state[key] = now
    return True


class AsyncLockRegistry:
    """带容量上限的 asyncio.Lock 注册表（如按 tid 的互斥锁）。

    超限时只淘汰**未持有**的锁：持有中的锁不会被换出，否则同一 key
    的并发方会拿到不同锁对象绕过互斥。
    """

    def __init__(self, max_size: int = 64) -> None:
        self._max = max_size
        self._locks: dict[Any, asyncio.Lock] = {}

    def get(self, key) -> asyncio.Lock:
        lock = self._locks.get(key)
        if lock is None:
            if len(self._locks) >= self._max:
                # 超限时先淘汰一个未持有的锁（全部持有中则暂时允许扩容，
                # 绝不能淘汰刚创建的锁——否则同一 key 会拿到不同锁对象绕过互斥）
                for k, v in list(self._locks.items()):
                    if not v.locked():
                        del self._locks[k]
                        break
            lock = asyncio.Lock()
            self._locks[key] = lock
        return lock
