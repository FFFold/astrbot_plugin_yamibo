import re
import time
from collections.abc import Iterable
from pathlib import Path
from typing import Any

TID_URL_RE = re.compile(r"(?:thread-(\d+)-|tid=(\d+))")
TIME_RE = re.compile(r"(\d{4})-(\d{1,2})-(\d{1,2})")
DEFAULT_LIMIT = 15


def cfg_get(config: dict, path: str, default: Any = None) -> Any:
    """按点号路径读取嵌套配置，如 cfg_get(config, "login.auth")。"""
    cur: Any = config
    for part in str(path).split("."):
        if not isinstance(cur, dict):
            return default
        cur = cur.get(part)
    return cur if cur is not None else default


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
    """论坛时间格式化为紧凑显示：当年 MM-DD，跨年 YYYY-MM-DD。"""
    m = TIME_RE.search(raw or "")
    if not m:
        return ""
    year, month, day = m.group(1), m.group(2).zfill(2), m.group(3).zfill(2)
    cur = time.strftime("%Y")
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
            reply = f"（回复 {it.reply_count}）" if getattr(it, "reply_count", 0) else ""
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
