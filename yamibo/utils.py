import re
import time

TID_URL_RE = re.compile(r"(?:thread-(\d+)-|tid=(\d+))")


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
