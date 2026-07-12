import logging
from urllib.parse import quote

import httpx

from app.config import settings

logger = logging.getLogger(__name__)

MATCHMAKING_DATA_SOURCES = (
    "matchmaking_competitive",
    "matchmaking",
    "renown",
)


class LeetifyClient:
    BASE = "https://api-public.cs-prod.leetify.com"

    def __init__(self, api_key: str | None = None):
        self.api_key = api_key or settings.leetify_api_key

    def _headers(self) -> dict:
        headers = {}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        return headers

    async def get_profile(self, steam64_id: str) -> dict | None:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(
                f"{self.BASE}/v3/profile",
                params={"steam64_id": steam64_id},
                headers=self._headers(),
            )
            if resp.status_code in (404, 401):
                return None
            resp.raise_for_status()
            return resp.json()

    async def get_profile_matches(self, steam64_id: str) -> list[dict] | None:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(
                f"{self.BASE}/v3/profile/matches",
                params={"steam64_id": steam64_id},
                headers=self._headers(),
            )
            if resp.status_code in (404, 401):
                return None
            resp.raise_for_status()
            data = resp.json()
            if isinstance(data, list):
                return data
            if isinstance(data, dict):
                matches = data.get("matches")
                if isinstance(matches, list):
                    return matches
            return None

    async def get_match_by_game_id(self, game_id: str) -> dict | None:
        encoded_id = quote(game_id, safe="")
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(
                f"{self.BASE}/v2/matches/{encoded_id}",
                headers=self._headers(),
            )
            if resp.status_code in (404, 401):
                return None
            resp.raise_for_status()
            return resp.json()

    async def get_match_by_source(self, data_source: str, data_source_id: str) -> dict | None:
        encoded_source = quote(data_source, safe="")
        encoded_id = quote(data_source_id, safe="")
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(
                f"{self.BASE}/v2/matches/{encoded_source}/{encoded_id}",
                headers=self._headers(),
            )
            if resp.status_code in (404, 401):
                return None
            resp.raise_for_status()
            return resp.json()

    async def resolve_match(
        self,
        *,
        share_code: str | None,
        source_match_id: str | None,
        mode: str | None,
        source: str,
        my_steam64_id: str | None,
        leetify_game_id: str | None = None,
        played_at=None,
    ) -> tuple[dict | None, str | None]:
        if leetify_game_id:
            data = await self.get_match_by_game_id(leetify_game_id)
            if data:
                return data, f"game_id:{leetify_game_id}"

        if source == "faceit" and source_match_id:
            data = await self.get_match_by_source("faceit", source_match_id)
            if data:
                return data, f"faceit:{source_match_id}"

        if share_code:
            sources = _data_sources_for_mode(mode)
            for data_source in sources:
                data = await self.get_match_by_source(data_source, share_code)
                if data:
                    return data, f"{data_source}:{share_code}"

        if my_steam64_id:
            history = await self.get_profile_matches(my_steam64_id)
            entry = _find_history_match(
                history,
                share_code=share_code,
                source_match_id=source_match_id,
                played_at=played_at,
            )
            if entry:
                if entry.get("stats"):
                    return entry, "profile_matches"
                game_id = entry.get("id")
                if game_id:
                    data = await self.get_match_by_game_id(str(game_id))
                    if data:
                        return data, f"profile_game_id:{game_id}"

        return None, None


def _data_sources_for_mode(mode: str | None) -> tuple[str, ...]:
    normalized = (mode or "").lower()
    if normalized in ("premier", "competitive", "matchmaking_competitive"):
        return MATCHMAKING_DATA_SOURCES
    if normalized == "wingman":
        return ("matchmaking", "matchmaking_competitive", "renown")
    return MATCHMAKING_DATA_SOURCES


def _find_history_match(
    history: list[dict] | None,
    *,
    share_code: str | None,
    source_match_id: str | None,
    played_at,
) -> dict | None:
    if not history:
        return None

    for entry in history:
        ds_id = entry.get("data_source_match_id")
        if share_code and ds_id == share_code:
            return entry
        if source_match_id and ds_id and str(ds_id) == str(source_match_id):
            return entry

    if played_at is None:
        return None

    played_ts = played_at.timestamp() if hasattr(played_at, "timestamp") else None
    if played_ts is None:
        return None

    best: dict | None = None
    best_delta = 600.0
    for entry in history:
        finished = entry.get("finished_at") or entry.get("finishedAt")
        if not finished:
            continue
        try:
            from datetime import datetime

            entry_ts = datetime.fromisoformat(str(finished).replace("Z", "+00:00")).timestamp()
        except ValueError:
            continue
        delta = abs(entry_ts - played_ts)
        if delta < best_delta:
            best_delta = delta
            best = entry
    return best
