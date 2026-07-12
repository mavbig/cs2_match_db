import re
from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Match, MatchPlayer
from app.services.leetify_client import LeetifyClient
from app.services.match_service import (
    fetch_steam_persona_names,
    get_match_with_players,
    get_my_steam64_id,
    get_setting,
    upsert_player,
)
from app.config import settings


def extract_demo_url_from_gc(raw: dict | None) -> str | None:
    if not raw:
        return None
    enrichment = raw.get("_enrichment") or {}
    if enrichment.get("replay_url"):
        return str(enrichment["replay_url"])

    for entry in reversed(raw.get("roundstatsall") or []):
        map_field = entry.get("map")
        if isinstance(map_field, str) and map_field.startswith("http") and ".dem" in map_field:
            return map_field.replace(".bz2", "") if map_field.endswith(".bz2") else map_field
    return None


def reparse_gc_payload(raw: dict) -> dict:
    all_rs = raw.get("roundstatsall") or []
    if all_rs:
        with_result = [e for e in all_rs if e.get("match_result") not in (None, 0)]
        if with_result:
            last = with_result[-1]
        else:
            last = max(
                all_rs,
                key=lambda e: sum((e.get("team_scores") or [])[:2]) if e.get("team_scores") else 0,
            )
    else:
        last = raw.get("roundstats_legacy") or {}

    team_scores = last.get("team_scores") or []
    map_name = None
    watchable = raw.get("watchablematchinfo") or {}
    for candidate in (watchable.get("game_map"), watchable.get("game_mapgroup")):
        if candidate:
            m = re.search(r"de_[a-z0-9_]+", str(candidate), re.I)
            if m:
                map_name = m.group(0).lower()
                break

    return {
        "map": map_name,
        "score_team_a": team_scores[0] if len(team_scores) > 0 else None,
        "score_team_b": team_scores[1] if len(team_scores) > 1 else None,
        "duration_seconds": last.get("match_duration"),
    }


def _parse_leetify_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _leetify_data_source(match: Match) -> tuple[str, str] | None:
    if match.share_code and match.source == "steam_gc":
        return "matchmaking", match.share_code
    if match.source == "faceit":
        return "faceit", match.source_match_id
    return None


async def apply_leetify_match(db: AsyncSession, match: Match, leetify_data: dict) -> None:
    my_steam64 = await get_my_steam64_id(db)

    map_name = leetify_data.get("map_name") or leetify_data.get("mapName")
    if map_name:
        match.map = str(map_name).lower()

    finished_at = _parse_leetify_datetime(leetify_data.get("finished_at") or leetify_data.get("finishedAt"))
    if finished_at:
        match.played_at = finished_at

    team_scores = leetify_data.get("team_scores") or leetify_data.get("teamScores") or []
    if isinstance(team_scores, list) and len(team_scores) >= 2:
        scores = []
        for entry in team_scores[:2]:
            if isinstance(entry, dict):
                scores.append(entry.get("score"))
            else:
                scores.append(entry)
        if scores[0] is not None and scores[1] is not None:
            match.score_team_a = int(scores[0])
            match.score_team_b = int(scores[1])

    replay_url = leetify_data.get("replay_url") or leetify_data.get("replayUrl")
    payload = dict(match.raw_payload or {})
    enrichment = dict(payload.get("_enrichment") or {})
    if replay_url:
        enrichment["replay_url"] = replay_url
    enrichment["leetify"] = leetify_data
    payload["_enrichment"] = enrichment
    match.raw_payload = payload

    stats = leetify_data.get("stats") or []
    if not stats:
        return

    steam_ids = [
        str(s.get("steam64_id") or s.get("steamId") or s.get("steam_id"))
        for s in stats
        if s.get("steam64_id") or s.get("steamId") or s.get("steam_id")
    ]
    steam_names = await fetch_steam_persona_names(db, steam_ids)

    mp_by_steam: dict[str, MatchPlayer] = {}
    for mp in match.players:
        mp_by_steam[mp.player.steam64_id] = mp

    for idx, stat in enumerate(stats):
        steam64 = str(stat.get("steam64_id") or stat.get("steamId") or stat.get("steam_id") or "")
        if not steam64:
            continue

        name = steam_names.get(steam64) or stat.get("name")
        player = await upsert_player(db, steam64, name=name)
        is_me = steam64 == my_steam64

        mp = mp_by_steam.get(steam64)
        if mp is None:
            team = "team_a" if idx < len(stats) / 2 else "team_b"
            mp = MatchPlayer(
                match_id=match.id,
                player_id=player.id,
                team=team,
                is_me=is_me,
            )
            db.add(mp)
            mp_by_steam[steam64] = mp

        mp.kills = _int_or_none(stat.get("kills") or stat.get("total_kills"))
        mp.deaths = _int_or_none(stat.get("deaths") or stat.get("total_deaths"))
        mp.assists = _int_or_none(stat.get("assists") or stat.get("total_assists"))
        mp.mvps = _int_or_none(stat.get("mvps") or stat.get("mvp"))
        mp.score = _int_or_none(stat.get("score"))
        mp.ping = _int_or_none(stat.get("ping") or stat.get("average_ping"))
        hsp = stat.get("hs_percentage") or stat.get("headshotPercentage") or stat.get("headshot_pct")
        if hsp is not None:
            mp.headshot_pct = float(hsp)
        mp.is_me = is_me

    await db.flush()


def _int_or_none(value) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


async def sync_match_from_sources(db: AsyncSession, match_id: UUID) -> dict:
    match = await get_match_with_players(db, match_id)
    if not match:
        raise ValueError("Match not found")

    sources: list[str] = []
    errors: list[str] = []

    if match.source == "steam_gc" and match.raw_payload:
        parsed = reparse_gc_payload(match.raw_payload)
        if parsed.get("map"):
            match.map = parsed["map"]
        if parsed.get("score_team_a") is not None:
            match.score_team_a = parsed["score_team_a"]
        if parsed.get("score_team_b") is not None:
            match.score_team_b = parsed["score_team_b"]
        if parsed.get("duration_seconds"):
            match.duration_seconds = parsed["duration_seconds"]
        sources.append("gc_reparse")

    leetify_key = await get_setting(db, "leetify_api_key") or settings.leetify_api_key
    source_pair = _leetify_data_source(match)
    if leetify_key and source_pair:
        data_source, data_source_id = source_pair
        client = LeetifyClient(leetify_key)
        try:
            leetify_data = await client.get_match_by_source(data_source, data_source_id)
            if leetify_data:
                await apply_leetify_match(db, match, leetify_data)
                sources.append("leetify")
            else:
                errors.append("Leetify has no data for this match (404 or private)")
        except Exception as exc:
            errors.append(f"Leetify: {exc}")
    elif source_pair and not leetify_key:
        errors.append("Leetify API key not configured")

    await db.flush()

    return {
        "match_id": str(match.id),
        "sources": sources,
        "errors": errors,
        "demo_url": extract_demo_url_from_gc(match.raw_payload),
        "map": match.map,
        "score_team_a": match.score_team_a,
        "score_team_b": match.score_team_b,
    }
