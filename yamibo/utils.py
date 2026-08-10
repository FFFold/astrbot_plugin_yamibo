import asyncio
import re
import time
from collections.abc import Iterable
from datetime import datetime
from pathlib import Path
from typing import Any

from yamibo.parser import TZ

TID_URL_RE = re.compile(r"(?:thread-(\d+)-|tid=(\d+))")
TIME_RE = re.compile(r"(\d{4})-(\d{1,2})-(\d{1,2})")
DEFAULT_LIMIT = 15

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
            lock = asyncio.Lock()
            self._locks[key] = lock
        if len(self._locks) > self._max:
            for k, v in list(self._locks.items()):
                if not v.locked():
                    del self._locks[k]
                if len(self._locks) <= self._max:
                    break
        return lock
