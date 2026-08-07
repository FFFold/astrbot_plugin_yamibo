import re

from bs4 import BeautifulSoup

from yamibo.models import HotItem, SignStatus, ThreadSummary

TID_RE = re.compile(r"thread-(\d+)-")
UID_RE = re.compile(r"space-uid-(\d+)\.html")
FORMHASH_RE = re.compile(r"formhash=([a-f0-9]{8})")


def extract_formhash(html: str) -> str | None:
    m = FORMHASH_RE.search(html)
    return m.group(1) if m else None


def parse_sign_status(html: str) -> SignStatus:
    if "今日已打卡" in html:
        return SignStatus(signed_today=True)
    return SignStatus(signed_today=False)


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
