"""HTTP client for csstats.gg (requires browser session cookie for profile stats)."""

from __future__ import annotations

import asyncio
import logging
import time

import httpx

logger = logging.getLogger(__name__)

DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

CLOUDFLARE_BLOCKED_MSG = (
    "Cloudflare blocked the server request (403). csstats.gg often blocks datacenter IPs, "
    "and browser cookies (cf_clearance) are usually tied to your home IP. "
    "Use “Import from saved HTML” instead: open the match in Chrome, press Ctrl+S or "
    "DevTools → Network → the match request → Save response, then paste the HTML below."
)


class CsstatsFetchError(RuntimeError):
    pass


class CsstatsClient:
    def __init__(
        self,
        cookie: str | None = None,
        *,
        request_delay_ms: int = 1500,
        timeout_s: float = 120.0,
    ):
        self.cookie = (cookie or "").strip()
        self.request_delay_ms = request_delay_ms
        self.timeout_s = timeout_s
        self._last_request_at = 0.0

    def _headers(self, *, xhr: bool = False, referer: str | None = None) -> dict[str, str]:
        headers = {
            "User-Agent": DEFAULT_USER_AGENT,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
            "DNT": "1",
            "Upgrade-Insecure-Requests": "1",
        }
        if xhr:
            headers["X-Requested-With"] = "XMLHttpRequest"
            headers["Accept"] = "*/*"
            headers["Sec-Fetch-Dest"] = "empty"
            headers["Sec-Fetch-Mode"] = "cors"
            headers["Sec-Fetch-Site"] = "same-origin"
        else:
            headers["Sec-Fetch-Dest"] = "document"
            headers["Sec-Fetch-Mode"] = "navigate"
            headers["Sec-Fetch-Site"] = "none"
            headers["Sec-Fetch-User"] = "?1"
        if referer:
            headers["Referer"] = referer
            headers["Sec-Fetch-Site"] = "same-origin"
        if self.cookie:
            headers["Cookie"] = self.cookie
        return headers

    async def _throttle(self) -> None:
        if self.request_delay_ms <= 0:
            return
        now = time.monotonic()
        wait_s = self.request_delay_ms / 1000.0 - (now - self._last_request_at)
        if wait_s > 0:
            await asyncio.sleep(wait_s)
        self._last_request_at = time.monotonic()

    def _validate_response(self, text: str, *, profile_stats: bool = False) -> None:
        if "Just a moment" in text or "cf-browser-verification" in text:
            raise CsstatsFetchError(CLOUDFLARE_BLOCKED_MSG)
        if profile_stats and "Please login to view player stats" in text:
            raise CsstatsFetchError(
                "csstats requires login. Paste your browser Cookie header into Settings."
            )

    async def _get_text(self, url: str, headers: dict[str, str]) -> str:
        async with httpx.AsyncClient(timeout=self.timeout_s, follow_redirects=True) as client:
            resp = await client.get(url, headers=headers)
            if resp.status_code == 403:
                raise CsstatsFetchError(CLOUDFLARE_BLOCKED_MSG)
            resp.raise_for_status()
            return resp.text

    async def fetch_match_html(self, match_id: str) -> str:
        if not self.cookie:
            raise CsstatsFetchError(
                "csstats Cookie not configured. Paste it in Settings, or import from saved HTML."
            )

        url = f"https://csstats.gg/match/{match_id}"
        referer = "https://csstats.gg/"
        await self._throttle()
        text = await self._get_text(url, self._headers(referer=referer))
        self._validate_response(text)
        if "match-scoreboard" not in text:
            raise CsstatsFetchError(
                "csstats returned HTML without a scoreboard. Cookie may be expired — refresh it, "
                "or import from saved HTML."
            )
        return text

    async def fetch_profile_stats_html(self, steam64_id: str) -> str:
        if not self.cookie:
            raise CsstatsFetchError(
                "csstats Cookie not configured. Paste it from browser DevTools in Settings."
            )

        url = f"https://csstats.gg/player/{steam64_id}/stats"
        referer = f"https://csstats.gg/player/{steam64_id}"
        await self._throttle()
        text = await self._get_text(url, self._headers(xhr=True, referer=referer))
        self._validate_response(text, profile_stats=True)
        if "#player-matches" not in text and "window.location='/match/" not in text:
            logger.warning("csstats /stats response may be incomplete (%d bytes)", len(text))
        return text
