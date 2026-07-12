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

    leetify = enrichment.get("leetify") or {}
    replay = leetify.get("replay_url") or leetify.get("replayUrl")
    if replay:
        return str(replay)

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


def _leetify_game_id_from_match(match: Match) -> str | None:
    enrichment = (match.raw_payload or {}).get("_enrichment") or {}
    cached = enrichment.get("leetify_game_id")
    if cached:
        return str(cached)
    leetify = enrichment.get("leetify") or {}
    if leetify.get("id"):
        return str(leetify["id"])
    return None


def _team_number_map(stats: list[dict]) -> dict[int, str]:
    team_numbers = sorted(
        {int(s["initial_team_number"]) for s in stats if s.get("initial_team_number") is not None}
    )
    if len(team_numbers) < 2:
        return {}
    return {team_numbers[0]: "team_a", team_numbers[1]: "team_b"}


def _map_leetify_scores(
    team_scores: list,
    stats: list[dict],
    my_steam64: str | None,
) -> tuple[int | None, int | None]:
    scores_by_team: dict[int, int] = {}
    for entry in team_scores:
        if not isinstance(entry, dict):
            continue
        team_number = entry.get("team_number")
        score = entry.get("score")
        if team_number is not None and score is not None:
            scores_by_team[int(team_number)] = int(score)

    if len(scores_by_team) < 2:
        return None, None

    my_team_number = None
    if my_steam64:
        for stat in stats:
            steam64 = str(stat.get("steam64_id") or stat.get("steamId") or "")
            if steam64 == my_steam64:
                tn = stat.get("initial_team_number")
                if tn is not None:
                    my_team_number = int(tn)
                break

    team_numbers = sorted(scores_by_team.keys())
    if my_team_number is not None and my_team_number in scores_by_team:
        other_numbers = [n for n in team_numbers if n != my_team_number]
        other_score = scores_by_team.get(other_numbers[0]) if other_numbers else None
        return scores_by_team.get(my_team_number), other_score

    return scores_by_team.get(team_numbers[0]), scores_by_team.get(team_numbers[1])


async def apply_leetify_match(db: AsyncSession, match: Match, leetify_data: dict) -> None:
    my_steam64 = await get_my_steam64_id(db)

    map_name = leetify_data.get("map_name") or leetify_data.get("mapName")
    if map_name:
        match.map = str(map_name).lower()

    finished_at = _parse_leetify_datetime(leetify_data.get("finished_at") or leetify_data.get("finishedAt"))
    if finished_at:
        match.played_at = finished_at

    stats = leetify_data.get("stats") or []
    team_scores = leetify_data.get("team_scores") or leetify_data.get("teamScores") or []
    score_a, score_b = _map_leetify_scores(team_scores, stats, my_steam64)
    if score_a is not None and score_b is not None:
        match.score_team_a = score_a
        match.score_team_b = score_b

    replay_url = leetify_data.get("replay_url") or leetify_data.get("replayUrl")
    payload = dict(match.raw_payload or {})
    enrichment = dict(payload.get("_enrichment") or {})
    if replay_url:
        enrichment["replay_url"] = replay_url
    if leetify_data.get("id"):
        enrichment["leetify_game_id"] = leetify_data["id"]
    enrichment["leetify"] = leetify_data
    payload["_enrichment"] = enrichment
    match.raw_payload = payload

    if not stats:
        return

    team_map = _team_number_map(stats)
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

        team_number = stat.get("initial_team_number")
        team = team_map.get(int(team_number)) if team_number is not None else None
        if team is None:
            team = "team_a" if idx < len(stats) / 2 else "team_b"

        mp = mp_by_steam.get(steam64)
        if mp is None:
            mp = MatchPlayer(
                match_id=match.id,
                player_id=player.id,
                team=team,
                is_me=is_me,
            )
            db.add(mp)
            mp_by_steam[steam64] = mp
        else:
            mp.team = team

        mp.kills = _int_or_none(stat.get("total_kills") or stat.get("kills"))
        mp.deaths = _int_or_none(stat.get("total_deaths") or stat.get("deaths"))
        mp.assists = _int_or_none(stat.get("total_assists") or stat.get("assists"))
        mp.mvps = _int_or_none(stat.get("mvps") or stat.get("mvp"))
        mp.score = _int_or_none(stat.get("score"))
        mp.ping = _int_or_none(stat.get("ping") or stat.get("average_ping"))
        hsp = stat.get("accuracy_head") or stat.get("hs_percentage") or stat.get("headshotPercentage")
        if hsp is not None:
            mp.headshot_pct = float(hsp) * 100 if float(hsp) <= 1 else float(hsp)
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
    my_steam64 = await get_my_steam64_id(db)

    if leetify_key and (match.share_code or match.source == "faceit" or _leetify_game_id_from_match(match)):
        client = LeetifyClient(leetify_key)
        try:
            leetify_data, lookup = await client.resolve_match(
                share_code=match.share_code,
                source_match_id=match.source_match_id,
                mode=match.mode,
                source=match.source,
                my_steam64_id=my_steam64,
                leetify_game_id=_leetify_game_id_from_match(match),
                played_at=match.played_at,
            )
            if leetify_data:
                await apply_leetify_match(db, match, leetify_data)
                sources.append("leetify")
                if lookup:
                    sources.append(lookup)
            else:
                tried = "matchmaking_competitive, matchmaking, profile history"
                if match.source == "faceit":
                    tried = "faceit, profile history"
                errors.append(
                    f"Leetify has no data for this match (404). "
                    f"Tried {tried}. Share codes only work if Leetify has analyzed the match."
                )
        except Exception as exc:
            errors.append(f"Leetify: {exc}")
    elif not leetify_key:
        errors.append("Leetify API key not configured")
    elif not match.share_code and match.source != "faceit":
        errors.append("No share code on this match — cannot query Leetify by match token")

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
