import logging
import re
from datetime import datetime, timedelta, timezone
from uuid import UUID

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

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
from app.services.enrichment import get_match_sync_status, touch_enrichment

logger = logging.getLogger(__name__)


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


def _pick_final_round_stats(raw: dict) -> dict:
    all_rs = raw.get("roundstatsall") or []
    if all_rs:
        with_result = [e for e in all_rs if e.get("match_result") not in (None, 0)]
        if with_result:
            return with_result[-1]
        return max(
            all_rs,
            key=lambda e: sum((e.get("team_scores") or [])[:2]) if e.get("team_scores") else 0,
        )
    return raw.get("roundstats_legacy") or {}


def _account_id_to_steam64(account_id: int) -> str:
    return str(int(account_id) + 76561197960265728)


def _restore_teams_from_gc(match: Match) -> None:
    if match.source != "steam_gc" or not match.raw_payload:
        return
    last = _pick_final_round_stats(match.raw_payload)
    reservation = last.get("reservation") or {}
    account_ids = reservation.get("account_ids") or []
    if len(account_ids) < 2:
        return

    steam_to_team: dict[str, str] = {}
    halfway = len(account_ids) / 2
    for i, account_id in enumerate(account_ids):
        steam64 = _account_id_to_steam64(account_id)
        steam_to_team[steam64] = "team_a" if i < halfway else "team_b"

    for mp in match.players:
        team = steam_to_team.get(mp.player.steam64_id)
        if team:
            mp.team = team


def reparse_gc_payload(raw: dict) -> dict:
    last = _pick_final_round_stats(raw)
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


def _team_number_map(stats: list[dict], my_steam64: str | None) -> dict[int, str]:
    team_numbers = sorted(
        {int(s["initial_team_number"]) for s in stats if s.get("initial_team_number") is not None}
    )
    if len(team_numbers) < 2:
        return {}

    my_team_number = None
    if my_steam64:
        for stat in stats:
            steam64 = str(stat.get("steam64_id") or stat.get("steamId") or stat.get("steam_id") or "")
            if steam64 == my_steam64:
                tn = stat.get("initial_team_number")
                if tn is not None:
                    my_team_number = int(tn)
                break

    if my_team_number is not None and my_team_number in team_numbers:
        others = [n for n in team_numbers if n != my_team_number]
        if others:
            return {my_team_number: "team_a", others[0]: "team_b"}

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


def _normalize_player_stat(stat: dict) -> dict:
    return {
        "steam64_id": stat.get("steam64_id") or stat.get("steam64Id") or stat.get("steamId"),
        "name": stat.get("name"),
        "total_kills": stat.get("total_kills") or stat.get("totalKills") or stat.get("kills"),
        "total_deaths": stat.get("total_deaths") or stat.get("totalDeaths") or stat.get("deaths"),
        "total_assists": stat.get("total_assists") or stat.get("totalAssists") or stat.get("assists"),
        "mvps": stat.get("mvps") or stat.get("mvp"),
        "score": stat.get("score"),
        "ping": stat.get("ping") or stat.get("average_ping"),
        "accuracy_head": stat.get("accuracy_head") or stat.get("hsp") or stat.get("headshotPercentage"),
        "initial_team_number": stat.get("initial_team_number") or stat.get("initialTeamNumber"),
    }


def _score_array_to_team_scores(score: list, stats: list[dict]) -> list[dict]:
    if not isinstance(score, list) or len(score) < 2:
        return []

    team_numbers = sorted(
        {
            int(s.get("initial_team_number") or s.get("initialTeamNumber"))
            for s in stats
            if (s.get("initial_team_number") or s.get("initialTeamNumber")) is not None
        }
    )
    if len(team_numbers) >= 2:
        return [
            {"team_number": team_numbers[0], "score": int(score[0])},
            {"team_number": team_numbers[1], "score": int(score[1])},
        ]

    return [
        {"team_number": 2, "score": int(score[0])},
        {"team_number": 3, "score": int(score[1])},
    ]


def normalize_leetify_match_data(data: dict) -> dict:
    normalized = dict(data)
    if data.get("dataSource") and not data.get("data_source"):
        normalized["data_source"] = data["dataSource"]
    if data.get("mapName") and not data.get("map_name"):
        normalized["map_name"] = data["mapName"]
    if data.get("finishedAt") and not data.get("finished_at"):
        normalized["finished_at"] = data["finishedAt"]
    if data.get("replayUrl") and not data.get("replay_url"):
        normalized["replay_url"] = data["replayUrl"]

    raw_stats = data.get("stats") or data.get("playerStats") or []
    if raw_stats and not normalized.get("stats"):
        normalized["stats"] = [_normalize_player_stat(s) for s in raw_stats]

    team_scores = data.get("team_scores") or data.get("teamScores")
    if not team_scores and isinstance(data.get("score"), list):
        normalized["team_scores"] = _score_array_to_team_scores(data["score"], normalized.get("stats") or [])
    elif team_scores:
        normalized["team_scores"] = team_scores

    return normalized


def _needs_full_leetify_match(data: dict) -> bool:
    stats = data.get("stats") or data.get("playerStats") or []
    return bool(data.get("id")) and len(stats) < 2


async def apply_leetify_match(
    db: AsyncSession,
    match: Match,
    leetify_data: dict,
    *,
    skip_player_lookup: bool = False,
) -> None:
    leetify_data = normalize_leetify_match_data(leetify_data)
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
    enrichment["leetify_synced_at"] = datetime.now(timezone.utc).isoformat()
    payload["_enrichment"] = enrichment
    match.raw_payload = payload

    if not stats:
        return

    if skip_player_lookup:
        await db.flush()
        return

    has_gc_teams = (
        match.source == "steam_gc"
        and bool(match.raw_payload)
        and bool(match.raw_payload.get("roundstatsall"))
    )
    if has_gc_teams:
        _restore_teams_from_gc(match)

    team_map = _team_number_map(stats, my_steam64)
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

        mp = mp_by_steam.get(steam64)
        if mp is None:
            if team is None:
                team = "team_a" if idx < len(stats) / 2 else "team_b"
            mp = MatchPlayer(
                match_id=match.id,
                player_id=player.id,
                team=team,
                is_me=is_me,
            )
            db.add(mp)
            mp_by_steam[steam64] = mp
        elif not has_gc_teams and team:
            mp.team = team
        elif team and not mp.team:
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
        match.raw_payload = touch_enrichment(
            match.raw_payload,
            steam_synced_at=datetime.now(timezone.utc).isoformat(),
        )
        sources.append("gc_reparse")

    leetify_key = await get_setting(db, "leetify_api_key") or settings.leetify_api_key
    my_steam64 = await get_my_steam64_id(db)

    if leetify_key and (match.share_code or match.source == "faceit" or _leetify_game_id_from_match(match)):
        client = LeetifyClient(leetify_key)
        try:
            leetify_data, lookup, leetify_notes = await client.resolve_match(
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
                detail = "; ".join(leetify_notes) if leetify_notes else "no data returned"
                errors.append(
                    "Leetify has no data for this match. "
                    f"Tried profile history, game id, and share-code lookups ({detail}). "
                    "The match must exist on Leetify (account linked + demo analyzed)."
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
        "sync_status": get_match_sync_status(match.raw_payload, match.source),
    }


def _leetify_match_identity(leetify_data: dict) -> tuple[str, str, str | None, str | None]:
    data_source = (leetify_data.get("data_source") or "").lower()
    ds_id = leetify_data.get("data_source_match_id")
    leetify_id = str(leetify_data.get("id") or "")
    share_code = None
    if ds_id and str(ds_id).upper().startswith("CSGO-"):
        share_code = str(ds_id)

    mode = None
    if data_source == "matchmaking_competitive":
        mode = "premier"
    elif data_source == "matchmaking":
        mode = "competitive"
    elif data_source == "faceit":
        mode = "faceit"
    elif data_source == "renown":
        mode = "renown"

    if data_source == "faceit" and ds_id:
        return "faceit", str(ds_id), None, mode
    if leetify_id:
        return "leetify", leetify_id, share_code, mode
    return "leetify", str(ds_id or "unknown"), share_code, mode


async def find_match_for_leetify(db: AsyncSession, leetify_data: dict) -> Match | None:
    leetify_id = leetify_data.get("id")
    data_source = (leetify_data.get("data_source") or "").lower()
    ds_id = leetify_data.get("data_source_match_id")
    share_code = None
    if ds_id and str(ds_id).upper().startswith("CSGO-"):
        share_code = str(ds_id)

    conditions = []
    if leetify_id:
        lid = str(leetify_id)
        conditions.append(Match.raw_payload["_enrichment"]["leetify_game_id"].astext == lid)
        conditions.append(Match.raw_payload["_enrichment"]["leetify"]["id"].astext == lid)
        conditions.append((Match.source == "leetify") & (Match.source_match_id == lid))
    if share_code:
        conditions.append(Match.share_code == share_code)
    if data_source == "faceit" and ds_id:
        conditions.append((Match.source == "faceit") & (Match.source_match_id == str(ds_id)))

    if not conditions:
        return None

    result = await db.execute(
        select(Match)
        .options(selectinload(Match.players).selectinload(MatchPlayer.player))
        .where(or_(*conditions))
        .limit(1)
    )
    match = result.scalar_one_or_none()
    if match:
        return match

    finished_at = _parse_leetify_datetime(leetify_data.get("finished_at") or leetify_data.get("finishedAt"))
    map_name = leetify_data.get("map_name") or leetify_data.get("mapName")
    if finished_at and map_name:
        result = await db.execute(
            select(Match)
            .options(selectinload(Match.players).selectinload(MatchPlayer.player))
            .where(Match.played_at >= finished_at - timedelta(minutes=3))
            .where(Match.played_at <= finished_at + timedelta(minutes=3))
            .where(Match.map == str(map_name).lower())
            .limit(1)
        )
        return result.scalar_one_or_none()

    return None


async def create_match_from_leetify(
    db: AsyncSession,
    leetify_data: dict,
    my_steam64: str,
    *,
    skip_player_lookup: bool = False,
) -> Match:
    leetify_data = normalize_leetify_match_data(leetify_data)
    source, source_match_id, share_code, mode = _leetify_match_identity(leetify_data)
    map_name = leetify_data.get("map_name") or leetify_data.get("mapName")
    finished_at = _parse_leetify_datetime(leetify_data.get("finished_at") or leetify_data.get("finishedAt"))
    stats = leetify_data.get("stats") or []
    team_scores = leetify_data.get("team_scores") or leetify_data.get("teamScores") or []
    score_a, score_b = _map_leetify_scores(team_scores, stats, my_steam64)

    match = Match(
        source=source,
        source_match_id=source_match_id,
        map=str(map_name).lower() if map_name else None,
        mode=mode,
        played_at=finished_at,
        score_team_a=score_a,
        score_team_b=score_b,
        share_code=share_code,
        raw_payload={"_source": "leetify_import", "_enrichment": {}},
    )
    db.add(match)
    await db.flush()
    await apply_leetify_match(db, match, leetify_data, skip_player_lookup=skip_player_lookup)
    return match


async def import_leetify_profile(
    db: AsyncSession,
    steam64_id: str,
    api_key: str,
    *,
    session_token: str | None = None,
) -> dict:
    client = LeetifyClient(api_key, session_token=session_token)
    logger.info("Leetify import: fetching match history for %s", steam64_id)
    entries, meta = await client.get_all_profile_matches(steam64_id)
    if not entries:
        profile_total = meta.get("profile_total_matches")
        if meta.get("history_auth_required"):
            if meta.get("history_auth_failed"):
                error = (
                    "Leetify rejected your session token (HTTP 401). "
                    "Log into leetify.com, open DevTools → Network → games/history, "
                    "and paste the full Authorization header (Bearer eyJ...) into Settings."
                )
            else:
                error = (
                    "Leetify full history needs a valid session token in Settings. "
                    "Copy it from DevTools while logged into leetify.com (games/history request → Authorization header)."
                )
        elif profile_total:
            error = (
                f"Leetify profile lists {profile_total} matches but no history could be fetched. "
                "Check session token or wait if rate limited (429)."
            )
        else:
            error = "Could not fetch Leetify match history (check API key, session token, or rate limits)"
        logger.warning("Leetify import: no matches fetched for %s (%s)", steam64_id, error)
        return {
            "total": 0,
            "imported": 0,
            "updated": 0,
            "failed": 0,
            "profile_total_matches": profile_total,
            "error": error,
        }

    use_history_stubs = meta.get("import_source") == "games_history"

    logger.info(
        "Leetify import: processing %d matches via %s (profile has %s total on Leetify)",
        len(entries),
        meta.get("import_source", "unknown"),
        meta.get("profile_total_matches"),
    )
    imported = 0
    updated = 0
    failed = 0

    for idx, entry in enumerate(entries, start=1):
        try:
            data = normalize_leetify_match_data(entry)
            if not use_history_stubs and _needs_full_leetify_match(data):
                full, _ = await client.get_match_by_game_id(str(data["id"]))
                if full:
                    data = normalize_leetify_match_data(full)

            existing = await find_match_for_leetify(db, data)
            if existing:
                await apply_leetify_match(
                    db,
                    existing,
                    data,
                    skip_player_lookup=use_history_stubs,
                )
                updated += 1
            else:
                await create_match_from_leetify(
                    db,
                    data,
                    steam64_id,
                    skip_player_lookup=use_history_stubs,
                )
                imported += 1
        except Exception:
            logger.exception("Failed to import Leetify match %s", entry.get("id"))
            failed += 1

        if idx % 100 == 0:
            await db.commit()
            logger.info(
                "Leetify import progress: %d/%d (%d new, %d updated, %d failed)",
                idx,
                len(entries),
                imported,
                updated,
                failed,
            )

    await db.commit()

    message_parts = [
        f"Import ({meta.get('import_source', 'leetify')}): {imported} new, {updated} updated from {len(entries)} games",
    ]
    if meta.get("api_limit_note"):
        message_parts.append(str(meta["api_limit_note"]))
    if use_history_stubs:
        message_parts.append(
            "Player names and full scoreboards were skipped — use Enrich existing matches (Leetify) separately."
        )
    elif meta.get("history_auth_required"):
        message_parts.append(
            "Full Leetify history needs a browser session token — add it in Settings "
            "(DevTools → Network → games/history → copy Authorization header)."
        )
    if imported == 0 and updated > 0:
        message_parts.append(
            "All recent Leetify matches were already in your database — stats were refreshed."
        )

    logger.info("Leetify import finished: %s", " · ".join(message_parts))

    return {
        "total": len(entries),
        "imported": imported,
        "updated": updated,
        "failed": failed,
        "profile_total_matches": meta.get("profile_total_matches"),
        "message": " · ".join(message_parts),
        "api_limit_note": meta.get("api_limit_note"),
    }
