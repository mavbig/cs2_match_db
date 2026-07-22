import asyncio
import json
import logging
import re
from datetime import datetime, timezone
from uuid import UUID

import httpx
from arq import cron
from arq.connections import RedisSettings
from arq.worker import func
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from config import settings

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("worker")

engine = create_async_engine(settings.database_url)
Session = async_sessionmaker(engine, expire_on_commit=False)

FACEIT_BASE = "https://open.faceit.com/data/v4"
FACEIT_REQUEST_TIMEOUT_S = 15.0
FACEIT_MATCH_DELAY_S = 0.2
FACEIT_COMMIT_EVERY = 10


async def _get_setting(session: AsyncSession, key: str) -> str | None:
    from sqlalchemy import text

    result = await session.execute(text("SELECT value FROM app_settings WHERE key = :k"), {"k": key})
    row = result.fetchone()
    return row[0] if row else None


def _parse_stat_int(value) -> int | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return int(value)
    m = re.search(r"\d+", str(value))
    return int(m.group()) if m else None


def _get_player_stat(player_stats: dict, *keys: str):
    if not player_stats:
        return None
    lowered = {str(k).lower(): v for k, v in player_stats.items()}
    for key in keys:
        val = lowered.get(key.lower())
        if val is not None and str(val).strip() != "":
            return val
    return None


def _parse_faceit_ping(player_stats: dict) -> int | None:
    raw = _get_player_stat(
        player_stats,
        "Ping",
        "Average Ping",
        "Avg Ping",
        "Avg. Ping",
        "Average ping",
    )
    return _parse_stat_int(raw)


def _parse_faceit_tab_score(
    player_stats: dict,
    kills: int | None,
    assists: int | None,
    mvps: int | None,
) -> int | None:
    raw = _get_player_stat(player_stats, "Score", "Points", "Tab Score", "Match Score")
    if raw is not None:
        text = str(raw).strip()
        # FACEIT sometimes uses "Score" for team round score (e.g. "13 / 11") — skip that.
        if "/" not in text:
            parsed = _parse_stat_int(text)
            if parsed is not None:
                return parsed

    if kills is None and assists is None and mvps is None:
        return None

    # Approximate Valve tab score when FACEIT does not expose per-player points.
    return (kills or 0) * 2 + (assists or 0) + (mvps or 0)


def _parse_headshot_pct(value) -> float | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    m = re.search(r"([\d.]+)", str(value))
    return float(m.group(1)) if m else None


def _parse_stat_float(value) -> float | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    m = re.search(r"([\d.]+)", str(value).replace(",", "."))
    return float(m.group(1)) if m else None


def _get_player_stat_substring(player_stats: dict, needle: str):
    if not player_stats:
        return None
    target = needle.lower()
    for key, value in player_stats.items():
        if value is None or str(value).strip() == "":
            continue
        if target in str(key).lower():
            return value
    return None


def _normalize_rate_pct(value) -> float | None:
    parsed = _parse_stat_float(value)
    if parsed is None:
        return None
    if 0 < parsed <= 1:
        return round(parsed * 100, 2)
    return parsed


def _extract_faceit_kr(stats: dict) -> float | None:
    value = _get_player_stat(
        stats,
        "Average K/R Ratio",
        "K/R Ratio",
        "Average KR",
        "Average K/R",
        "KPR",
        "K/R",
        "Average Kills per Round",
        "Kills per Round",
        "Kills Per Round",
    )
    if value is None:
        value = _get_player_stat_substring(stats, "k/r ratio")
    if value is None:
        value = _get_player_stat_substring(stats, "kills per round")
    return _parse_stat_float(value)


def _enrich_faceit_stat_block(block: dict) -> dict:
    matches = block.get("matches") or block.get("match_count")
    if matches:
        try:
            match_count = float(matches)
        except (TypeError, ValueError):
            match_count = 0
    else:
        match_count = 0

    if match_count > 0:
        if block.get("avg_kills") is None and block.get("total_kills") is not None:
            block["avg_kills"] = round(float(block["total_kills"]) / match_count, 2)
        if block.get("avg_deaths") is None and block.get("total_deaths") is not None:
            block["avg_deaths"] = round(float(block["total_deaths"]) / match_count, 2)
        if block.get("avg_assists") is None and block.get("total_assists") is not None:
            block["avg_assists"] = round(float(block["total_assists"]) / match_count, 2)

    rounds = block.get("rounds")
    kills = block.get("total_kills")
    if block.get("kr") is None and kills is not None and rounds:
        try:
            round_count = float(rounds)
            if round_count > 0:
                block["kr"] = round(float(kills) / round_count, 2)
        except (TypeError, ValueError, ZeroDivisionError):
            pass

    for key in ("win_rate_pct", "hs_pct", "entry_success_pct", "kast_pct"):
        if block.get(key) is not None:
            block[key] = _normalize_rate_pct(block[key])

    return block


def _normalize_faceit_lifetime(lifetime: dict) -> dict:
    block = {
        "matches": _parse_stat_int(
            _get_player_stat(lifetime, "Total Matches", "Matches", "Number of Matches", "Games")
        ),
        "win_rate_pct": _parse_stat_float(_get_player_stat(lifetime, "Win Rate %", "Win Rate")),
        "kd": _parse_stat_float(
            _get_player_stat(lifetime, "Average K/D Ratio", "Average K/D", "K/D Ratio", "KDR", "Average KDR")
        ),
        "kr": _extract_faceit_kr(lifetime),
        "adr": _parse_stat_float(
            _get_player_stat(lifetime, "ADR", "Average Damage per Round", "Damage / Round", "Average ADR")
        ),
        "hs_pct": _parse_stat_float(
            _get_player_stat(lifetime, "Average Headshots %", "Headshots %", "Average Headshots", "Headshot %")
        ),
        "avg_kills": _parse_stat_float(
            _get_player_stat(
                lifetime,
                "Average Kills",
                "Avg Kills",
                "Kills / Match",
                "Kills per Match",
                "Average Kills per Match",
            )
        ),
        "avg_deaths": _parse_stat_float(
            _get_player_stat(
                lifetime,
                "Average Deaths",
                "Avg Deaths",
                "Deaths / Match",
                "Deaths per Match",
                "Average Deaths per Match",
            )
        ),
        "avg_assists": _parse_stat_float(
            _get_player_stat(
                lifetime,
                "Average Assists",
                "Avg Assists",
                "Assists / Match",
                "Assists per Match",
            )
        ),
        "entry_success_pct": _parse_stat_float(
            _get_player_stat(
                lifetime,
                "Entry Success Rate",
                "Entry Rate",
                "Entry Success Rate %",
                "First Entry Success Rate",
                "Entry Success %",
            )
            or _get_player_stat_substring(lifetime, "entry success")
        ),
        "kast_pct": _parse_stat_float(_get_player_stat(lifetime, "KAST", "Average KAST", "KAST %")),
        "total_kills": _parse_stat_float(_get_player_stat(lifetime, "Total Kills", "Kills", "Kill Count")),
        "total_deaths": _parse_stat_float(_get_player_stat(lifetime, "Total Deaths", "Deaths")),
        "total_assists": _parse_stat_float(_get_player_stat(lifetime, "Total Assists", "Assists")),
        "rounds": _parse_stat_float(
            _get_player_stat(lifetime, "Rounds", "Total Rounds", "Rounds Played", "Total Rounds Played")
            or _get_player_stat_substring(lifetime, "rounds")
        ),
    }
    enriched = _enrich_faceit_stat_block(block)
    enriched.pop("total_kills", None)
    enriched.pop("total_deaths", None)
    enriched.pop("total_assists", None)
    enriched.pop("rounds", None)
    return enriched


def _aggregate_faceit_recent(items: list, limit: int = 20) -> dict:
    samples = [(item.get("stats") or {}) for item in items[:limit] if item.get("stats")]
    if not samples:
        return {"match_count": 0}

    def _avg(*keys: str, as_pct: bool = False) -> float | None:
        vals = []
        for sample in samples:
            raw = _get_player_stat(sample, *keys)
            parsed = _normalize_rate_pct(raw) if as_pct else _parse_stat_float(raw)
            if parsed is not None:
                vals.append(parsed)
        return round(sum(vals) / len(vals), 2) if vals else None

    block = {
        "match_count": len(samples),
        "kd": _avg("K/D Ratio", "KDR", "Average K/D Ratio"),
        "kr": _avg(
            "Average K/R Ratio",
            "K/R Ratio",
            "Average K/R",
            "K/R",
            "Average Kills per Round",
            "Kills per Round",
        ),
        "adr": _avg("ADR", "Average Damage per Round", "Damage / Round"),
        "hs_pct": _avg("Headshots %", "Average Headshots %", "Average Headshots", "Headshot %", as_pct=True),
        "avg_kills": _avg("Kills", "Average Kills"),
        "avg_deaths": _avg("Deaths", "Average Deaths"),
        "avg_assists": _avg("Assists", "Average Assists"),
        "entry_success_pct": _avg(
            "Entry Success Rate",
            "Entry Rate",
            "First Entry Success Rate",
            as_pct=True,
        ),
        "kast_pct": _avg("KAST", "Average KAST", as_pct=True),
    }
    return _enrich_faceit_stat_block(block)


def _compute_faceit_flags(lifetime: dict, recent: dict, bans: list) -> list[dict]:
    flags: list[dict] = []

    if bans:
        latest = bans[0]
        reason = latest.get("reason") or "unknown reason"
        flags.append(
            {
                "severity": "high",
                "label": "FACEIT ban on record",
                "detail": f"{len(bans)} ban(s) — latest: {reason}",
            }
        )

    matches = lifetime.get("matches")
    kd = lifetime.get("kd")
    hs = lifetime.get("hs_pct")

    if matches is not None and matches < 50 and kd is not None and kd >= 1.35:
        flags.append(
            {
                "severity": "medium",
                "label": "Low match count, high lifetime K/D",
                "detail": f"{matches} matches with {kd:.2f} K/D",
            }
        )

    if matches is not None and matches < 100 and hs is not None and hs >= 55:
        flags.append(
            {
                "severity": "medium",
                "label": "High headshot % on low match count",
                "detail": f"{hs:.1f}% HS over {matches} matches",
            }
        )

    recent_adr = recent.get("adr")
    lifetime_adr = lifetime.get("adr")
    if (
        recent_adr is not None
        and lifetime_adr is not None
        and lifetime_adr > 0
        and recent_adr >= lifetime_adr * 1.25
    ):
        flags.append(
            {
                "severity": "medium",
                "label": "Recent ADR above lifetime average",
                "detail": f"Last {recent.get('match_count', '?')} avg {recent_adr} vs lifetime {lifetime_adr}",
            }
        )

    recent_kd = recent.get("kd")
    if (
        recent_kd is not None
        and kd is not None
        and kd > 0
        and recent_kd >= kd * 1.25
        and recent_kd >= 1.3
    ):
        flags.append(
            {
                "severity": "medium",
                "label": "Recent K/D spike vs lifetime",
                "detail": f"Last {recent.get('match_count', '?')} avg {recent_kd:.2f} vs lifetime {kd:.2f}",
            }
        )

    return flags


async def _fetch_faceit_enrichment(
    client: httpx.AsyncClient,
    headers: dict,
    faceit_player_id: str,
) -> dict:
    lifetime_raw: dict = {}
    recent_items: list = []
    bans: list = []

    stats_resp = await client.get(
        f"{FACEIT_BASE}/players/{faceit_player_id}/stats/cs2",
        headers=headers,
    )
    if stats_resp.status_code == 200:
        lifetime_raw = stats_resp.json().get("lifetime") or {}

    recent_resp = await client.get(
        f"{FACEIT_BASE}/players/{faceit_player_id}/games/cs2/stats",
        params={"limit": 20},
        headers=headers,
    )
    if recent_resp.status_code == 200:
        recent_items = recent_resp.json().get("items") or []

    bans_resp = await client.get(
        f"{FACEIT_BASE}/players/{faceit_player_id}/bans",
        params={"limit": 20},
        headers=headers,
    )
    if bans_resp.status_code == 200:
        bans = bans_resp.json().get("items") or []

    lifetime = _normalize_faceit_lifetime(lifetime_raw)
    recent = _aggregate_faceit_recent(recent_items)
    return {
        "lifetime": lifetime,
        "recent_20": recent,
        "bans": [
            {
                "type": b.get("type"),
                "reason": b.get("reason"),
                "game": b.get("game"),
                "starts_at": b.get("starts_at"),
                "ends_at": b.get("ends_at"),
            }
            for b in bans
        ],
        "flags": _compute_faceit_flags(lifetime, recent, bans),
    }


def _extract_map(detail: dict, stats: dict) -> str | None:
    voting = detail.get("voting") or {}
    map_info = voting.get("map") or {}
    pick = map_info.get("pick")
    if isinstance(pick, list) and pick:
        return str(pick[0])
    if isinstance(pick, str):
        return pick

    rounds = stats.get("rounds") or []
    if rounds:
        round_stats = rounds[0].get("round_stats") or {}
        if round_stats.get("Map"):
            return str(round_stats["Map"])

    return detail.get("map") or item_map_from_metadata(detail)


def item_map_from_metadata(detail: dict) -> str | None:
    metadata = detail.get("metadata") or []
    for entry in metadata:
        if entry.get("key") == "map":
            return entry.get("value")
    return None


async def _faceit_headers(session: AsyncSession) -> dict:
    faceit_key = await _get_setting(session, "faceit_api_key") or settings.faceit_api_key
    return {"Authorization": f"Bearer {faceit_key}"}


async def _faceit_get(
    client: httpx.AsyncClient,
    url: str,
    headers: dict,
    *,
    params: dict | None = None,
    timeout: float = FACEIT_REQUEST_TIMEOUT_S,
) -> httpx.Response | None:
    try:
        return await asyncio.wait_for(
            client.get(url, headers=headers, params=params),
            timeout=timeout,
        )
    except (asyncio.TimeoutError, httpx.TimeoutException):
        logger.warning("FACEIT request timed out after %.0fs: %s", timeout, url)
        return None


async def _resolve_steam64(
    client: httpx.AsyncClient,
    headers: dict,
    faceit_player_id: str,
    cache: dict[str, str | None],
) -> str | None:
    if faceit_player_id in cache:
        return cache[faceit_player_id]

    resp = await _faceit_get(client, f"{FACEIT_BASE}/players/{faceit_player_id}", headers, timeout=10.0)
    if resp is None or resp.status_code != 200:
        cache[faceit_player_id] = None
        return None

    data = resp.json()
    steam64 = (data.get("games") or {}).get("cs2", {}).get("game_player_id")
    cache[faceit_player_id] = str(steam64) if steam64 else None
    return cache[faceit_player_id]


async def _upsert_player(
    session: AsyncSession,
    steam64_id: str,
    name: str | None,
    now: datetime,
) -> str:
    from sqlalchemy import text

    result = await session.execute(
        text(
            "INSERT INTO players (id, steam64_id, current_name, first_seen_at, last_seen_at) "
            "VALUES (gen_random_uuid(), :sid, :name, :now, :now) "
            "ON CONFLICT (steam64_id) DO UPDATE SET "
            "last_seen_at = :now, current_name = COALESCE(EXCLUDED.current_name, players.current_name) "
            "RETURNING id"
        ),
        {"sid": steam64_id, "name": name, "now": now},
    )
    return str(result.fetchone()[0])


class SteamClient:
    async def get_player_summaries(self, api_key: str, steam64_ids: list[str]) -> list[dict]:
        if not api_key:
            return []
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(
                "https://api.steampowered.com/ISteamUser/GetPlayerSummaries/v0002/",
                params={"key": api_key, "steamids": ",".join(steam64_ids)},
            )
            resp.raise_for_status()
            return resp.json().get("response", {}).get("players", [])


class LeetifyClient:
    def __init__(self, api_key: str):
        self.api_key = api_key

    async def get_profile(self, steam64_id: str) -> dict | None:
        headers = {"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(
                "https://api-public.cs-prod.leetify.com/v3/profile",
                params={"steam64_id": steam64_id},
                headers=headers,
            )
            if resp.status_code in (404, 401):
                return None
            resp.raise_for_status()
            return resp.json()


async def enrich_player(ctx, player_id: str):
    from sqlalchemy import text

    async with Session() as session:
        pid = str(UUID(player_id))
        result = await session.execute(
            text("SELECT steam64_id, current_name FROM players WHERE id = :id"),
            {"id": pid},
        )
        row = result.fetchone()
        if not row:
            return

        steam64_id, current_name = row[0], row[1]
        steam_key = await _get_setting(session, "steam_api_key") or settings.steam_api_key
        faceit_key = await _get_setting(session, "faceit_api_key") or settings.faceit_api_key
        leetify_key = await _get_setting(session, "leetify_api_key") or settings.leetify_api_key
        now = datetime.now(timezone.utc)

        if steam_key:
            summaries = await SteamClient().get_player_summaries(steam_key, [steam64_id])
            if summaries:
                s = summaries[0]
                name = s.get("personaname")
                await session.execute(
                    text(
                        "UPDATE players SET current_name = :name, avatar_url = :avatar, "
                        "profile_url = :url, last_seen_at = :now WHERE id = :id"
                    ),
                    {
                        "name": name,
                        "avatar": s.get("avatarfull"),
                        "url": s.get("profileurl"),
                        "now": now,
                        "id": pid,
                    },
                )
                if name and name != current_name:
                    hist = await session.execute(
                        text(
                            "SELECT id FROM player_name_history WHERE player_id = :pid AND name = :name"
                        ),
                        {"pid": pid, "name": name},
                    )
                    if not hist.fetchone():
                        await session.execute(
                            text(
                                "INSERT INTO player_name_history (id, player_id, name, first_seen_at, last_seen_at) "
                                "VALUES (gen_random_uuid(), :pid, :name, :now, :now)"
                            ),
                            {"pid": pid, "name": name, "now": now},
                        )

        profile = await LeetifyClient(leetify_key).get_profile(steam64_id)
        if profile:
            await session.execute(
                text(
                    "INSERT INTO player_stat_snapshots (id, player_id, source, captured_at, payload) "
                    "VALUES (gen_random_uuid(), :pid, 'leetify', :now, CAST(:payload AS jsonb))"
                ),
                {"pid": pid, "now": now, "payload": json.dumps(profile)},
            )

        if faceit_key:
            async with httpx.AsyncClient(timeout=30) as client:
                headers = {"Authorization": f"Bearer {faceit_key}"}
                resp = await client.get(
                    f"{FACEIT_BASE}/players",
                    params={"game": "cs2", "game_player_id": steam64_id},
                    headers=headers,
                )
                if resp.status_code == 200:
                    data = resp.json()
                    cs2 = data.get("games", {}).get("cs2", {})
                    faceit_id = data.get("player_id", "")
                    nickname = data.get("nickname")
                    profile_url = f"https://www.faceit.com/en/players/{nickname}" if nickname else None

                    existing = await session.execute(
                        text(
                            "SELECT id FROM player_platform_accounts WHERE platform = 'faceit' AND external_id = :eid"
                        ),
                        {"eid": faceit_id},
                    )
                    if not existing.fetchone():
                        await session.execute(
                            text(
                                "INSERT INTO player_platform_accounts (id, player_id, platform, external_id, nickname, profile_url) "
                                "VALUES (gen_random_uuid(), :pid, 'faceit', :eid, :nick, :url)"
                            ),
                            {"pid": pid, "eid": faceit_id, "nick": nickname, "url": profile_url},
                        )
                    else:
                        await session.execute(
                            text(
                                "UPDATE player_platform_accounts SET nickname = :nick, profile_url = :url "
                                "WHERE platform = 'faceit' AND external_id = :eid"
                            ),
                            {"eid": faceit_id, "nick": nickname, "url": profile_url},
                        )

                    enrichment = await _fetch_faceit_enrichment(client, headers, faceit_id)
                    payload = {
                        "player_id": faceit_id,
                        "nickname": nickname,
                        "profile_url": profile_url,
                        "verified": data.get("verified"),
                        "country": data.get("country"),
                        "elo": cs2.get("faceit_elo"),
                        "skill_level": cs2.get("skill_level"),
                        **enrichment,
                    }
                    await session.execute(
                        text(
                            "INSERT INTO player_stat_snapshots (id, player_id, source, captured_at, payload) "
                            "VALUES (gen_random_uuid(), :pid, 'faceit', :now, CAST(:payload AS jsonb))"
                        ),
                        {"pid": pid, "now": now, "payload": json.dumps(payload)},
                    )

        await session.commit()
        logger.info("Enriched player %s", steam64_id)


async def _import_faceit_match(
    session: AsyncSession,
    client: httpx.AsyncClient,
    headers: dict,
    match_id: str,
    history_item: dict,
    my_steam64: str | None,
    player_cache: dict[str, str | None],
) -> bool:
    from sqlalchemy import text

    detail_resp = await _faceit_get(client, f"{FACEIT_BASE}/matches/{match_id}", headers)
    if detail_resp is None or detail_resp.status_code != 200:
        logger.warning("FACEIT match detail failed for %s", match_id)
        return False

    stats_resp = await _faceit_get(client, f"{FACEIT_BASE}/matches/{match_id}/stats", headers)
    stats = stats_resp.json() if stats_resp is not None and stats_resp.status_code == 200 else {}

    detail = detail_resp.json()
    map_name = _extract_map(detail, stats)

    results = detail.get("results") or {}
    score = results.get("score") or {}
    score_a = _parse_stat_int(score.get("faction1"))
    score_b = _parse_stat_int(score.get("faction2"))

    rounds = stats.get("rounds") or []
    if (score_a is None or score_b is None) and rounds:
        round_stats = rounds[0].get("round_stats") or {}
        score_text = round_stats.get("Score")
        if score_text and "/" in str(score_text):
            left, _, right = str(score_text).partition("/")
            score_a = score_a if score_a is not None else _parse_stat_int(left)
            score_b = score_b if score_b is not None else _parse_stat_int(right)
        teams = rounds[0].get("teams") or []
        if len(teams) >= 2:
            if score_a is None:
                score_a = _parse_stat_int((teams[0].get("team_stats") or {}).get("Final Score"))
            if score_b is None:
                score_b = _parse_stat_int((teams[1].get("team_stats") or {}).get("Final Score"))

    finished_at = history_item.get("finished_at") or detail.get("finished_at")
    played_at = (
        datetime.fromtimestamp(finished_at, tz=timezone.utc)
        if finished_at
        else datetime.now(timezone.utc)
    )

    raw_payload = {"history": history_item, "detail": detail, "stats": stats}

    match_row = await session.execute(
        text(
            "INSERT INTO matches (id, source, source_match_id, map, mode, played_at, score_team_a, score_team_b, raw_payload) "
            "VALUES (gen_random_uuid(), 'faceit', :mid, :map, 'faceit', :played, :sa, :sb, CAST(:raw AS jsonb)) "
            "ON CONFLICT (source, source_match_id) DO UPDATE SET "
            "map = EXCLUDED.map, played_at = EXCLUDED.played_at, "
            "score_team_a = EXCLUDED.score_team_a, score_team_b = EXCLUDED.score_team_b, "
            "raw_payload = EXCLUDED.raw_payload "
            "RETURNING id"
        ),
        {
            "mid": match_id,
            "map": map_name,
            "played": played_at,
            "sa": score_a,
            "sb": score_b,
            "raw": json.dumps(raw_payload),
        },
    )
    db_match_id = str(match_row.fetchone()[0])
    now = datetime.now(timezone.utc)

    if not rounds:
        return True

    team_index = 0
    for team in rounds[0].get("teams") or []:
        team_index += 1
        team_key = "team_a" if team_index == 1 else "team_b"

        for fp in team.get("players") or []:
            faceit_pid = fp.get("player_id")
            nickname = fp.get("nickname")
            if not faceit_pid:
                continue

            steam64 = await _resolve_steam64(client, headers, faceit_pid, player_cache)
            if not steam64:
                logger.debug("Skipping FACEIT player %s (%s) — no Steam ID linked", nickname, faceit_pid)
                continue

            player_db_id = await _upsert_player(session, steam64, nickname, now)
            ps = fp.get("player_stats") or {}
            kills = _parse_stat_int(_get_player_stat(ps, "Kills"))
            deaths = _parse_stat_int(_get_player_stat(ps, "Deaths"))
            assists = _parse_stat_int(_get_player_stat(ps, "Assists"))
            mvps = _parse_stat_int(_get_player_stat(ps, "MVPs", "MVP"))
            ping = _parse_faceit_ping(ps)
            tab_score = _parse_faceit_tab_score(ps, kills, assists, mvps)

            await session.execute(
                text(
                    "INSERT INTO match_players (id, match_id, player_id, team, kills, deaths, assists, mvps, headshot_pct, score, ping, is_me) "
                    "VALUES (gen_random_uuid(), :mid, :pid, :team, :k, :d, :a, :mvp, :hsp, :score, :ping, :is_me) "
                    "ON CONFLICT (match_id, player_id) DO UPDATE SET "
                    "team = EXCLUDED.team, kills = EXCLUDED.kills, deaths = EXCLUDED.deaths, "
                    "assists = EXCLUDED.assists, mvps = EXCLUDED.mvps, headshot_pct = EXCLUDED.headshot_pct, "
                    "score = EXCLUDED.score, ping = EXCLUDED.ping, is_me = EXCLUDED.is_me"
                ),
                {
                    "mid": db_match_id,
                    "pid": player_db_id,
                    "team": team_key,
                    "k": kills,
                    "d": deaths,
                    "a": assists,
                    "mvp": mvps,
                    "hsp": _parse_headshot_pct(
                        _get_player_stat(ps, "Headshots %", "Headshots", "HS %", "Headshot %")
                    ),
                    "score": tab_score,
                    "ping": ping,
                    "is_me": bool(my_steam64 and steam64 == my_steam64),
                },
            )

    return True


async def sync_faceit_matches(ctx):
    from sqlalchemy import text

    async with Session() as session:
        faceit_key = await _get_setting(session, "faceit_api_key") or settings.faceit_api_key
        faceit_nick = await _get_setting(session, "faceit_nickname") or settings.faceit_nickname
        my_steam64 = await _get_setting(session, "my_steam64_id") or settings.my_steam64_id

        if not faceit_key or not faceit_nick:
            logger.warning("FACEIT not configured, skipping sync")
            return

        job_result = await session.execute(
            text(
                "INSERT INTO sync_jobs (id, job_type, status, started_at, matches_imported) "
                "VALUES (gen_random_uuid(), 'faceit', 'running', :now, 0) RETURNING id"
            ),
            {"now": datetime.now(timezone.utc)},
        )
        job_id = str(job_result.fetchone()[0])
        imported = 0
        failed = 0
        processed = 0
        headers = await _faceit_headers(session)
        player_cache: dict[str, str | None] = {}
        max_matches = settings.faceit_sync_max_matches

        try:
            timeout = httpx.Timeout(connect=10.0, read=FACEIT_REQUEST_TIMEOUT_S, write=10.0, pool=10.0)
            async with httpx.AsyncClient(timeout=timeout) as client:
                resp = await _faceit_get(
                    client,
                    f"{FACEIT_BASE}/players",
                    headers,
                    params={"nickname": faceit_nick},
                    timeout=20.0,
                )
                if resp is None:
                    raise RuntimeError("FACEIT player lookup timed out")
                resp.raise_for_status()
                player = resp.json()

                offset = 0
                while offset < max_matches:
                    hist_resp = await client.get(
                        f"{FACEIT_BASE}/players/{player['player_id']}/history",
                        params={"game": "cs2", "offset": offset, "limit": 20},
                        headers=headers,
                    )
                    hist_resp.raise_for_status()
                    history = hist_resp.json()

                    items = history.get("items", [])
                    if not items:
                        break

                    for item in items:
                        match_id = item.get("match_id")
                        if not match_id:
                            continue

                        processed += 1
                        try:
                            ok = await _import_faceit_match(
                                session, client, headers, match_id, item, my_steam64, player_cache
                            )
                            if ok:
                                imported += 1
                            else:
                                failed += 1
                        except Exception:
                            failed += 1
                            logger.exception("FACEIT import failed for match %s", match_id)

                        if processed % FACEIT_COMMIT_EVERY == 0:
                            await session.execute(
                                text(
                                    "UPDATE sync_jobs SET matches_imported = :n WHERE id = :id"
                                ),
                                {"n": imported, "id": job_id},
                            )
                            await session.commit()
                            logger.info(
                                "FACEIT sync progress: %d imported, %d failed, %d processed",
                                imported,
                                failed,
                                processed,
                            )

                        if FACEIT_MATCH_DELAY_S > 0:
                            await asyncio.sleep(FACEIT_MATCH_DELAY_S)

                    offset += 20
                    if len(items) < 20:
                        break

            await session.execute(
                text(
                    "UPDATE sync_jobs SET status = 'completed', finished_at = :now, matches_imported = :n WHERE id = :id"
                ),
                {"now": datetime.now(timezone.utc), "n": imported, "id": job_id},
            )
            await session.commit()
            logger.info(
                "FACEIT sync complete: %d imported, %d failed, %d processed",
                imported,
                failed,
                processed,
            )
        except Exception as e:
            await session.execute(
                text(
                    "UPDATE sync_jobs SET status = 'failed', finished_at = :now, "
                    "matches_imported = :n, error_message = :err WHERE id = :id"
                ),
                {"now": datetime.now(timezone.utc), "n": imported, "err": str(e)[:500], "id": job_id},
            )
            await session.commit()
            logger.exception("FACEIT sync failed after %d imports", imported)


async def process_enrichment_jobs(ctx):
    from sqlalchemy import text

    async with Session() as session:
        result = await session.execute(
            text(
                "SELECT id, job_type FROM sync_jobs WHERE status = 'pending' AND job_type LIKE 'enrich_player:%' LIMIT 20"
            )
        )
        jobs = result.fetchall()

    for job_id, job_type in jobs:
        player_id = job_type.split(":", 1)[1]
        try:
            await enrich_player(ctx, player_id)
            async with Session() as session:
                from sqlalchemy import text

                await session.execute(
                    text("UPDATE sync_jobs SET status = 'completed', finished_at = :now WHERE id = :id"),
                    {"now": datetime.now(timezone.utc), "id": str(job_id)},
                )
                await session.commit()
        except Exception as e:
            logger.exception("Enrichment failed for %s", player_id)


async def run_enrichment_batch(ctx):
    from sqlalchemy import text

    async with Session() as session:
        result = await session.execute(
            text("SELECT id FROM players ORDER BY last_seen_at DESC LIMIT 50")
        )
        player_ids = [str(row[0]) for row in result.fetchall()]

    for pid in player_ids:
        await enrich_player(ctx, pid)


async def _list_pending_leetify_match_ids(
    *,
    lookback_days: int | None = None,
    limit: int | None = None,
) -> list[str]:
    """Return match IDs that still need Leetify enrichment."""
    from sqlalchemy import text

    lookback = lookback_days if lookback_days is not None else settings.leetify_auto_sync_lookback_days
    batch_limit = limit if limit is not None else settings.leetify_auto_sync_limit

    async with Session() as session:
        result = await session.execute(
            text(
                """
                SELECT id FROM matches
                WHERE source IN ('steam_gc', 'faceit')
                  AND (share_code IS NOT NULL OR source = 'faceit')
                  AND (
                    raw_payload IS NULL
                    OR raw_payload->'_enrichment'->>'leetify_synced_at' IS NULL
                  )
                  AND (
                    played_at IS NULL
                    OR played_at >= NOW() - make_interval(days => :lookback_days)
                  )
                ORDER BY played_at DESC NULLS LAST
                LIMIT :limit
                """
            ),
            {"lookback_days": lookback, "limit": batch_limit},
        )
        return [str(row[0]) for row in result.fetchall()]


async def _sync_leetify_match_ids(match_ids: list[str], *, label: str) -> tuple[int, int]:
    if not match_ids:
        logger.info("%s: no matches to process", label)
        return 0, 0

    synced = 0
    failed = 0
    delay_s = max(0, settings.leetify_auto_sync_delay_ms) / 1000.0

    async with httpx.AsyncClient(base_url=settings.api_internal_url, timeout=120.0) as client:
        for idx, match_id in enumerate(match_ids):
            try:
                resp = await client.post(f"/api/v1/matches/{match_id}/sync")
                if resp.is_success:
                    body = resp.json() if resp.headers.get("content-type", "").startswith("application/json") else {}
                    sources = body.get("sources") or []
                    if "leetify" in sources:
                        synced += 1
                        logger.info("%s: enriched %s via Leetify", label, match_id)
                    else:
                        failed += 1
                        errors = body.get("errors") or []
                        logger.info(
                            "%s: match %s not ready yet (%s)",
                            label,
                            match_id,
                            "; ".join(errors) if errors else "no leetify data",
                        )
                else:
                    failed += 1
                    logger.warning("%s: HTTP %s for match %s", label, resp.status_code, match_id)
            except Exception as exc:
                failed += 1
                logger.warning("%s: failed for match %s: %s", label, match_id, exc)

            if delay_s and idx + 1 < len(match_ids):
                await asyncio.sleep(delay_s)

    logger.info("%s finished: %d enriched, %d pending/failed, %d total", label, synced, failed, len(match_ids))
    return synced, failed


async def sync_leetify_matches(ctx):
    """Manual bulk enrich: recent matches still missing Leetify data."""
    match_ids = await _list_pending_leetify_match_ids(
        lookback_days=max(settings.leetify_auto_sync_lookback_days, 90),
        limit=max(settings.leetify_auto_sync_limit, 50),
    )
    await _sync_leetify_match_ids(match_ids, label="Leetify bulk sync")


async def auto_sync_leetify_pending(ctx):
    """Cron: enrich newly imported Steam/FACEIT matches once Leetify has them."""
    match_ids = await _list_pending_leetify_match_ids()
    if not match_ids:
        return
    logger.info("Leetify auto-sync: %d pending match(es)", len(match_ids))
    await _sync_leetify_match_ids(match_ids, label="Leetify auto-sync")


async def import_leetify_profile(ctx, sync_job_id: str | None = None):
    from sqlalchemy import text

    logger.info("Leetify profile import job started (sync_job_id=%s)", sync_job_id)
    job_id = sync_job_id
    if job_id:
        async with Session() as session:
            await session.execute(
                text("UPDATE sync_jobs SET status = 'running' WHERE id = :id"),
                {"id": job_id},
            )
            await session.commit()

    async with httpx.AsyncClient(base_url=settings.api_internal_url, timeout=7200.0) as client:
        try:
            resp = await client.post("/api/v1/import/leetify")
            if resp.is_success:
                result = resp.json()
                if result.get("error"):
                    msg = str(result["error"])
                elif result.get("message"):
                    msg = str(result["message"])
                else:
                    msg = f"{result.get('imported', 0)} new, {result.get('updated', 0)} updated"
                logger.info("Leetify profile import: %s", msg)
                if job_id:
                    async with Session() as session:
                        await session.execute(
                            text(
                                "UPDATE sync_jobs SET status = 'completed', finished_at = :now, "
                                "matches_imported = :n, error_message = :msg WHERE id = :id"
                            ),
                            {
                                "now": datetime.now(timezone.utc),
                                "n": (
                                    result.get("imported", 0)
                                    + result.get("updated", 0)
                                ),
                                "msg": msg,
                                "id": job_id,
                            },
                        )
                        await session.commit()
            else:
                err = resp.text[:500]
                logger.warning("Leetify profile import HTTP %s: %s", resp.status_code, err)
                if job_id:
                    async with Session() as session:
                        await session.execute(
                            text(
                                "UPDATE sync_jobs SET status = 'failed', finished_at = :now, "
                                "error_message = :msg WHERE id = :id"
                            ),
                            {"now": datetime.now(timezone.utc), "msg": err, "id": job_id},
                        )
                        await session.commit()
        except Exception as exc:
            logger.exception("Leetify profile import failed")
            if job_id:
                async with Session() as session:
                    await session.execute(
                        text(
                            "UPDATE sync_jobs SET status = 'failed', finished_at = :now, "
                            "error_message = :msg WHERE id = :id"
                        ),
                        {"now": datetime.now(timezone.utc), "msg": str(exc), "id": job_id},
                    )
                    await session.commit()


async def import_csstats_profile(ctx, sync_job_id: str | None = None):
    from sqlalchemy import text

    logger.info("csstats profile import job started (sync_job_id=%s)", sync_job_id)
    job_id = sync_job_id
    if job_id:
        async with Session() as session:
            await session.execute(
                text("UPDATE sync_jobs SET status = 'running' WHERE id = :id"),
                {"id": job_id},
            )
            await session.commit()

    async with httpx.AsyncClient(base_url=settings.api_internal_url, timeout=7200.0) as client:
        try:
            resp = await client.post("/api/v1/import/csstats")
            if resp.is_success:
                result = resp.json()
                if result.get("error"):
                    msg = str(result["error"])
                elif result.get("message"):
                    msg = str(result["message"])
                else:
                    msg = f"{result.get('imported', 0)} new, {result.get('updated', 0)} updated"
                logger.info("csstats profile import: %s", msg)
                if job_id:
                    async with Session() as session:
                        await session.execute(
                            text(
                                "UPDATE sync_jobs SET status = 'completed', finished_at = :now, "
                                "matches_imported = :n, error_message = :msg WHERE id = :id"
                            ),
                            {
                                "now": datetime.now(timezone.utc),
                                "n": (
                                    result.get("imported", 0)
                                    + result.get("updated", 0)
                                ),
                                "msg": msg,
                                "id": job_id,
                            },
                        )
                        await session.commit()
            else:
                err = resp.text[:500]
                logger.warning("csstats profile import HTTP %s: %s", resp.status_code, err)
                if job_id:
                    async with Session() as session:
                        await session.execute(
                            text(
                                "UPDATE sync_jobs SET status = 'failed', finished_at = :now, "
                                "error_message = :msg WHERE id = :id"
                            ),
                            {"now": datetime.now(timezone.utc), "msg": err, "id": job_id},
                        )
                        await session.commit()
        except Exception as exc:
            logger.exception("csstats profile import failed")
            if job_id:
                async with Session() as session:
                    await session.execute(
                        text(
                            "UPDATE sync_jobs SET status = 'failed', finished_at = :now, "
                            "error_message = :msg WHERE id = :id"
                        ),
                        {"now": datetime.now(timezone.utc), "msg": str(exc), "id": job_id},
                    )
                    await session.commit()


class WorkerSettings:
    redis_settings = RedisSettings.from_dsn(settings.redis_url)
    job_timeout = 7200
    functions = [
        enrich_player,
        func(sync_faceit_matches, timeout=7200),
        process_enrichment_jobs,
        run_enrichment_batch,
        func(sync_leetify_matches, timeout=1800),
        func(auto_sync_leetify_pending, timeout=600),
        func(import_leetify_profile, timeout=7200),
        func(import_csstats_profile, timeout=7200),
    ]
    cron_jobs = [
        cron(sync_faceit_matches, hour={0, 6, 12, 18}, minute=0),
        cron(process_enrichment_jobs, minute={0, 5, 10, 15, 20, 25, 30, 35, 40, 45, 50, 55}),
        cron(auto_sync_leetify_pending, minute={2, 7, 12, 17, 22, 27, 32, 37, 42, 47, 52, 57}),
    ]
