import re
from datetime import datetime, timedelta, timezone

from bs4 import BeautifulSoup

from yamibo.models import HotItem, PostFloor, SignStatus, ThreadContent, ThreadSummary

TID_RE = re.compile(r"thread-(\d+)-")
UID_RE = re.compile(r"space-uid-(\d+)\.html")
FORMHASH_RE = re.compile(r"formhash=([a-f0-9]{8})")
RANK_CACHE_NEXT_RE = re.compile(r"下次将于\s*(\d{4})-(\d{1,2})-(\d{1,2})\s+(\d{1,2}):(\d{2})")
NOTICE_TEXT_RE = re.compile(r"未读提醒[^0-9]*(\d+)")
NOTICE_CLS_RE = re.compile(r'class="ntc_l"[^>]*>(\d+)')
TZ = timezone(timedelta(hours=8))  # 东八区固定偏移（中国无夏令时，避免依赖 tzdata 包）；scheduler 复用此常量

BBS_ORIGIN = "https://bbs.yamibo.com"
TIME_PAT_RE = re.compile(r"\d{4}-\d{1,2}-\d{1,2}(?: \d{1,2}:\d{2})?")
PADDING_TOKEN_RE = re.compile(r"[\s%^&*+~'`\"\-/\\@!?.,;:()\[\]<>_=|{}]+")


def _is_padding_line(line: str) -> bool:
    r"""识别 Discuz 反灌水随机字符填充行（如 `" Y3 N- A8 V/ \+ @ F% ^% V`）。

    判定：无 CJK 字符，且所有分词（按符号切分）长度均 <= 2。
    自然英文句子（单词较长）与含中文的行不会被误杀。
    """
    line = line.strip()
    if not line or len(line) < 5:
        return False
    if re.search(r"[\u4e00-\u9fff]", line):
        return False
    tokens = [t for t in PADDING_TOKEN_RE.split(line) if t]
    return bool(tokens) and all(len(t) <= 2 for t in tokens)


def _clean_padding(text: str) -> str:
    return "\n".join(ln for ln in text.splitlines() if not _is_padding_line(ln))


def extract_formhash(html: str) -> str | None:
    m = FORMHASH_RE.search(html)
    return m.group(1) if m else None


def parse_notice_count(html: str) -> int | None:
    """未读提醒数量：优先「未读提醒 N」文案，其次 class=ntc_l 徽标；均未匹配返回 None。"""
    for pattern in (NOTICE_TEXT_RE, NOTICE_CLS_RE):
        m = pattern.search(html or "")
        if m:
            return int(m.group(1))
    return None


def parse_sign_status(html: str) -> SignStatus:
    """判断今日是否已签到。

    注意：只能看 .signbtn 按钮区文本（「点击打卡」/「今日已打卡」），
    不能全页搜索——页面底部今日排行表中其他用户的「今日已打卡」会造成假阳性。
    """
    soup = BeautifulSoup(html, "html.parser")
    btn = soup.select_one(".signbtn")
    if btn:
        return SignStatus(signed_today="今日已打卡" in btn.get_text())
    return SignStatus(signed_today=False)


def _safe_int(text: str | None) -> int:
    m = re.search(r"\d+", text or "")
    return int(m.group()) if m else 0


def _tid_from_href(href: str) -> int | None:
    m = TID_RE.search(href or "")
    return int(m.group(1)) if m else None


def _uid_from_href(href: str) -> int | None:
    m = UID_RE.search(href or "")
    return int(m.group(1)) if m else None


def parse_hot_homepage(html: str) -> list[HotItem]:
    soup = BeautifulSoup(html, "html.parser")
    block = soup.select_one("#portal_block_52_content")
    items: list[HotItem] = []
    if not block:
        return items
    for li in block.select("ul li"):
        a = li.select_one("a")
        em = li.select_one("em")
        tid = _tid_from_href(a.get("href")) if a else None
        if tid:
            items.append(HotItem(tid=tid, title=a.get_text(strip=True), date=em.get_text(strip=True) if em else ""))
    return items


def parse_ranklist(html: str) -> list[HotItem]:
    soup = BeautifulSoup(html, "html.parser")
    items: list[HotItem] = []
    for tr in soup.select("div.tl table tr"):
        a = tr.select_one("th a")
        if not a:
            continue
        tid = _tid_from_href(a.get("href"))
        if not tid:
            continue
        cite = tr.select_one("td.by cite a")
        reply_td = tr.find_all("td")[-1]
        reply_text = reply_td.get_text(strip=True)
        items.append(
            HotItem(
                tid=tid,
                title=a.get_text(strip=True),
                author=cite.get_text(strip=True) if cite else "",
                reply_count=int(re.sub(r"\D", "", reply_text)) if re.search(r"\d", reply_text) else 0,
            )
        )
    return items


def extract_rank_cache_next(html: str) -> datetime | None:
    """解析排行榜页脚缓存提示「下次将于 YYYY-M-D HH:MM 进行更新」（东八区）。

    榜单数据被缓存、约 5 小时刷新一次；调度应 sleep 到这个时刻再抓。
    文案缺失或日期非法返回 None（调用方回退到兜底间隔）。
    """
    m = RANK_CACHE_NEXT_RE.search(html or "")
    if not m:
        return None
    try:
        return datetime(
            int(m.group(1)), int(m.group(2)), int(m.group(3)),
            int(m.group(4)), int(m.group(5)), tzinfo=TZ,
        )
    except ValueError:
        return None


def parse_forum_threads(html: str) -> list[ThreadSummary]:
    soup = BeautifulSoup(html, "html.parser")
    items: list[ThreadSummary] = []
    for tb in soup.select("#threadlisttableid tbody[id^=normalthread_]"):
        a = tb.select_one("a.xst")
        if not a:
            continue
        tid = _tid_from_href(a.get("href"))
        if not tid:
            continue
        cite = tb.select_one("td.by cite a")
        em = tb.select_one("td.by em span")
        items.append(
            ThreadSummary(
                tid=tid,
                title=a.get_text(strip=True),
                author=cite.get_text(strip=True) if cite else "",
                last_reply_time=em.get_text(strip=True) if em else "",
            )
        )
    return items


def _abs(url: str) -> str:
    if url.startswith("http"):
        return url
    return f"{BBS_ORIGIN}/{url.lstrip('/')}"


def parse_thread(html: str, tid: int, *, skip_hidden: bool = True) -> ThreadContent:
    soup = BeautifulSoup(html, "html.parser")
    title_el = soup.select_one("#thread_subject")
    title = title_el.get_text(strip=True) if title_el else ""
    posts = soup.select("#postlist div[id^=post_]")
    if not posts:
        return ThreadContent(tid=tid, title=title, author_uid=0, author_name="")
    first_floor: PostFloor | None = None
    floors: list[PostFloor] = []
    for post in posts:
        m = re.search(r"post_(\d+)", post.get("id", ""))
        if not m:
            continue
        pid = int(m.group(1))
        authi = post.select_one(f"#favatar{pid} .authi")
        uid_m = UID_RE.search(str(authi)) if authi else None
        author_name = authi.get_text(strip=True) if authi else ""
        num_el = post.select_one(f"#postnum{pid} em")
        floor = _safe_int(num_el.get_text()) if num_el else 0
        ap_el = post.select_one(f"#authorposton{pid}")
        time_str = ""
        if ap_el:
            m = TIME_PAT_RE.search(ap_el.get_text(" ", strip=True))
            time_str = m.group(0) if m else ""
        msg = post.select_one(f"#postmessage_{pid}")
        if not msg:
            continue
        if skip_hidden:
            for locked in msg.select(".locked, blockquote[class*=lock]"):
                locked.decompose()
        text = _clean_padding(msg.get_text("\n", strip=True))
        images: list[str] = []
        for img in msg.select("img"):
            src = img.get("zoomfile") or img.get("src")
            if src and not re.search(r"smiley|static/image|template/", src):
                images.append(_abs(src))
        for img in post.select(".pattl img"):
            src = img.get("zoomfile") or img.get("src")
            if src:
                images.append(_abs(src))
        pf = PostFloor(
            pid=pid, floor=floor, author_uid=int(uid_m.group(1)) if uid_m else 0,
            author_name=author_name, time=time_str, text=text, images=images, is_op=(floor == 1),
        )
        if floor == 1:
            first_floor = pf
        floors.append(pf)
    if first_floor is not None and not first_floor.is_op:
        first_floor.is_op = True
    return ThreadContent(
        tid=tid, title=title,
        author_uid=first_floor.author_uid if first_floor else 0,
        author_name=first_floor.author_name if first_floor else "",
        floors=floors,
    )


def parse_search_results(html: str) -> list[ThreadSummary]:
    """解析搜索结果。结果容器为 div#threadlist（class=slst mtw）。"""
    soup = BeautifulSoup(html, "html.parser")
    items: list[ThreadSummary] = []
    for li in soup.select("#threadlist li.pbw, #searchresult li.pbw"):
        a = li.select_one("h3 a")
        if not a:
            continue
        href = a.get("href") or ""
        m = re.search(r"tid=(\d+)", href)
        if not m:
            continue
        info = li.select_one("p.xg1")
        info_text = info.get_text() if info else ""
        reply_m = re.search(r"(\d+)\s*个回复", info_text)
        view_m = re.search(r"(\d+)\s*次查看", info_text)
        time_el = li.select_one("p span")
        items.append(
            ThreadSummary(
                tid=int(m.group(1)),
                title=a.get_text(strip=True),
                last_reply_time=time_el.get_text(strip=True) if time_el else "",
                reply_count=int(reply_m.group(1)) if reply_m else 0,
                view_count=int(view_m.group(1)) if view_m else 0,
            )
        )
    return items


def parse_my_records(html: str) -> dict:
    soup = BeautifulSoup(html, "html.parser")
    table = soup.select_one("table.dt.mtm")
    rows = table.find_all("tr") if table else []
    result = {"count": 0, "last_time": "", "last_reward": ""}
    if len(rows) > 1:
        cells = rows[1].find_all("td")
        if cells:
            result["count"] = max(0, len(rows) - 1)
            result["last_time"] = cells[0].get_text(strip=True)
            result["last_reward"] = cells[1].get_text(strip=True)
    return result
