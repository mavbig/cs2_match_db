from datetime import datetime, timezone
from uuid import UUID

import logging
from arq import create_pool
from arq.connections import RedisSettings
from fastapi import APIRouter, Depends, Header, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.config import settings
from app.database import get_db
from app.models import Match, MatchPlayer, Player, SyncJob
from app.schemas import (
    DashboardOut,
    MatchIngestBatchIn,
    MatchOut,
    MatchPlayerOut,
    MatchSummaryOut,
    PlayedWithOut,
    PlayerDetailOut,
    PlayerLookupIn,
    PlayerMatchOut,
    PlayerOut,
    PlayerSyncResultOut,
    SearchResultOut,
    SettingsOut,
    SettingsUpdateIn,
    ShareCodeImportIn,
    SyncJobOut,
    SyncStatusOut,
    MatchSyncStatusOut,
)
from app.services.match_service import (
    create_sync_job,
    get_match_with_players,
    get_my_steam64_id,
    get_player_matches,
    get_played_with_stats,
    get_player_detail,
    get_setting,
    get_top_teammates,
    ingest_match,
    fetch_steam_persona_names,
    parse_steam_input,
    search_players,
    set_setting,
    upsert_player,
)
from app.services.enrichment import get_match_sync_status
from app.services.leetify_sync import extract_demo_url_from_gc, import_leetify_profile, sync_match_from_sources
from app.services.player_enrichment import enrich_player_profile
from app.services.secret_store import get_leetify_session_token, save_leetify_session_token
from app.services.steam_client import SteamClient

router = APIRouter(prefix="/api/v1", tags=["api"])
logger = logging.getLogger(__name__)


async def enqueue_player_enrichment(player_id: UUID) -> None:
    try:
        arq_redis = await create_pool(RedisSettings.from_dsn(settings.redis_url))
        await arq_redis.enqueue_job("enrich_player", str(player_id))
        await arq_redis.aclose()
    except Exception:
        pass


def verify_sync_token(x_sync_token: str = Header(...)) -> None:
    if x_sync_token != settings.api_sync_token:
        raise HTTPException(status_code=401, detail="Invalid sync token")


def _steam_profile_url(steam64_id: str, stored: str | None = None) -> str:
    if stored and stored.startswith("http"):
        return stored
    return f"https://steamcommunity.com/profiles/{steam64_id}"


def _match_to_out(match: Match) -> MatchOut:
    players = []
    for mp in match.players:
        players.append(
            MatchPlayerOut(
                player_id=mp.player_id,
                steam64_id=mp.player.steam64_id,
                name=mp.player.current_name,
                team=mp.team,
                kills=mp.kills,
                deaths=mp.deaths,
                assists=mp.assists,
                mvps=mp.mvps,
                headshot_pct=mp.headshot_pct,
                score=mp.score,
                ping=mp.ping,
                is_me=mp.is_me,
                times_played_with_me=None,
            )
        )
    return MatchOut(
        id=match.id,
        source=match.source,
        source_match_id=match.source_match_id,
        map=match.map,
        mode=match.mode,
        played_at=match.played_at,
        score_team_a=match.score_team_a,
        score_team_b=match.score_team_b,
        duration_seconds=match.duration_seconds,
        share_code=match.share_code,
        demo_url=extract_demo_url_from_gc(match.raw_payload),
        sync_status=MatchSyncStatusOut(**get_match_sync_status(match.raw_payload, match.source)),
        players=players,
    )


def _match_summary(match: Match) -> MatchSummaryOut:
    return MatchSummaryOut(
        id=match.id,
        source=match.source,
        source_match_id=match.source_match_id,
        map=match.map,
        mode=match.mode,
        played_at=match.played_at,
        score_team_a=match.score_team_a,
        score_team_b=match.score_team_b,
        player_count=len(match.players) if match.players else 0,
    )


@router.get("/health")
async def health():
    return {"status": "ok"}


@router.post("/ingest/matches", dependencies=[Depends(verify_sync_token)])
async def ingest_matches_batch(body: MatchIngestBatchIn, db: AsyncSession = Depends(get_db)):
    created = 0
    updated = 0

    steam_ids: list[str] = []
    for m in body.matches:
        if m.source == "steam_gc":
            steam_ids.extend(p.steam64_id for p in m.players)
    steam_names = await fetch_steam_persona_names(db, steam_ids)

    for m in body.matches:
        names = steam_names if m.source == "steam_gc" else None
        _, was_created = await ingest_match(db, m, steam_names=names)
        if was_created:
            created += 1
        else:
            updated += 1
    return {"created": created, "updated": updated, "total": len(body.matches)}


@router.get("/matches", response_model=list[MatchSummaryOut])
async def list_matches(
    limit: int = Query(20, le=100),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Match)
        .options(selectinload(Match.players))
        .order_by(Match.played_at.desc().nullslast())
        .offset(offset)
        .limit(limit)
    )
    return [_match_summary(m) for m in result.scalars().all()]


def _gc_parse_hints(raw: dict | None) -> dict | None:
    if not raw:
        return None
    from app.services.leetify_sync import reparse_gc_payload

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

    reservation = last.get("reservation") or {}
    watchable = raw.get("watchablematchinfo") or {}
    rankings = reservation.get("rankings") or []
    parsed = reparse_gc_payload(raw)

    return {
        "roundstats_count": len(all_rs),
        "picked_round": last.get("round"),
        "picked_team_scores": last.get("team_scores"),
        "parsed_scores": [parsed.get("score_team_a"), parsed.get("score_team_b")],
        "game_type": reservation.get("game_type"),
        "rank_type_ids": [r.get("rank_type_id") for r in rankings if r.get("rank_type_id") is not None],
        "watchable_game_map": watchable.get("game_map"),
        "final_map_field": last.get("map"),
        "final_map_id": last.get("map_id"),
        "match_result": last.get("match_result"),
    }


@router.get("/matches/count")
async def count_matches(db: AsyncSession = Depends(get_db)):
    total = await db.scalar(select(func.count()).select_from(Match))
    return {"total": total or 0}


@router.post("/matches/{match_id}/sync")
async def sync_match(match_id: UUID, db: AsyncSession = Depends(get_db)):
    try:
        result = await sync_match_from_sources(db, match_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    await db.commit()
    return result


@router.get("/matches/{match_id}/demo-url")
async def get_match_demo_url(match_id: UUID, db: AsyncSession = Depends(get_db)):
    match = await get_match_with_players(db, match_id)
    if not match:
        raise HTTPException(status_code=404, detail="Match not found")
    demo_url = extract_demo_url_from_gc(match.raw_payload)
    if not demo_url:
        raise HTTPException(status_code=404, detail="No demo URL available for this match")
    return {"demo_url": demo_url}


@router.get("/matches/{match_id}/gc-debug")
async def match_gc_debug(match_id: UUID, db: AsyncSession = Depends(get_db)):
    match = await get_match_with_players(db, match_id)
    if not match:
        raise HTTPException(status_code=404, detail="Match not found")
    if match.source != "steam_gc":
        raise HTTPException(status_code=400, detail="Debug export only available for steam_gc matches")

    return {
        "match_id": str(match.id),
        "source_match_id": match.source_match_id,
        "share_code": match.share_code,
        "stored": {
            "map": match.map,
            "mode": match.mode,
            "score_team_a": match.score_team_a,
            "score_team_b": match.score_team_b,
            "played_at": match.played_at.isoformat() if match.played_at else None,
            "duration_seconds": match.duration_seconds,
        },
        "parse_hints": _gc_parse_hints(match.raw_payload),
        "raw_payload": match.raw_payload,
    }


@router.get("/matches/{match_id}", response_model=MatchOut)
async def get_match(match_id: UUID, db: AsyncSession = Depends(get_db)):
    match = await get_match_with_players(db, match_id)
    if not match:
        raise HTTPException(status_code=404, detail="Match not found")
    out = _match_to_out(match)

    me = next((p for p in match.players if p.is_me), None)
    if not me:
        return out

    other_player_ids = [mp.player_id for mp in match.players if not mp.is_me]
    if not other_player_ids:
        return out

    shared_match_ids = (
        select(MatchPlayer.match_id)
        .where(
            MatchPlayer.player_id == me.player_id,
            MatchPlayer.is_me.is_(True),
        )
        .subquery()
    )

    rows = await db.execute(
        select(
            MatchPlayer.player_id,
            func.count(func.distinct(MatchPlayer.match_id)).label("times"),
        )
        .select_from(MatchPlayer)
        .where(
            MatchPlayer.player_id.in_(other_player_ids),
            MatchPlayer.match_id.in_(select(shared_match_ids.c.match_id)),
        )
        .group_by(MatchPlayer.player_id)
    )
    times_by_player = {pid: int(times or 0) for pid, times in rows.all()}

    out.players = [
        p.model_copy(update={"times_played_with_me": times_by_player.get(p.player_id) if not p.is_me else None})
        for p in out.players
    ]
    return out


@router.get("/players", response_model=SearchResultOut)
async def search_players_route(q: str = Query(..., min_length=1), db: AsyncSession = Depends(get_db)):
    players = await search_players(db, q)
    return SearchResultOut(
        players=[
            PlayerOut(
                id=p.id,
                steam64_id=p.steam64_id,
                current_name=p.current_name,
                avatar_url=p.avatar_url,
                profile_url=_steam_profile_url(p.steam64_id, p.profile_url),
                first_seen_at=p.first_seen_at,
                last_seen_at=p.last_seen_at,
            )
            for p in players
        ]
    )


@router.get("/players/{player_id}", response_model=PlayerDetailOut)
async def get_player(player_id: UUID, db: AsyncSession = Depends(get_db)):
    player = await get_player_detail(db, player_id)
    if not player:
        raise HTTPException(status_code=404, detail="Player not found")

    match_count = (
        await db.execute(select(func.count()).select_from(MatchPlayer).where(MatchPlayer.player_id == player_id))
    ).scalar() or 0

    stats = {}
    if player.stat_snapshots:
        latest = sorted(player.stat_snapshots, key=lambda s: s.captured_at, reverse=True)
        for snap in latest:
            if snap.source not in stats:
                stats[snap.source] = snap.payload

    played = await get_played_with_stats(db, player.steam64_id)

    return PlayerDetailOut(
        id=player.id,
        steam64_id=player.steam64_id,
        current_name=player.current_name,
        avatar_url=player.avatar_url,
        profile_url=_steam_profile_url(player.steam64_id, player.profile_url),
        first_seen_at=player.first_seen_at,
        last_seen_at=player.last_seen_at,
        name_history=[h.name for h in player.name_history],
        platform_accounts=[
            {
                "platform": a.platform,
                "external_id": a.external_id,
                "nickname": a.nickname,
                "profile_url": a.profile_url,
            }
            for a in player.platform_accounts
        ],
        latest_stats=stats,
        match_count=match_count,
        times_played_with_me=played["times_together"] if played else None,
    )


@router.get("/players/{player_id}/matches", response_model=list[PlayerMatchOut])
async def list_player_matches(
    player_id: UUID,
    limit: int = Query(100, le=200),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
):
    player = await db.get(Player, player_id)
    if not player:
        raise HTTPException(status_code=404, detail="Player not found")

    rows = await get_player_matches(db, player_id, limit=limit, offset=offset)
    out: list[PlayerMatchOut] = []
    for match, mp in rows:
        out.append(
            PlayerMatchOut(
                id=match.id,
                source=match.source,
                source_match_id=match.source_match_id,
                map=match.map,
                mode=match.mode,
                played_at=match.played_at,
                score_team_a=match.score_team_a,
                score_team_b=match.score_team_b,
                player_count=len(match.players) if match.players else 0,
                kills=mp.kills,
                deaths=mp.deaths,
                assists=mp.assists,
                mvps=mp.mvps,
                headshot_pct=mp.headshot_pct,
                score=mp.score,
            )
        )
    return out


@router.post("/players/{player_id}/sync", response_model=PlayerSyncResultOut)
async def sync_player_profile(player_id: UUID, db: AsyncSession = Depends(get_db)):
    try:
        result = await enrich_player_profile(db, player_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    await db.commit()
    return PlayerSyncResultOut(**result)


@router.get("/players/{player_id}/profile-debug")
async def get_player_profile_debug(player_id: UUID, db: AsyncSession = Depends(get_db)):
    player = await get_player_detail(db, player_id)
    if not player:
        raise HTTPException(status_code=404, detail="Player not found")

    snapshots: dict[str, dict] = {}
    for snap in sorted(player.stat_snapshots or [], key=lambda row: row.captured_at, reverse=True):
        if snap.source in snapshots:
            continue
        snapshots[snap.source] = {
            "captured_at": snap.captured_at.isoformat(),
            "payload": snap.payload,
        }

    return {
        "player_id": str(player_id),
        "steam64_id": player.steam64_id,
        "current_name": player.current_name,
        "platform_accounts": [
            {
                "platform": account.platform,
                "external_id": account.external_id,
                "nickname": account.nickname,
                "profile_url": account.profile_url,
            }
            for account in player.platform_accounts
        ],
        "summary": _build_profile_debug_summary(snapshots),
        "snapshots": snapshots,
    }


def _build_profile_debug_summary(snapshots: dict[str, dict]) -> dict:
    faceit_snap = snapshots.get("faceit") or {}
    faceit_payload = faceit_snap.get("payload") or {}
    faceit_debug = faceit_payload.get("_profile_debug") or {}
    return {
        "faceit_captured_at": faceit_snap.get("captured_at"),
        "faceit_lifetime_in_snapshot": faceit_payload.get("lifetime"),
        "faceit_recent_in_snapshot": faceit_payload.get("recent_20"),
        "faceit_normalized_lifetime": faceit_debug.get("normalized_lifetime"),
        "faceit_api_lifetime_keys": sorted((faceit_debug.get("api_lifetime") or {}).keys()),
        "faceit_merged_lifetime_keys": sorted((faceit_debug.get("merged_lifetime") or {}).keys()),
    }


@router.get("/players/by-steam/{steam64_id}/played-with", response_model=PlayedWithOut)
async def played_with(steam64_id: str, db: AsyncSession = Depends(get_db)):
    stats = await get_played_with_stats(db, steam64_id)
    if not stats:
        raise HTTPException(status_code=404, detail="Player not found or not configured")

    shared = await db.execute(
        select(Match)
        .join(MatchPlayer, MatchPlayer.match_id == Match.id)
        .join(Player, Player.id == MatchPlayer.player_id)
        .where(Player.steam64_id == steam64_id)
        .options(selectinload(Match.players))
        .order_by(Match.played_at.desc().nullslast())
        .limit(20)
    )
    matches = shared.scalars().all()

    return PlayedWithOut(
        player=PlayerOut.model_validate(stats["player"]),
        times_together=stats["times_together"],
        first_together=stats["first_together"],
        last_together=stats["last_together"],
        shared_matches=[_match_summary(m) for m in matches],
    )


@router.post("/players/lookup", response_model=PlayerDetailOut)
async def lookup_player(body: PlayerLookupIn, db: AsyncSession = Depends(get_db)):
    steam64, vanity = parse_steam_input(body.steam_url_or_id)
    steam_client = SteamClient()

    if vanity and not steam64:
        steam64 = await steam_client.resolve_vanity_url(vanity)
        if not steam64:
            raise HTTPException(status_code=404, detail="Could not resolve Steam profile")

    if not steam64:
        raise HTTPException(status_code=400, detail="Invalid Steam URL or ID")

    summaries = await steam_client.get_player_summaries([steam64])
    summary = summaries[0] if summaries else {}
    player = await upsert_player(
        db,
        steam64,
        name=summary.get("personaname"),
        avatar_url=summary.get("avatarfull"),
        profile_url=summary.get("profileurl"),
    )

    await enqueue_player_enrichment(player.id)
    detail = await get_player_detail(db, player.id)
    assert detail is not None

    played = await get_played_with_stats(db, steam64)
    match_count = (
        await db.execute(select(func.count()).select_from(MatchPlayer).where(MatchPlayer.player_id == player.id))
    ).scalar() or 0

    return PlayerDetailOut(
        id=detail.id,
        steam64_id=detail.steam64_id,
        current_name=detail.current_name,
        avatar_url=detail.avatar_url,
        profile_url=_steam_profile_url(detail.steam64_id, detail.profile_url),
        first_seen_at=detail.first_seen_at,
        last_seen_at=detail.last_seen_at,
        name_history=[h.name for h in detail.name_history],
        platform_accounts=[],
        latest_stats={},
        match_count=match_count,
        times_played_with_me=played["times_together"] if played else 0,
    )


@router.get("/dashboard", response_model=DashboardOut)
async def dashboard(db: AsyncSession = Depends(get_db)):
    recent = await db.execute(
        select(Match).options(selectinload(Match.players)).order_by(Match.played_at.desc().nullslast()).limit(10)
    )
    top = await get_top_teammates(db, 5)

    total_matches = (await db.execute(select(func.count()).select_from(Match))).scalar() or 0
    total_players = (await db.execute(select(func.count()).select_from(Player))).scalar() or 0
    pending = (
        await db.execute(select(func.count()).select_from(SyncJob).where(SyncJob.status == "pending"))
    ).scalar() or 0

    auth_code = await get_setting(db, "steam_auth_code")
    faceit_key = await get_setting(db, "faceit_api_key")

    last_steam = (
        await db.execute(
            select(SyncJob.finished_at)
            .where(SyncJob.job_type == "steam_gc", SyncJob.status == "completed")
            .order_by(SyncJob.finished_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()

    last_faceit = (
        await db.execute(
            select(SyncJob.finished_at)
            .where(SyncJob.job_type == "faceit", SyncJob.status == "completed")
            .order_by(SyncJob.finished_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()

    sync_status = SyncStatusOut(
        last_steam_sync=last_steam,
        last_faceit_sync=last_faceit,
        total_matches=total_matches,
        total_players=total_players,
        pending_jobs=pending,
        steam_configured=bool(auth_code or settings.steam_auth_code),
        faceit_configured=bool(faceit_key or settings.faceit_api_key),
    )

    teammates = []
    for t in top:
        teammates.append(
            PlayedWithOut(
                player=PlayerOut.model_validate(t["player"]),
                times_together=t["times_together"],
                first_together=None,
                last_together=t["last_together"],
            )
        )

    return DashboardOut(
        recent_matches=[_match_summary(m) for m in recent.scalars().all()],
        top_teammates=teammates,
        sync_status=sync_status,
    )


@router.get("/settings", response_model=SettingsOut)
async def get_settings(db: AsyncSession = Depends(get_db)):
    auth = await get_setting(db, "steam_auth_code") or settings.steam_auth_code
    share = await get_setting(db, "steam_oldest_share_code") or settings.steam_oldest_share_code
    steam_key = await get_setting(db, "steam_api_key") or settings.steam_api_key
    faceit_key = await get_setting(db, "faceit_api_key") or settings.faceit_api_key
    faceit_nick = await get_setting(db, "faceit_nickname") or settings.faceit_nickname
    leetify_key = await get_setting(db, "leetify_api_key") or settings.leetify_api_key
    leetify_session = await get_leetify_session_token(db)
    my_id = await get_my_steam64_id(db)

    onboarding = bool(my_id and auth and share)
    return SettingsOut(
        my_steam64_id=my_id or None,
        steam_auth_code_set=bool(auth),
        steam_oldest_share_code_set=bool(share),
        steam_api_key_set=bool(steam_key),
        faceit_api_key_set=bool(faceit_key),
        faceit_nickname=faceit_nick or None,
        leetify_api_key_set=bool(leetify_key),
        leetify_session_token_set=bool(leetify_session),
        onboarding_complete=onboarding,
    )


@router.put("/settings", response_model=SettingsOut)
async def update_settings(body: SettingsUpdateIn, db: AsyncSession = Depends(get_db)):
    if body.my_steam64_id is not None:
        await set_setting(db, "my_steam64_id", body.my_steam64_id)
    if body.steam_auth_code is not None:
        await set_setting(db, "steam_auth_code", body.steam_auth_code)
    if body.steam_oldest_share_code is not None:
        await set_setting(db, "steam_oldest_share_code", body.steam_oldest_share_code)
    if body.steam_api_key is not None:
        await set_setting(db, "steam_api_key", body.steam_api_key)
    if body.faceit_api_key is not None:
        await set_setting(db, "faceit_api_key", body.faceit_api_key)
    if body.faceit_nickname is not None:
        await set_setting(db, "faceit_nickname", body.faceit_nickname)
    if body.leetify_api_key is not None:
        await set_setting(db, "leetify_api_key", body.leetify_api_key)
    if body.leetify_session_token is not None:
        await save_leetify_session_token(db, body.leetify_session_token)
    return await get_settings(db)


@router.post("/sync/trigger/{job_type}", response_model=SyncJobOut)
async def trigger_sync(job_type: str, db: AsyncSession = Depends(get_db)):
    if job_type not in ("steam_gc", "faceit", "enrichment", "leetify", "leetify_import"):
        raise HTTPException(status_code=400, detail="Invalid job type")
    job = await create_sync_job(db, job_type)

    if job_type == "steam_gc":
        await set_setting(db, "steam_sync_force_full", "1")

    try:
        arq_redis = await create_pool(RedisSettings.from_dsn(settings.redis_url))
        if job_type == "enrichment":
            await arq_redis.enqueue_job("run_enrichment_batch")
        elif job_type == "faceit":
            await arq_redis.enqueue_job("sync_faceit_matches")
        elif job_type == "leetify":
            await arq_redis.enqueue_job("sync_leetify_matches")
        elif job_type == "leetify_import":
            await arq_redis.enqueue_job("import_leetify_profile", str(job.id))
        await arq_redis.aclose()
    except Exception:
        pass

    return SyncJobOut.model_validate(job)


@router.post("/import/leetify")
async def import_leetify(db: AsyncSession = Depends(get_db)):
    my_steam64 = await get_my_steam64_id(db)
    if not my_steam64:
        raise HTTPException(status_code=400, detail="Configure your Steam64 ID in settings first")
    leetify_key = await get_setting(db, "leetify_api_key") or settings.leetify_api_key
    leetify_session = await get_leetify_session_token(db)
    if not leetify_key:
        raise HTTPException(status_code=400, detail="Leetify API key not configured")

    logger.info("Starting Leetify profile import for %s", my_steam64)
    result = await import_leetify_profile(db, my_steam64, leetify_key, session_token=leetify_session)
    await db.commit()
    logger.info("Leetify profile import API finished: %s", result)
    return result


@router.get("/sync/jobs", response_model=list[SyncJobOut])
async def list_sync_jobs(limit: int = 10, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(SyncJob).order_by(SyncJob.started_at.desc().nullslast()).limit(limit))
    return [SyncJobOut.model_validate(j) for j in result.scalars().all()]


@router.get("/sync/config")
async def sync_config(db: AsyncSession = Depends(get_db), _: None = Depends(verify_sync_token)):
    force_full = await get_setting(db, "steam_sync_force_full")
    return {
        "my_steam64_id": await get_my_steam64_id(db),
        "steam_auth_code": await get_setting(db, "steam_auth_code") or settings.steam_auth_code,
        "steam_oldest_share_code": await get_setting(db, "steam_oldest_share_code") or settings.steam_oldest_share_code,
        "steam_api_key": await get_setting(db, "steam_api_key") or settings.steam_api_key,
        "force_full_sync": force_full == "1",
    }


@router.post("/sync/ack-force-full", dependencies=[Depends(verify_sync_token)])
async def ack_force_full_sync(db: AsyncSession = Depends(get_db)):
    await set_setting(db, "steam_sync_force_full", None)
    return {"ok": True}


@router.post("/sync/jobs/{job_id}/complete", dependencies=[Depends(verify_sync_token)])
async def complete_sync_job(
    job_id: UUID,
    matches_imported: int = 0,
    error: str | None = None,
    db: AsyncSession = Depends(get_db),
):
    job = await db.get(SyncJob, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    job.status = "failed" if error else "completed"
    job.finished_at = datetime.now(timezone.utc)
    job.matches_imported = matches_imported
    job.error_message = error
    return {"ok": True}


@router.post("/sync/jobs/start", dependencies=[Depends(verify_sync_token)])
async def start_sync_job(job_type: str, db: AsyncSession = Depends(get_db)):
    job = await create_sync_job(db, job_type)
    job.status = "running"
    return SyncJobOut.model_validate(job)


@router.post("/import/share-code")
async def import_share_code(body: ShareCodeImportIn, db: AsyncSession = Depends(get_db)):
    job = await create_sync_job(db, f"share_code:{body.share_code}")
    return {"job_id": str(job.id), "message": "Share code queued for import via steam-sync"}
