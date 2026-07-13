import asyncio
import json
import logging
import time
from datetime import datetime, timedelta, timezone
from urllib.parse import quote

import httpx

from app.config import settings

logger = logging.getLogger(__name__)

MATCHMAKING_DATA_SOURCES = (
    "matchmaking",
    "matchmaking_competitive",
    "renown",
)

HISTORY_PAGE_CAP = 120


def _day_start(dt: datetime) -> datetime:
    dt = dt.astimezone(timezone.utc)
    return dt.replace(hour=0, minute=0, second=0, microsecond=0)


def _day_end(dt: datetime) -> datetime:
    dt = dt.astimezone(timezone.utc)
    return dt.replace(hour=23, minute=59, second=59, microsecond=999000)


def _format_period_start(dt: datetime) -> str:
    return _day_start(dt).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def _format_period_end(dt: datetime) -> str:
    return _day_end(dt).strftime("%Y-%m-%dT%H:%M:%S.999Z")


def _parse_finished_at(game: dict) -> datetime | None:
    finished = game.get("finished_at") or game.get("finishedAt")
    if not finished:
        return None
    try:
        return datetime.fromisoformat(str(finished).replace("Z", "+00:00")).astimezone(timezone.utc)
    except ValueError:
        return None


class LeetifyClient:
    PUBLIC_BASE = "https://api-public.cs-prod.leetify.com"
    INTERNAL_BASE = "https://api.cs-prod.leetify.com"

    def __init__(self, api_key: str | None = None, session_token: str | None = None):
        self.api_key = api_key or settings.leetify_api_key
        self.session_token = session_token
        self._last_request_at = 0.0
        self._profile_matches_cache: list[dict] | None = None

    @property
    def _min_delay_s(self) -> float:
        return max(settings.leetify_request_delay_ms, 100) / 1000.0

    async def _throttle(self) -> None:
        elapsed = time.monotonic() - self._last_request_at
        if elapsed < self._min_delay_s:
            await asyncio.sleep(self._min_delay_s - elapsed)

    def _headers(self) -> dict:
        if not self.api_key:
            return {}
        return {
            "Authorization": f"Bearer {self.api_key}",
            "_leetify_key": self.api_key,
        }

    def _internal_headers(self) -> dict:
        headers = {
            "Accept": "application/json",
            "Origin": "https://leetify.com",
            "Referer": "https://leetify.com/",
        }
        if not self.session_token:
            return headers

        token = self.session_token.strip()
        if "=" in token and not token.lower().startswith("bearer "):
            headers["Cookie"] = token
            return headers

        if token.lower().startswith("bearer "):
            headers["Authorization"] = token
        else:
            headers["Authorization"] = f"Bearer {token}"
        return headers

    async def _request_json(
        self,
        base: str,
        path: str,
        *,
        params: dict | None = None,
        internal: bool = False,
    ) -> tuple[dict | list | None, int, str | None]:
        headers = self._internal_headers() if internal else self._headers()

        for attempt in range(4):
            await self._throttle()
            async with httpx.AsyncClient(timeout=90) as client:
                resp = await client.get(
                    f"{base}{path}",
                    params=params,
                    headers=headers,
                )
            self._last_request_at = time.monotonic()

            if resp.status_code == 429:
                wait_s = min(30.0, 2.0 ** attempt)
                logger.warning("Leetify rate limited on %s — waiting %.0fs", path, wait_s)
                await asyncio.sleep(wait_s)
                continue

            if resp.status_code in (404, 401, 403):
                return None, resp.status_code, (resp.text or "")[:200] or None
            if resp.status_code >= 500:
                body = (resp.text or "")[:200]
                logger.warning("Leetify %s%s returned %s: %s", base, path, resp.status_code, body)
                return None, resp.status_code, body or "server error"
            if resp.status_code >= 400:
                return None, resp.status_code, resp.text[:200] if resp.text else None
            return resp.json(), resp.status_code, None

        return None, 429, "rate limited"

    async def _get_public_json(self, path: str, *, params: dict | None = None):
        return await self._request_json(self.PUBLIC_BASE, path, params=params)

    async def _get_internal_json(self, path: str, *, params: dict | None = None):
        return await self._request_json(self.INTERNAL_BASE, path, params=params, internal=True)

    async def get_profile(self, steam64_id: str) -> dict | None:
        data, _, _ = await self._get_public_json("/v3/profile", params={"steam64_id": steam64_id})
        return data if isinstance(data, dict) else None

    def _parse_matches_response(self, data) -> list[dict]:
        if isinstance(data, list):
            return data
        if isinstance(data, dict):
            for key in ("matches", "games", "data", "items", "results"):
                matches = data.get(key)
                if isinstance(matches, list):
                    return matches
        return []

    async def get_profile_matches(
        self,
        steam64_id: str,
        *,
        limit: int | None = None,
        offset: int = 0,
        before: str | None = None,
    ) -> list[dict] | None:
        params: dict = {"steam64_id": steam64_id}
        if limit is not None:
            params["limit"] = limit
        if offset:
            params["offset"] = offset
        if before:
            params["before"] = before

        data, status, err = await self._get_public_json("/v3/profile/matches", params=params)
        if data is None:
            if status == 429:
                logger.warning("Leetify profile/matches rate limited for %s", steam64_id)
            elif status >= 500:
                logger.warning("Leetify profile/matches failed for %s: %s", steam64_id, err)
            return None

        return self._parse_matches_response(data)

    async def get_games_history(
        self,
        current_start: datetime,
        current_end: datetime,
        previous_start: datetime,
        previous_end: datetime,
    ) -> tuple[list[dict] | None, int | None]:
        filters = {
            "currentPeriod": {
                "start": _format_period_start(current_start),
                "end": _format_period_end(current_end),
            },
            "previousPeriod": {
                "start": _format_period_start(previous_start),
                "end": _format_period_end(previous_end),
            },
            "isPeriodSetManually": True,
        }
        data, status, err = await self._get_internal_json(
            "/api/v2/games/history",
            params={"filters": json.dumps(filters, separators=(",", ":"))},
        )
        if data is None:
            if status in (401, 403):
                if not self.session_token:
                    logger.info(
                        "Leetify games/history requires a browser session token (HTTP %s). "
                        "Add it in Settings.",
                        status,
                    )
                else:
                    logger.warning("Leetify games/history rejected session token (HTTP %s)", status)
            elif status == 429:
                logger.warning("Leetify games/history rate limited")
            elif status >= 500:
                logger.warning("Leetify games/history failed: %s", err)
            return None, status
        return self._parse_matches_response(data), None

    def _shift_history_windows_back(
        self,
        current_start: datetime,
        window_days: int,
    ) -> tuple[datetime, datetime, datetime, datetime]:
        previous_end = _day_end(current_start - timedelta(days=1))
        previous_start = _day_start(previous_end - timedelta(days=window_days))
        current_end = previous_end
        current_start = previous_start
        return current_start, current_end, previous_start, previous_end

    async def get_all_games_history(
        self,
        *,
        earliest: datetime | None = None,
    ) -> tuple[list[dict], dict]:
        window_days = settings.leetify_history_window_days
        max_requests = settings.leetify_history_max_requests
        years_back = max(1, settings.leetify_history_months // 12)
        default_earliest = _day_start(datetime.now(timezone.utc) - timedelta(days=365 * years_back))
        earliest_bound = _day_start(earliest) if earliest else default_earliest

        collected: list[dict] = []
        seen_ids: set[str] = set()
        history_auth_failed = False
        requests = 0
        stagnant_windows = 0

        now = datetime.now(timezone.utc)
        current_end = _day_end(now)
        current_start = _day_start(current_end - timedelta(days=window_days))
        previous_end = _day_end(current_start - timedelta(days=1))
        previous_start = _day_start(previous_end - timedelta(days=window_days))

        while requests < max_requests and current_end >= earliest_bound:
            page, status = await self.get_games_history(
                current_start,
                current_end,
                previous_start,
                previous_end,
            )
            requests += 1

            if page is None:
                if status in (401, 403):
                    history_auth_failed = True
                if not collected:
                    return [], {
                        "history_available": False,
                        "history_windows": requests,
                        "history_auth_required": not bool(self.session_token) or history_auth_failed,
                        "history_auth_failed": history_auth_failed,
                    }
                break

            if not page:
                stagnant_windows += 1
                if stagnant_windows >= 3:
                    break
                current_start, current_end, previous_start, previous_end = self._shift_history_windows_back(
                    current_start,
                    window_days,
                )
                continue

            stagnant_windows = 0
            added = 0
            oldest_in_page: datetime | None = None
            for game in page:
                finished = _parse_finished_at(game)
                if finished and (oldest_in_page is None or finished < oldest_in_page):
                    oldest_in_page = finished

                game_id = str(game.get("id") or "")
                if game_id and game_id in seen_ids:
                    continue
                if game_id:
                    seen_ids.add(game_id)
                collected.append(game)
                added += 1

            logger.info(
                "Leetify games/history %s..%s / prev %s..%s: page=%d added=%d total=%d",
                current_start.date(),
                current_end.date(),
                previous_start.date(),
                previous_end.date(),
                len(page),
                added,
                len(collected),
            )

            if len(page) >= HISTORY_PAGE_CAP and oldest_in_page and oldest_in_page > earliest_bound:
                # API caps at 120 games — walk further back inside this window.
                current_end = _day_end(oldest_in_page - timedelta(seconds=1))
                if current_end < current_start:
                    current_start, current_end, previous_start, previous_end = self._shift_history_windows_back(
                        current_start,
                        window_days,
                    )
                continue

            if added == 0:
                current_start, current_end, previous_start, previous_end = self._shift_history_windows_back(
                    current_start,
                    window_days,
                )
                continue

            current_start, current_end, previous_start, previous_end = self._shift_history_windows_back(
                current_start,
                window_days,
            )

        if not collected:
            return [], {
                "history_available": bool(self.session_token) and not history_auth_failed,
                "history_windows": requests,
                "history_auth_required": not bool(self.session_token) or history_auth_failed,
                "history_auth_failed": history_auth_failed,
            }

        return collected, {
            "history_available": True,
            "history_windows": requests,
            "fetched": len(collected),
            "import_source": "games_history",
        }

    async def get_all_profile_matches(self, steam64_id: str) -> tuple[list[dict], dict]:
        profile = await self.get_profile(steam64_id)
        total_on_profile = int(profile["total_matches"]) if profile and profile.get("total_matches") else None
        earliest = None
        if profile and profile.get("first_match_date"):
            try:
                earliest = datetime.fromisoformat(
                    str(profile["first_match_date"]).replace("Z", "+00:00")
                ).astimezone(timezone.utc)
            except ValueError:
                earliest = None

        history_games, history_meta = await self.get_all_games_history(earliest=earliest)
        if history_games:
            meta = {
                "profile_total_matches": total_on_profile,
                "leetify_user_id": str(profile.get("id")) if profile and profile.get("id") else None,
                **history_meta,
            }
            if total_on_profile and len(history_games) < total_on_profile:
                meta["api_limit_note"] = (
                    f"Fetched {len(history_games)} of {total_on_profile} Leetify games "
                    f"via {history_meta.get('history_windows', '?')} history requests."
                )
            return history_games, meta

        if history_meta.get("history_auth_required"):
            error_note = (
                "Session token was rejected — copy a fresh Authorization header from leetify.com."
                if history_meta.get("history_auth_failed")
                else None
            )
            return [], {
                "profile_total_matches": total_on_profile,
                "history_auth_required": True,
                "history_auth_failed": history_meta.get("history_auth_failed", False),
                "import_source": "none",
                "api_limit_note": error_note,
            }

        page = await self.get_profile_matches(steam64_id, limit=100, offset=0)
        collected = page or []

        meta = {
            "profile_total_matches": total_on_profile,
            "leetify_user_id": str(profile.get("id")) if profile and profile.get("id") else None,
            "fetched": len(collected),
            "import_source": "profile_matches",
            "history_auth_required": False,
        }
        if total_on_profile and len(collected) < total_on_profile:
            meta["api_limit_note"] = (
                f"Leetify public API returned {len(collected)} of {total_on_profile} matches. "
                "Add a session token in Settings for full history."
            )
        return collected, meta

    async def get_match_by_game_id(self, game_id: str) -> tuple[dict | None, str | None]:
        encoded_id = quote(game_id, safe="")
        data, status, err = await self._get_public_json(f"/v2/matches/{encoded_id}")
        if isinstance(data, dict):
            return data, None
        if status == 429:
            return None, "rate limited"
        if status >= 500:
            return None, f"game_id lookup HTTP {status}"
        return None, None

    async def get_match_by_source(self, data_source: str, data_source_id: str) -> tuple[dict | None, str | None]:
        encoded_source = quote(data_source, safe="")
        encoded_id = quote(data_source_id, safe="")
        path = f"/v2/matches/{encoded_source}/{encoded_id}"
        data, status, err = await self._get_public_json(path)
        if isinstance(data, dict):
            return data, None
        if status == 429:
            return None, "rate limited"
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
        ds_id = entry.get("data_source_match_id") or entry.get("dataSourceMatchId")
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
