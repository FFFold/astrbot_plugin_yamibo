import asyncio
import re
import time
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from yamibo.parser import TZ

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
