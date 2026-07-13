import re
from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.config import settings
from app.models import (
    AppSettings,
    Match,
    MatchPlayer,
    Player,
    PlayerNameHistory,
    PlayerPlatformAccount,
    PlayerStatSnapshot,
    SyncJob,
)
from app.schemas import MatchIngestIn, MatchPlayerIn
from app.services.enrichment import touch_enrichment
from app.services.steam_client import SteamClient


STEAM64_RE = re.compile(r"^\d{17}$")
STEAM_URL_RE = re.compile(
    r"(?:https?://)?(?:www\.)?steamcommunity\.com/(?:profiles/(?P<profile_id>\d+)|id/(?P<vanity>[^/\s?#]+))",
    re.I,
)


async def get_setting(db: AsyncSession, key: str) -> str | None:
    row = await db.get(AppSettings, key)
    return row.value if row else None


async def set_setting(db: AsyncSession, key: str, value: str | None) -> None:
    row = await db.get(AppSettings, key)
    if row:
        row.value = value
        row.updated_at = datetime.now(timezone.utc)
    else:
        db.add(AppSettings(key=key, value=value))


async def get_my_steam64_id(db: AsyncSession) -> str:
    val = await get_setting(db, "my_steam64_id")
    return val or settings.my_steam64_id


async def upsert_player(
    db: AsyncSession,
    steam64_id: str,
    name: str | None = None,
    avatar_url: str | None = None,
    profile_url: str | None = None,
) -> Player:
    result = await db.execute(select(Player).where(Player.steam64_id == steam64_id))
    player = result.scalar_one_or_none()
    now = datetime.now(timezone.utc)

    if player is None:
        player = Player(
            steam64_id=steam64_id,
            current_name=name,
            avatar_url=avatar_url,
            profile_url=profile_url or f"https://steamcommunity.com/profiles/{steam64_id}/",
            first_seen_at=now,
            last_seen_at=now,
        )
        db.add(player)
        await db.flush()
        if name:
            db.add(PlayerNameHistory(player_id=player.id, name=name, first_seen_at=now, last_seen_at=now))
    else:
        player.last_seen_at = now
        if avatar_url:
            player.avatar_url = avatar_url
        if profile_url:
            player.profile_url = profile_url
        if name and name != player.current_name:
            player.current_name = name
            hist = await db.execute(
                select(PlayerNameHistory).where(
                    PlayerNameHistory.player_id == player.id,
                    PlayerNameHistory.name == name,
                )
            )
            existing = hist.scalar_one_or_none()
            if existing:
                existing.last_seen_at = now
            else:
                db.add(PlayerNameHistory(player_id=player.id, name=name, first_seen_at=now, last_seen_at=now))

    await db.flush()
    return player


async def fetch_steam_persona_names(db: AsyncSession, steam64_ids: list[str]) -> dict[str, str]:
    api_key = await get_setting(db, "steam_api_key") or settings.steam_api_key
    if not api_key or not steam64_ids:
        return {}

    client = SteamClient(api_key)
    names: dict[str, str] = {}
    unique_ids = list(dict.fromkeys(steam64_ids))
    for i in range(0, len(unique_ids), 100):
        batch = unique_ids[i : i + 100]
        for player in await client.get_player_summaries(batch):
            steam64 = player.get("steamid")
            persona = player.get("personaname")
            if steam64 and persona:
                names[str(steam64)] = str(persona)
    return names


async def ingest_match(
    db: AsyncSession,
    data: MatchIngestIn,
    steam_names: dict[str, str] | None = None,
) -> tuple[Match, bool]:
    existing = await db.execute(
        select(Match).where(
            Match.source == data.source,
            Match.source_match_id == data.source_match_id,
        )
    )
    match = existing.scalar_one_or_none()
    created = match is None

    if match is None and data.share_code:
        by_share = await db.execute(select(Match).where(Match.share_code == data.share_code))
        match = by_share.scalar_one_or_none()
        if match is not None:
            created = False

    if match is None:
        payload = data.raw_payload
        if payload is not None:
            payload = touch_enrichment(
                payload,
                steam_synced_at=datetime.now(timezone.utc).isoformat(),
            )
        match = Match(
            source=data.source,
            source_match_id=data.source_match_id,
            map=data.map,
            mode=data.mode,
            played_at=data.played_at,
            score_team_a=data.score_team_a,
            score_team_b=data.score_team_b,
            duration_seconds=data.duration_seconds,
            share_code=data.share_code,
            raw_payload=payload,
        )
        db.add(match)
        await db.flush()
    else:
        if data.map:
            match.map = data.map
        if data.mode:
            match.mode = data.mode
        if data.played_at:
            match.played_at = data.played_at
        if data.score_team_a is not None:
            match.score_team_a = data.score_team_a
        if data.score_team_b is not None:
            match.score_team_b = data.score_team_b
        if data.duration_seconds is not None:
            match.duration_seconds = data.duration_seconds
        if data.share_code:
            match.share_code = data.share_code
        if data.raw_payload:
            existing_payload = dict(match.raw_payload or {})
            existing_enrichment = dict(existing_payload.get("_enrichment") or {})
            payload = touch_enrichment(
                data.raw_payload,
                steam_synced_at=datetime.now(timezone.utc).isoformat(),
            )
            merged_enrichment = dict(payload.get("_enrichment") or {})
            merged_enrichment = {**existing_enrichment, **merged_enrichment}
            payload["_enrichment"] = merged_enrichment
            if match.source == "leetify" and data.source == "steam_gc":
                merged_enrichment["steam_gc_match_id"] = data.source_match_id
                payload["_enrichment"] = merged_enrichment
            match.raw_payload = payload

    my_steam64 = await get_my_steam64_id(db)

    for p in data.players:
        player_name = (steam_names or {}).get(p.steam64_id) or p.name
        player = await upsert_player(db, p.steam64_id, name=player_name)
        is_me = p.is_me or p.steam64_id == my_steam64

        mp_result = await db.execute(
            select(MatchPlayer).where(
                MatchPlayer.match_id == match.id,
                MatchPlayer.player_id == player.id,
            )
        )
        mp = mp_result.scalar_one_or_none()
        if mp is None:
            mp = MatchPlayer(
                match_id=match.id,
                player_id=player.id,
                team=p.team,
                kills=p.kills,
                deaths=p.deaths,
                assists=p.assists,
                mvps=p.mvps,
                headshot_pct=p.headshot_pct,
                score=p.score,
                ping=p.ping,
                is_me=is_me,
            )
            db.add(mp)
        else:
            mp.team = p.team or mp.team
            mp.kills = p.kills if p.kills is not None else mp.kills
            mp.deaths = p.deaths if p.deaths is not None else mp.deaths
            mp.assists = p.assists if p.assists is not None else mp.assists
            mp.mvps = p.mvps if p.mvps is not None else mp.mvps
            mp.headshot_pct = p.headshot_pct if p.headshot_pct is not None else mp.headshot_pct
            mp.score = p.score if p.score is not None else mp.score
            mp.ping = p.ping if p.ping is not None else mp.ping
            mp.is_me = is_me

    await db.flush()
    return match, created


def parse_steam_input(value: str) -> tuple[str | None, str | None]:
    value = value.strip()
    if STEAM64_RE.match(value):
        return value, None
    m = STEAM_URL_RE.search(value)
    if m:
        if m.group("profile_id"):
            return m.group("profile_id"), None
        return None, m.group("vanity")
    if "/" not in value and len(value) >= 2:
        return None, value
    return None, None


async def get_match_with_players(db: AsyncSession, match_id: UUID) -> Match | None:
    result = await db.execute(
        select(Match)
        .options(selectinload(Match.players).selectinload(MatchPlayer.player))
        .where(Match.id == match_id)
    )
    return result.scalar_one_or_none()


async def get_player_matches(
    db: AsyncSession,
    player_id: UUID,
    limit: int = 100,
    offset: int = 0,
) -> list[tuple[Match, MatchPlayer]]:
    result = await db.execute(
        select(Match, MatchPlayer)
        .join(MatchPlayer, MatchPlayer.match_id == Match.id)
        .where(MatchPlayer.player_id == player_id)
        .options(selectinload(Match.players))
        .order_by(Match.played_at.desc().nullslast())
        .offset(offset)
        .limit(limit)
    )
    return list(result.all())


async def search_players(db: AsyncSession, query: str, limit: int = 20) -> list[Player]:
    q = query.strip()
    steam64, vanity = parse_steam_input(q)

    if steam64:
        result = await db.execute(select(Player).where(Player.steam64_id == steam64))
        player = result.scalar_one_or_none()
        return [player] if player else []

    stmt = (
        select(Player)
        .outerjoin(PlayerNameHistory, PlayerNameHistory.player_id == Player.id)
        .where(
            or_(
                Player.current_name.ilike(f"%{q}%"),
                PlayerNameHistory.name.ilike(f"%{q}%"),
                Player.steam64_id == q,
            )
        )
        .distinct()
        .limit(limit)
    )
    result = await db.execute(stmt)
    return list(result.scalars().all())


async def get_played_with_stats(db: AsyncSession, target_steam64: str) -> dict | None:
    my_steam64 = await get_my_steam64_id(db)
    if not my_steam64:
        return None

    target_result = await db.execute(select(Player).where(Player.steam64_id == target_steam64))
    target = target_result.scalar_one_or_none()
    if not target:
        return None

    me_result = await db.execute(select(Player).where(Player.steam64_id == my_steam64))
    me = me_result.scalar_one_or_none()
    if not me:
        return None

    stmt = (
        select(
            func.count(func.distinct(MatchPlayer.match_id)).label("times"),
            func.min(Match.played_at).label("first_at"),
            func.max(Match.played_at).label("last_at"),
        )
        .select_from(MatchPlayer)
        .join(Match, Match.id == MatchPlayer.match_id)
        .where(
            MatchPlayer.player_id == target.id,
            MatchPlayer.match_id.in_(
                select(MatchPlayer.match_id).where(
                    MatchPlayer.player_id == me.id,
                    MatchPlayer.is_me.is_(True),
                )
            ),
        )
    )
    row = (await db.execute(stmt)).one()
    return {
        "player": target,
        "times_together": row.times or 0,
        "first_together": row.first_at,
        "last_together": row.last_at,
    }


async def get_top_teammates(db: AsyncSession, limit: int = 10, offset: int = 0) -> list[dict]:
    my_steam64 = await get_my_steam64_id(db)
    if not my_steam64:
        return []

    me_result = await db.execute(select(Player).where(Player.steam64_id == my_steam64))
    me = me_result.scalar_one_or_none()
    if not me:
        return []

    stmt = (
        select(
            Player,
            func.count(func.distinct(MatchPlayer.match_id)).label("times"),
            func.max(Match.played_at).label("last_at"),
        )
        .join(MatchPlayer, MatchPlayer.player_id == Player.id)
        .join(Match, Match.id == MatchPlayer.match_id)
        .where(
            MatchPlayer.match_id.in_(
                select(MatchPlayer.match_id).where(
                    MatchPlayer.player_id == me.id,
                    MatchPlayer.is_me.is_(True),
                )
            ),
            Player.id != me.id,
        )
        .group_by(Player.id)
        .order_by(func.count(func.distinct(MatchPlayer.match_id)).desc())
        .offset(offset)
        .limit(limit)
    )
    rows = (await db.execute(stmt)).all()
    return [{"player": r[0], "times_together": r[1], "last_together": r[2]} for r in rows]


async def create_sync_job(db: AsyncSession, job_type: str) -> SyncJob:
    job = SyncJob(job_type=job_type, status="pending", started_at=datetime.now(timezone.utc))
    db.add(job)
    await db.flush()
    return job


async def get_player_detail(db: AsyncSession, player_id: UUID) -> Player | None:
    result = await db.execute(
        select(Player)
        .options(
            selectinload(Player.name_history),
            selectinload(Player.platform_accounts),
            selectinload(Player.stat_snapshots),
        )
        .where(Player.id == player_id)
    )
    return result.scalar_one_or_none()


async def save_stat_snapshot(db: AsyncSession, player_id: UUID, source: str, payload: dict) -> None:
    db.add(PlayerStatSnapshot(player_id=player_id, source=source, payload=payload))


async def upsert_platform_account(
    db: AsyncSession,
    player_id: UUID,
    platform: str,
    external_id: str,
    nickname: str | None = None,
    profile_url: str | None = None,
) -> None:
    result = await db.execute(
        select(PlayerPlatformAccount).where(
            PlayerPlatformAccount.platform == platform,
            PlayerPlatformAccount.external_id == external_id,
        )
    )
    acct = result.scalar_one_or_none()
    if acct is None:
        db.add(
            PlayerPlatformAccount(
                player_id=player_id,
                platform=platform,
                external_id=external_id,
                nickname=nickname,
                profile_url=profile_url,
            )
        )
    else:
        acct.nickname = nickname or acct.nickname
        acct.profile_url = profile_url or acct.profile_url
