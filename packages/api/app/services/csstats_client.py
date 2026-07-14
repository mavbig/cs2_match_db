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

    def _headers(self, *, xhr: bool = False) -> dict[str, str]:
        headers = {
            "User-Agent": DEFAULT_USER_AGENT,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
        }
        if xhr:
            headers["X-Requested-With"] = "XMLHttpRequest"
            headers["Accept"] = "*/*"
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

    async def fetch_match_html(self, match_id: str) -> str:
        url = f"https://csstats.gg/match/{match_id}"
        await self._throttle()
        async with httpx.AsyncClient(timeout=self.timeout_s, follow_redirects=True) as client:
            resp = await client.get(url, headers=self._headers())
            resp.raise_for_status()
            text = resp.text
            if "Just a moment" in text or "cf-browser-verification" in text:
                raise RuntimeError(
                    "Cloudflare blocked the request. Paste your csstats Cookie into Settings."
                )
            return text

    async def fetch_profile_stats_html(self, steam64_id: str) -> str:
        url = f"https://csstats.gg/player/{steam64_id}/stats"
        referer = f"https://csstats.gg/player/{steam64_id}"
        await self._throttle()
        async with httpx.AsyncClient(timeout=self.timeout_s, follow_redirects=True) as client:
            headers = self._headers(xhr=True)
            headers["Referer"] = referer
            resp = await client.get(url, headers=headers)
            resp.raise_for_status()
            text = resp.text
            if "Please login to view player stats" in text:
                raise RuntimeError(
                    "csstats requires login. Paste your browser Cookie header into Settings."
                )
            if "Just a moment" in text:
                raise RuntimeError(
                    "Cloudflare blocked the request. Paste your csstats Cookie into Settings."
                )
            if "#player-matches" not in text and "window.location='/match/" not in text:
                logger.warning("csstats /stats response may be incomplete (%d bytes)", len(text))
            return text
