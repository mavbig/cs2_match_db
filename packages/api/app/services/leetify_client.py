import logging
from datetime import datetime
from urllib.parse import quote

import httpx

from app.config import settings

logger = logging.getLogger(__name__)

MATCHMAKING_DATA_SOURCES = (
    "matchmaking",
    "matchmaking_competitive",
    "renown",
)


class LeetifyClient:
    BASE = "https://api-public.cs-prod.leetify.com"

    def __init__(self, api_key: str | None = None):
        self.api_key = api_key or settings.leetify_api_key

    def _headers(self) -> dict:
        if not self.api_key:
            return {}
        return {
            "Authorization": f"Bearer {self.api_key}",
            "_leetify_key": self.api_key,
        }

    async def _get_json(self, path: str, *, params: dict | None = None) -> tuple[dict | list | None, int, str | None]:
        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.get(
                f"{self.BASE}{path}",
                params=params,
                headers=self._headers(),
            )
            if resp.status_code in (404, 401):
                return None, resp.status_code, None
            if resp.status_code >= 500:
                body = (resp.text or "")[:200]
                logger.warning("Leetify %s returned %s: %s", path, resp.status_code, body)
                return None, resp.status_code, body or "server error"
            if resp.status_code >= 400:
                return None, resp.status_code, resp.text[:200] if resp.text else None
            return resp.json(), resp.status_code, None

    async def get_profile(self, steam64_id: str) -> dict | None:
        data, _, _ = await self._get_json("/v3/profile", params={"steam64_id": steam64_id})
        return data if isinstance(data, dict) else None

    def _parse_matches_response(self, data) -> list[dict]:
        if isinstance(data, list):
            return data
        if isinstance(data, dict):
            matches = data.get("matches")
            if isinstance(matches, list):
                return matches
        return []

    async def _fetch_matches_page(
        self,
        *,
        steam64_id: str | None,
        leetify_id: str | None,
        offset: int = 0,
        before: str | None = None,
    ) -> list[dict]:
        param_sets: list[dict] = []
        base: dict = {}
        if leetify_id:
            base["id"] = leetify_id
        if steam64_id:
            base["steam64_id"] = steam64_id

        if before:
            param_sets.append({**base, "before": before})
            param_sets.append({**base, "finished_before": before})
            param_sets.append({**base, "end_date": before})
        if offset:
            param_sets.append({**base, "offset": offset, "limit": 100})
            param_sets.append({**base, "offset": offset})
        if not param_sets:
            param_sets.append(dict(base))

        for params in param_sets:
            data, status, _ = await self._get_json("/v3/profile/matches", params=params)
            if status == 401:
                return []
            page = self._parse_matches_response(data)
            if page:
                return page
        return []

    async def get_all_profile_matches(self, steam64_id: str) -> tuple[list[dict], dict]:
        profile = await self.get_profile(steam64_id)
        leetify_id = str(profile.get("id")) if profile and profile.get("id") else None
        total_on_profile = int(profile["total_matches"]) if profile and profile.get("total_matches") else None

        collected: list[dict] = []
        seen_ids: set[str] = set()

        def absorb(page: list[dict]) -> int:
            added = 0
            for entry in page:
                entry_id = str(entry.get("id") or "")
                if entry_id and entry_id in seen_ids:
                    continue
                if entry_id:
                    seen_ids.add(entry_id)
                collected.append(entry)
                added += 1
            return added

        offset = 0
        while offset < 50_000:
            page = await self._fetch_matches_page(
                steam64_id=steam64_id,
                leetify_id=leetify_id,
                offset=offset,
            )
            if not page:
                break
            added = absorb(page)
            logger.info(
                "Leetify profile/matches offset=%d: page=%d added=%d total=%d",
                offset,
                len(page),
                added,
                len(collected),
            )
            if added == 0 or len(page) < 100:
                break
            offset += 100

        if total_on_profile and len(collected) < total_on_profile and collected:
            sorted_entries = sorted(
                collected,
                key=lambda e: str(e.get("finished_at") or e.get("finishedAt") or ""),
            )
            before = sorted_entries[0].get("finished_at") or sorted_entries[0].get("finishedAt")
            for attempt in range(30):
                if not before:
                    break
                page = await self._fetch_matches_page(
                    steam64_id=steam64_id,
                    leetify_id=leetify_id,
                    before=str(before),
                )
                if not page:
                    break
                added = absorb(page)
                logger.info(
                    "Leetify profile/matches before=%s: page=%d added=%d total=%d",
                    before,
                    len(page),
                    added,
                    len(collected),
                )
                if added == 0:
                    break
                sorted_entries = sorted(
                    collected,
                    key=lambda e: str(e.get("finished_at") or e.get("finishedAt") or ""),
                )
                before = sorted_entries[0].get("finished_at") or sorted_entries[0].get("finishedAt")

        meta = {
            "profile_total_matches": total_on_profile,
            "leetify_user_id": leetify_id,
            "fetched": len(collected),
        }
        if total_on_profile and len(collected) < total_on_profile:
            meta["api_limit_note"] = (
                f"Leetify API returned {len(collected)} of {total_on_profile} matches on your profile. "
                "The public API may only expose your most recent games."
            )
        return collected, meta

    async def get_match_by_game_id(self, game_id: str) -> tuple[dict | None, str | None]:
        encoded_id = quote(game_id, safe="")
        data, status, err = await self._get_json(f"/v2/matches/{encoded_id}")
        if isinstance(data, dict):
            return data, None
        if status >= 500:
            return None, f"game_id lookup HTTP {status}"
        return None, None

    async def get_match_by_source(self, data_source: str, data_source_id: str) -> tuple[dict | None, str | None]:
        encoded_source = quote(data_source, safe="")
        encoded_id = quote(data_source_id, safe="")
        path = f"/v2/matches/{encoded_source}/{encoded_id}"
        data, status, err = await self._get_json(path)
        if isinstance(data, dict):
            return data, None
        if status >= 500:
            return None, f"{data_source} HTTP {status}"
        return None, None

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
    ) -> tuple[dict | None, str | None, list[str]]:
        notes: list[str] = []

        if leetify_game_id:
            data, err = await self.get_match_by_game_id(leetify_game_id)
            if data:
                return data, f"game_id:{leetify_game_id}", notes
            if err:
                notes.append(err)

        if my_steam64_id:
            history, _ = await self.get_all_profile_matches(my_steam64_id)
            if not history:
                notes.append("profile/matches unavailable")
            else:
                entry = _find_history_match(
                    history,
                    share_code=share_code,
                    source_match_id=source_match_id,
                    played_at=played_at,
                )
                if entry:
                    if entry.get("stats"):
                        return entry, "profile_matches", notes
                    game_id = entry.get("id")
                    if game_id:
                        data, err = await self.get_match_by_game_id(str(game_id))
                        if data:
                            return data, f"profile_game_id:{game_id}", notes
                        if err:
                            notes.append(err)
                    entry_source = entry.get("data_source")
                    entry_id = entry.get("data_source_match_id")
                    if entry_source and entry_id:
                        data, err = await self.get_match_by_source(str(entry_source), str(entry_id))
                        if data:
                            return data, f"{entry_source}:{entry_id}", notes
                        if err:
                            notes.append(err)

        if source == "faceit" and source_match_id:
            data, err = await self.get_match_by_source("faceit", source_match_id)
            if data:
                return data, f"faceit:{source_match_id}", notes
            if err:
                notes.append(err)

        if share_code:
            for data_source in _data_sources_for_mode(mode):
                data, err = await self.get_match_by_source(data_source, share_code)
                if data:
                    return data, f"{data_source}:{share_code}", notes
                if err:
                    notes.append(f"{data_source}: {err}")

        return None, None, notes


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

    def normalize_code(code: str | None) -> str | None:
        if not code:
            return None
        return str(code).strip().upper()

    share_norm = normalize_code(share_code)

    for entry in history:
        ds_id = entry.get("data_source_match_id")
        if share_norm and normalize_code(str(ds_id) if ds_id else None) == share_norm:
            return entry
        if source_match_id and ds_id and str(ds_id) == str(source_match_id):
            return entry

    if played_at is None:
        return None

    played_ts = played_at.timestamp() if hasattr(played_at, "timestamp") else None
    if played_ts is None:
        return None

    best: dict | None = None
    best_delta = 900.0
    for entry in history:
        finished = entry.get("finished_at") or entry.get("finishedAt")
        if not finished:
            continue
        try:
            entry_ts = datetime.fromisoformat(str(finished).replace("Z", "+00:00")).timestamp()
        except ValueError:
            continue
        delta = abs(entry_ts - played_ts)
        if delta < best_delta:
            best_delta = delta
            best = entry
    return best
