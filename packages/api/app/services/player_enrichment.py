import logging
import re
from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models import Player, PlayerNameHistory, PlayerPlatformAccount
from app.services.faceit_client import FaceitClient
from app.services.leetify_client import LeetifyClient
from app.services.match_service import get_setting, save_stat_snapshot, upsert_player
from app.services.steam_client import SteamClient

logger = logging.getLogger(__name__)


def _parse_stat_int(value) -> int | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return int(value)
    m = re.search(r"\d+", str(value))
    return int(m.group()) if m else None


def _parse_stat_float(value) -> float | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    m = re.search(r"([\d.]+)", str(value).replace(",", "."))
    return float(m.group(1)) if m else None


def _get_player_stat(player_stats: dict, *keys: str):
    if not player_stats:
        return None
    lowered = {str(k).lower(): v for k, v in player_stats.items()}
    for key in keys:
        val = lowered.get(key.lower())
        if val is not None and str(val).strip() != "":
            return val
    return None


def _normalize_faceit_lifetime(lifetime: dict) -> dict:
    return {
        "matches": _parse_stat_int(_get_player_stat(lifetime, "Total Matches", "Matches")),
        "win_rate_pct": _parse_stat_float(_get_player_stat(lifetime, "Win Rate %", "Win Rate")),
        "kd": _parse_stat_float(_get_player_stat(lifetime, "Average K/D Ratio", "K/D Ratio")),
        "kr": _parse_stat_float(_get_player_stat(lifetime, "Average K/R Ratio", "K/R Ratio")),
        "adr": _parse_stat_float(_get_player_stat(lifetime, "ADR", "Average Damage per Round")),
        "hs_pct": _parse_stat_float(
            _get_player_stat(lifetime, "Average Headshots %", "Headshots %", "Headshot %")
        ),
        "avg_kills": _parse_stat_float(_get_player_stat(lifetime, "Average Kills", "Kills")),
        "avg_deaths": _parse_stat_float(_get_player_stat(lifetime, "Average Deaths", "Deaths")),
        "avg_assists": _parse_stat_float(_get_player_stat(lifetime, "Average Assists", "Assists")),
        "entry_success_pct": _parse_stat_float(
            _get_player_stat(lifetime, "Entry Success Rate", "Entry Rate")
        ),
        "kast_pct": _parse_stat_float(_get_player_stat(lifetime, "KAST", "Average KAST")),
    }


def _aggregate_faceit_recent(items: list, limit: int = 20) -> dict:
    samples = [(item.get("stats") or {}) for item in items[:limit] if item.get("stats")]
    if not samples:
        return {"match_count": 0}

    def _avg(*keys: str) -> float | None:
        vals = []
        for sample in samples:
            parsed = _parse_stat_float(_get_player_stat(sample, *keys))
            if parsed is not None:
                vals.append(parsed)
        return round(sum(vals) / len(vals), 2) if vals else None

    return {
        "match_count": len(samples),
        "kd": _avg("K/D Ratio"),
        "kr": _avg("K/R Ratio"),
        "adr": _avg("ADR", "Average Damage per Round"),
        "hs_pct": _avg("Headshots %", "Average Headshots %", "Headshot %"),
        "avg_kills": _avg("Kills", "Average Kills"),
        "avg_deaths": _avg("Deaths", "Average Deaths"),
        "avg_assists": _avg("Assists", "Average Assists"),
        "entry_success_pct": _avg("Entry Success Rate", "Entry Rate"),
        "kast_pct": _avg("KAST"),
    }


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

    return flags


async def _fetch_faceit_enrichment(client: FaceitClient, faceit_player_id: str) -> dict:
    lifetime_raw: dict = {}
    recent_items: list = []
    bans: list = []

    stats = await client.get_player_stats(faceit_player_id)
    if stats:
        lifetime_raw = stats.get("lifetime") or {}

    recent = await client.get_player_recent_match_stats(faceit_player_id)
    if recent:
        recent_items = recent.get("items") or []

    bans_resp = await client.get_player_bans(faceit_player_id)
    if bans_resp:
        bans = bans_resp.get("items") or []

    lifetime = _normalize_faceit_lifetime(lifetime_raw)
    recent_block = _aggregate_faceit_recent(recent_items)
    return {
        "lifetime": lifetime,
        "recent_20": recent_block,
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
        "flags": _compute_faceit_flags(lifetime, recent_block, bans),
    }


async def _record_name_change(db: AsyncSession, player_id: UUID, name: str) -> None:
    result = await db.execute(
        select(PlayerNameHistory).where(
            PlayerNameHistory.player_id == player_id,
            PlayerNameHistory.name == name,
        )
    )
    if result.scalar_one_or_none():
        return
    now = datetime.now(timezone.utc)
    db.add(PlayerNameHistory(player_id=player_id, name=name, first_seen_at=now, last_seen_at=now))


async def _upsert_faceit_account(
    db: AsyncSession,
    player_id: UUID,
    faceit_id: str,
    nickname: str | None,
    profile_url: str | None,
) -> None:
    result = await db.execute(
        select(PlayerPlatformAccount).where(
            PlayerPlatformAccount.platform == "faceit",
            PlayerPlatformAccount.external_id == faceit_id,
        )
    )
    acct = result.scalar_one_or_none()
    if acct is None:
        db.add(
            PlayerPlatformAccount(
                player_id=player_id,
                platform="faceit",
                external_id=faceit_id,
                nickname=nickname,
                profile_url=profile_url,
            )
        )
    else:
        acct.nickname = nickname or acct.nickname
        acct.profile_url = profile_url or acct.profile_url


async def enrich_player_profile(db: AsyncSession, player_id: UUID) -> dict:
    player = await db.get(Player, player_id)
    if not player:
        raise ValueError("Player not found")

    sources: list[str] = []
    errors: list[str] = []
    now = datetime.now(timezone.utc)

    steam_key = await get_setting(db, "steam_api_key") or settings.steam_api_key
    faceit_key = await get_setting(db, "faceit_api_key") or settings.faceit_api_key
    leetify_key = await get_setting(db, "leetify_api_key") or settings.leetify_api_key

    if steam_key:
        try:
            summaries = await SteamClient(steam_key).get_player_summaries([player.steam64_id])
            if summaries:
                s = summaries[0]
                name = s.get("personaname")
                if name and name != player.current_name:
                    await _record_name_change(db, player_id, name)
                await upsert_player(
                    db,
                    player.steam64_id,
                    name=name,
                    avatar_url=s.get("avatarfull"),
                    profile_url=s.get("profileurl"),
                )
                player.last_seen_at = now
                sources.append("steam")
            else:
                errors.append("Steam: profile not found")
        except Exception as exc:
            errors.append(f"Steam: {exc}")
    else:
        errors.append("Steam API key not configured")

    if leetify_key:
        try:
            profile = await LeetifyClient(leetify_key).get_profile(player.steam64_id)
            if profile:
                await save_stat_snapshot(db, player_id, "leetify", profile)
                sources.append("leetify")
            else:
                errors.append("Leetify: no public profile for this player")
        except Exception as exc:
            errors.append(f"Leetify: {exc}")
    else:
        errors.append("Leetify API key not configured")

    if faceit_key:
        try:
            client = FaceitClient(faceit_key)
            data = await client.get_player_by_steam_id(player.steam64_id)
            if data:
                cs2 = data.get("games", {}).get("cs2", {})
                faceit_id = data.get("player_id", "")
                nickname = data.get("nickname")
                profile_url = f"https://www.faceit.com/en/players/{nickname}" if nickname else None
                await _upsert_faceit_account(db, player_id, faceit_id, nickname, profile_url)
                enrichment = await _fetch_faceit_enrichment(client, faceit_id)
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
                await save_stat_snapshot(db, player_id, "faceit", payload)
                sources.append("faceit")
            else:
                errors.append("FACEIT: no account linked to this Steam ID")
        except Exception as exc:
            errors.append(f"FACEIT: {exc}")
    else:
        errors.append("FACEIT API key not configured")

    await db.flush()
    logger.info("Player profile sync for %s: %s", player.steam64_id, sources)
    return {"player_id": str(player_id), "sources": sources, "errors": errors}
