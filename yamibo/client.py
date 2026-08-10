"""论坛传输层：aiohttp 会话、cookie、405 挑战求解重试、登录态检测。"""

import asyncio
import re
import time
from datetime import datetime

import aiohttp
from aiohttp import ClientTimeout
from yarl import URL

from yamibo.parser import BBS_ORIGIN, extract_formhash, extract_rank_cache_next, parse_ranklist, parse_sign_status
from yamibo.waf import WafSolver

UID_RE = re.compile(r"discuz_uid\s*=\s*'(\d+)'")
SCRIPT_SRC_RE = re.compile(r'<script[^>]+src="([^"]*nox[^"]*\.js)"')

TOKEN_TTL = 600  # 秒；观察有效期约 20-30 分钟，10 分钟预刷新
SOLVE_BACKOFF_SEC = 60  # 求解失败后的退避窗口；WAF 故障期间避免请求量放大


class ForumError(Exception):
    pass


class NotLoggedInError(ForumError):
    pass


class WafError(ForumError):
    pass


class ForumClient:
    def __init__(
        self,
        auth: str,
        saltkey: str,
        user_agent: str,
        *,
        proxy: str = "",
        timeout: float = 20,
        base_url: str = BBS_ORIGIN,
    ) -> None:
        self._auth = auth
        self._saltkey = saltkey
        self._user_agent = user_agent
        self._proxy = proxy
        self._timeout = timeout
        self._base = base_url
        self._session: aiohttp.ClientSession | None = None
        self._solver = WafSolver(user_agent)
        self._nox_token: str | None = None
        self._token_solved_at = 0.0
        self._solve_fail_at = 0.0
        self._token_lock = asyncio.Lock()

    @property
    def base_url(self) -> str:
        return self._base

    @property
    def session(self) -> aiohttp.ClientSession | None:
        return self._session

    async def start(self) -> None:
        jar = aiohttp.CookieJar(unsafe=True)
        headers = {
            "User-Agent": self._user_agent,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            "Referer": self._base + "/",
        }
        timeout = ClientTimeout(total=self._timeout)
        self._session = aiohttp.ClientSession(
            cookie_jar=jar, headers=headers, timeout=timeout,
            raise_for_status=False,
        )
        if self._proxy:
            self._session._default_proxy = self._proxy  # type: ignore[attr-defined]
        self._apply_static_cookies()
        try:
            await self.refresh_token()
        except ForumError:
            pass  # 首次失败不致命，请求时会重试

    async def close(self) -> None:
        if self._session:
            await self._session.close()
            self._session = None

    def set_manual_token(self, token: str) -> None:
        """手动提供 nox token 兜底（WAF 求解失败时）。"""
        token = (token or "").strip()
        if token:
            self._nox_token = token
            self._token_solved_at = time.monotonic()
            self._apply_static_cookies()

    def _apply_static_cookies(self) -> None:
        cookies = {
            "EeqY_2132_auth": self._auth,
            "EeqY_2132_saltkey": self._saltkey,
        }
        if self._nox_token:
            cookies["nox_jst_v1"] = self._nox_token
        assert self._session is not None
        self._session.cookie_jar.update_cookies(cookies, response_url=URL(self._base))

    async def refresh_token(self) -> bool:
        """解一次挑战，更新 nox_jst_v1。

        返回是否持有（新）token。多调度循环并发调用时共用一把锁，只有一个协程真正求解；
        求解失败记录失败时刻，退避窗口内不再重试，避免 WAF 故障期间请求量放大。
        """
        async with self._token_lock:
            if self._nox_token is not None and time.monotonic() - self._token_solved_at <= TOKEN_TTL:
                return True
            if time.monotonic() - self._solve_fail_at < SOLVE_BACKOFF_SEC:
                return self._nox_token is not None
            token = await self._solve()
            if token:
                self._nox_token = token
                self._token_solved_at = time.monotonic()
                self._apply_static_cookies()
            else:
                self._solve_fail_at = time.monotonic()
            return token is not None

    async def _solve(self) -> str | None:
        assert self._session is not None
        try:
            resp = await self._session.get(self._base + "/")
            text = await resp.text()
        except Exception:
            return None
        m = SCRIPT_SRC_RE.search(text)
        if not m:
            return None
        script_url = self._base + m.group(1)
        try:
            resp2 = await self._session.get(script_url)
            script = await resp2.text()
        except Exception:
            return None
        return self._solver.solve_script(script)

    async def _read_text(self, resp: aiohttp.ClientResponse) -> str:
        raw = await resp.read()
        if resp.charset:
            return raw.decode(resp.charset, errors="replace")
        for enc in ("utf-8", "gbk"):
            try:
                return raw.decode(enc)
            except UnicodeDecodeError:
                continue
        return raw.decode("utf-8", errors="replace")

    async def get_text(self, path: str, *, retry_waf: bool = True) -> str:
        assert self._session is not None
        if self._nox_token is None or time.monotonic() - self._token_solved_at > TOKEN_TTL:
            await self.refresh_token()
        url = path if path.startswith("http") else self._base + path
        resp = await self._session.get(url)
        if resp.status == 405 and retry_waf:
            refreshed = await self.refresh_token()
            if not refreshed:
                raise WafError("WAF 挑战求解失败（退避中），请稍后再试或配置 manual_nox_token 兜底")
            resp = await self._session.get(url)
            if resp.status == 405:
                raise WafError("WAF challenge 求解后仍返回 405，cookie 可能已失效")
        return await self._read_text(resp)

    @staticmethod
    def is_logged_in(html: str) -> bool:
        m = UID_RE.search(html)
        return bool(m and m.group(1) != "0")

    @staticmethod
    def ensure_logged_in(html: str) -> None:
        if not ForumClient.is_logged_in(html):
            raise NotLoggedInError("cookie 无效或已过期（discuz_uid=0），请在插件配置中重新填写 saltkey/auth")

    async def get_formhash(self, path: str = "/plugin.php?id=zqlj_sign") -> str | None:
        html = await self.get_text(path)
        return extract_formhash(html)

    async def get_sign_status(self) -> tuple:
        """返回 (html, SignStatus)。"""
        html = await self.get_text("/plugin.php?id=zqlj_sign")
        return html, parse_sign_status(html)

    async def sign(self, html: str | None = None) -> None:
        """执行打卡。需先取签到页拿 formhash。"""
        if html is None:
            html = await self.get_text("/plugin.php?id=zqlj_sign")
        formhash = extract_formhash(html)
        if not formhash:
            raise ForumError("签到页未找到 formhash")
        await self.get_text(f"/plugin.php?id=zqlj_sign&sign={formhash}")

    async def get_hot_rank(self, n: int = 10) -> tuple[list, datetime | None]:
        """今日热度榜（view=heats&orderby=today）。

        返回 (Top-N 帖子, 下次缓存刷新时间或 None)。
        页面页脚标注榜单缓存周期（约 5h），调度可据此对齐抓取时机。
        cookie 失效时页面渲染登录提示、榜单为空，先检查登录态避免误判为空榜。
        """
        html = await self.get_text("/misc.php?mod=ranklist&type=thread&view=heats&orderby=today")
        self.ensure_logged_in(html)
        return parse_ranklist(html)[:n], extract_rank_cache_next(html)

    async def get_hot_threads(self, n: int = 10) -> list:
        """今日热度榜热帖（仅列表，供手动指令使用）。"""
        items, _ = await self.get_hot_rank(n)
        return items

    async def get_thread_author_view(self, tid: int, author_uid: int) -> str:
        """只看楼主视图（倒序，楼主最新楼层在前）。"""
        return await self.get_text(
            f"/forum.php?mod=viewthread&tid={tid}&authorid={author_uid}&ordertype=1"
        )
