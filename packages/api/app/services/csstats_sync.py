"""Import matches from csstats.gg HTML."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.config import settings
from app.models import Match, MatchPlayer
from app.schemas import MatchIngestIn, MatchPlayerIn
from app.services.csstats_parser import (
    CsstatsMatchSummary,
    CsstatsPlayerRow,
    CsstatsProfileMatchStub,
    extract_csstats_match_id,
    parse_csstats_match_html,
    parse_csstats_profile_stats_html,
)
from app.services.csstats_client import CsstatsClient, CsstatsFetchError
from app.services.enrichment import touch_enrichment
from app.services.match_service import fetch_steam_persona_names, get_my_steam64_id, get_setting, ingest_match

logger = logging.getLogger(__name__)

COMMIT_EVERY = 25


def _parse_score(score: str | None) -> tuple[int | None, int | None]:
    if not score or ":" not in score:
        return None, None
    left, right = score.split(":", 1)
    try:
        return int(left), int(right)
    except ValueError:
        return None, None


def _summary_to_ingest(
    summary: CsstatsMatchSummary,
    *,
    source: str,
    source_match_id: str,
    my_steam64: str | None,
) -> MatchIngestIn:
    players = [
        MatchPlayerIn(
            steam64_id=p.steam64_id,
            name=p.name,
            team=p.team,
            kills=p.kills,
            deaths=p.deaths,
            assists=p.assists,
            headshot_pct=p.headshot_pct,
            score=int(round(p.rating * 100)) if p.rating is not None else None,
            is_me=my_steam64 == p.steam64_id if my_steam64 else False,
        )
        for p in summary.players
    ]

    payload = touch_enrichment(
        {
            "_source": "csstats_import",
            "csstats_match_id": summary.match_id,
        },
        csstats_synced_at=datetime.now(timezone.utc).isoformat(),
    )

    return MatchIngestIn(
        source=source,
        source_match_id=source_match_id,
        map=summary.map,
        mode=summary.mode,
        played_at=summary.played_at,
        score_team_a=summary.score_team_a,
        score_team_b=summary.score_team_b,
        raw_payload=payload,
        players=players,
    )


async def find_match_for_csstats(db: AsyncSession, summary: CsstatsMatchSummary) -> Match | None:
    match_id = summary.match_id
    conditions = [
        (Match.source == "csstats") & (Match.source_match_id == match_id),
        Match.raw_payload["csstats_match_id"].astext == match_id,
        Match.raw_payload["_enrichment"]["csstats_match_id"].astext == match_id,
    ]

    result = await db.execute(
        select(Match)
        .options(selectinload(Match.players).selectinload(MatchPlayer.player))
        .where(or_(*conditions))
        .limit(1)
    )
    match = result.scalar_one_or_none()
    if match:
        return match

    if summary.played_at and summary.map:
        result = await db.execute(
            select(Match)
            .options(selectinload(Match.players).selectinload(MatchPlayer.player))
            .where(Match.played_at >= summary.played_at - timedelta(minutes=5))
            .where(Match.played_at <= summary.played_at + timedelta(minutes=5))
            .where(Match.map == summary.map.lower())
            .limit(1)
        )
        return result.scalar_one_or_none()

    return None


async def _get_csstats_cookie(db: AsyncSession) -> str:
    return await get_setting(db, "csstats_cookie") or settings.csstats_cookie or ""


async def import_csstats_match_from_html(
    db: AsyncSession,
    html: str,
    match_id: str | None = None,
) -> tuple[Match, bool, str]:
    summary = parse_csstats_match_html(html, match_id)
    if not summary.match_id:
        raise ValueError("Could not determine csstats match ID from HTML")

    if len(summary.players) < 2:
        raise ValueError(
            f"Parsed only {len(summary.players)} players — page may be incomplete or blocked"
        )

    my_steam64 = await get_my_steam64_id(db)
    existing = await find_match_for_csstats(db, summary)
    source = existing.source if existing else "csstats"
    source_match_id = existing.source_match_id if existing else summary.match_id

    data = _summary_to_ingest(
        summary,
        source=source,
        source_match_id=source_match_id,
        my_steam64=my_steam64,
    )

    if existing and existing.raw_payload:
        merged = dict(existing.raw_payload)
        merged["csstats_match_id"] = summary.match_id
        data.raw_payload = touch_enrichment(
            merged,
            csstats_synced_at=datetime.now(timezone.utc).isoformat(),
            csstats_match_id=summary.match_id,
        )

    steam_ids = [p.steam64_id for p in data.players]
    steam_names = await fetch_steam_persona_names(db, steam_ids)
    match, created = await ingest_match(db, data, steam_names=steam_names)
    action = "imported" if created else "updated"
    return match, created, action


async def import_csstats_match_by_id(
    db: AsyncSession,
    client: CsstatsClient,
    match_id: str,
) -> tuple[Match, bool, str]:
    html = await client.fetch_match_html(match_id)
    return await import_csstats_match_from_html(db, html, match_id)


async def import_csstats_stub(
    db: AsyncSession,
    stub: CsstatsProfileMatchStub,
    my_steam64: str | None,
) -> tuple[Match, bool, str]:
    score_a, score_b = _parse_score(stub.score)
    players: list[CsstatsPlayerRow] = []
    if my_steam64:
        players.append(
            CsstatsPlayerRow(
                steam64_id=my_steam64,
                name=None,
                team="team_a",
                kills=stub.kills,
                deaths=stub.deaths,
                assists=stub.assists,
                rating=stub.rating,
            )
        )

    summary = CsstatsMatchSummary(
        match_id=stub.match_id,
        map=stub.map,
        played_at=stub.played_at,
        score_team_a=score_a,
        score_team_b=score_b,
        players=players,
    )

    existing = await find_match_for_csstats(db, summary)
    source = existing.source if existing else "csstats"
    source_match_id = existing.source_match_id if existing else stub.match_id
    data = _summary_to_ingest(summary, source=source, source_match_id=source_match_id, my_steam64=my_steam64)
    if existing and existing.raw_payload:
        merged = dict(existing.raw_payload)
        merged["csstats_match_id"] = stub.match_id
        data.raw_payload = touch_enrichment(
            merged,
            csstats_synced_at=datetime.now(timezone.utc).isoformat(),
            csstats_match_id=stub.match_id,
        )

    steam_names = await fetch_steam_persona_names(db, [p.steam64_id for p in data.players])
    match, created = await ingest_match(db, data, steam_names=steam_names)
    return match, created, "imported" if created else "updated"


async def import_csstats_profile_from_html(
    db: AsyncSession,
    html: str,
    steam64_id: str,
    *,
    cookie: str | None = None,
    limit: int | None = None,
) -> dict:
    stubs = parse_csstats_profile_stats_html(html)
    if limit is not None:
        stubs = stubs[:limit]

    if not stubs:
        return {
            "total": 0,
            "imported": 0,
            "updated": 0,
            "skipped": 0,
            "failed": 0,
            "error": "No matches found in pasted csstats profile HTML",
        }

    cookie = cookie or await _get_csstats_cookie(db)
    client = CsstatsClient(cookie=cookie, request_delay_ms=settings.csstats_request_delay_ms) if cookie else None
    my_steam64 = await get_my_steam64_id(db)

    imported = updated = skipped = failed = stub_only = 0

    for idx, stub in enumerate(stubs, start=1):
        try:
            existing = await find_match_for_csstats(
                db,
                CsstatsMatchSummary(match_id=stub.match_id, map=stub.map, played_at=stub.played_at),
            )
            if existing and existing.source == "csstats" and len(existing.players) >= 10:
                skipped += 1
                continue

            created = False
            if client:
                try:
                    _, created, _ = await import_csstats_match_by_id(db, client, stub.match_id)
                except CsstatsFetchError:
                    _, created, _ = await import_csstats_stub(db, stub, my_steam64)
                    stub_only += 1
            else:
                _, created, _ = await import_csstats_stub(db, stub, my_steam64)
                stub_only += 1

            if created:
                imported += 1
            else:
                updated += 1

            if idx % COMMIT_EVERY == 0:
                await db.commit()
        except Exception as exc:
            failed += 1
            logger.warning("csstats profile HTML import failed for %s: %s", stub.match_id, exc)

    await db.commit()
    msg = f"{imported} new, {updated} updated, {skipped} skipped, {failed} failed"
    if stub_only:
        msg += f", {stub_only} imported from profile rows only (no full scoreboard)"
    msg += f" out of {len(stubs)} matches"
    return {
        "total": len(stubs),
        "imported": imported,
        "updated": updated,
        "skipped": skipped,
        "failed": failed,
        "stub_only": stub_only,
        "message": msg,
    }


async def import_csstats_profile(
    db: AsyncSession,
    steam64_id: str,
    *,
    cookie: str | None = None,
    limit: int | None = None,
) -> dict:
    cookie = cookie or await _get_csstats_cookie(db)
    if not cookie:
        return {
            "total": 0,
            "imported": 0,
            "updated": 0,
            "skipped": 0,
            "failed": 0,
            "error": (
                "csstats Cookie not configured. Log into csstats.gg, open DevTools → Network, "
                "copy the Cookie header from any request, and paste it in Settings."
            ),
        }

    client = CsstatsClient(
        cookie=cookie,
        request_delay_ms=settings.csstats_request_delay_ms,
    )

    logger.info("csstats import: fetching profile stats for %s", steam64_id)
    try:
        stats_html = await client.fetch_profile_stats_html(steam64_id)
    except CsstatsFetchError as exc:
        return {
            "total": 0,
            "imported": 0,
            "updated": 0,
            "skipped": 0,
            "failed": 0,
            "error": str(exc),
        }
    stubs = parse_csstats_profile_stats_html(stats_html)
    if limit is not None:
        stubs = stubs[:limit]

    if not stubs:
        return {
            "total": 0,
            "imported": 0,
            "updated": 0,
            "skipped": 0,
            "failed": 0,
            "error": "No matches found in csstats profile stats HTML",
        }

    logger.info("csstats import: found %d matches in profile list", len(stubs))

    imported = updated = skipped = failed = 0

    for idx, stub in enumerate(stubs, start=1):
        try:
            existing = await find_match_for_csstats(
                db,
                CsstatsMatchSummary(
                    match_id=stub.match_id,
                    map=stub.map,
                    played_at=stub.played_at,
                ),
            )

            if existing and existing.source == "csstats" and len(existing.players) >= 10:
                skipped += 1
                continue

            _, created, _ = await import_csstats_match_by_id(db, client, stub.match_id)

            if created:
                imported += 1
            else:
                updated += 1

            if idx % COMMIT_EVERY == 0:
                await db.commit()
                logger.info(
                    "csstats import progress: %d/%d (%d new, %d updated, %d failed)",
                    idx,
                    len(stubs),
                    imported,
                    updated,
                    failed,
                )

        except CsstatsFetchError as exc:
            try:
                my_steam64 = await get_my_steam64_id(db)
                _, created, _ = await import_csstats_stub(db, stub, my_steam64)
                if created:
                    imported += 1
                else:
                    updated += 1
                logger.info("csstats stub import for %s after fetch block: %s", stub.match_id, exc)
            except Exception as stub_exc:
                failed += 1
                logger.warning("csstats import failed for match %s: %s", stub.match_id, stub_exc)

        except Exception as exc:
            failed += 1
            logger.warning("csstats import failed for match %s: %s", stub.match_id, exc)

    await db.commit()
    return {
        "total": len(stubs),
        "imported": imported,
        "updated": updated,
        "skipped": skipped,
        "failed": failed,
        "message": f"{imported} new, {updated} updated, {skipped} skipped, {failed} failed out of {len(stubs)} matches",
    }
