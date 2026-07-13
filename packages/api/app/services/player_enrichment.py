import logging
import re
from collections import Counter
from datetime import datetime, timedelta, timezone
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


def _parse_match_result(stats: dict) -> str | None:
    raw = _get_player_stat(stats, "Result", "Win")
    if raw is None:
        return None
    text = str(raw).strip().lower()
    if text in ("1", "win", "won", "true", "w"):
        return "win"
    if text in ("0", "loss", "lost", "false", "l"):
        return "loss"
    return None


def _compute_faceit_flags(
    lifetime: dict,
    recent: dict,
    bans: list,
    activity: dict | None = None,
) -> list[dict]:
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

    if activity and activity.get("stale_warning"):
        flags.append(
            {
                "severity": "medium",
                "label": "Inactive FACEIT account",
                "detail": activity["stale_warning"],
            }
        )

    return flags


def _build_faceit_activity(
    history_items: list[dict],
    recent_items: list[dict],
    *,
    lifetime_matches: int | None,
) -> dict:
    result_by_match: dict[str, str] = {}
    for item in recent_items:
        match_id = item.get("match_id")
        if not match_id:
            continue
        result = _parse_match_result(item.get("stats") or {})
        if result:
            result_by_match[str(match_id)] = result

    seen: set[str] = set()
    matches: list[dict] = []

    def add_match(match_id: str, finished_at: datetime, result: str | None) -> None:
        if not match_id or match_id in seen:
            return
        seen.add(match_id)
        matches.append(
            {
                "match_id": match_id,
                "finished_at": finished_at.isoformat(),
                "result": result,
            }
        )

    for item in history_items:
        finished = item.get("finished_at")
        match_id = str(item.get("match_id") or "")
        if not finished or not match_id:
            continue
        add_match(
            match_id,
            datetime.fromtimestamp(int(finished), tz=timezone.utc),
            result_by_match.get(match_id),
        )

    for item in recent_items:
        match_id = str(item.get("match_id") or "")
        if not match_id:
            continue
        raw_date = item.get("date") or item.get("finished_at")
        if raw_date is None:
            continue
        try:
            if isinstance(raw_date, (int, float)):
                finished_at = datetime.fromtimestamp(int(raw_date), tz=timezone.utc)
            else:
                finished_at = datetime.fromisoformat(str(raw_date).replace("Z", "+00:00")).astimezone(timezone.utc)
        except (TypeError, ValueError):
            continue
        add_match(match_id, finished_at, result_by_match.get(match_id))

    if not matches:
        return {
            "matches": [],
            "last_played_at": None,
            "days_since_last": None,
            "months": [],
            "stale_warning": None,
            "sample_size": 0,
        }

    matches.sort(key=lambda entry: entry["finished_at"])
    now = datetime.now(timezone.utc)
    last_at = datetime.fromisoformat(matches[-1]["finished_at"].replace("Z", "+00:00"))
    days_since = max(0, (now - last_at).days)

    month_counts: Counter[str] = Counter()
    for entry in matches:
        dt = datetime.fromisoformat(entry["finished_at"].replace("Z", "+00:00"))
        month_counts[dt.strftime("%Y-%m")] += 1

    months: list[dict] = []
    cursor = datetime(now.year, now.month, 1, tzinfo=timezone.utc)
    for _ in range(12):
        key = cursor.strftime("%Y-%m")
        months.append(
            {
                "month": key,
                "label": cursor.strftime("%b '%y"),
                "count": month_counts.get(key, 0),
            }
        )
        cursor = (cursor.replace(day=1) - timedelta(days=1)).replace(day=1)
    months.reverse()

    max_count = max((m["count"] for m in months), default=1) or 1
    for bucket in months:
        bucket["height_pct"] = round(100 * bucket["count"] / max_count)

    stale_warning = None
    if days_since >= 180:
        stale_warning = (
            f"Last FACEIT game was {days_since} days ago — lifetime stats may not reflect current form."
        )
    elif days_since >= 90 and lifetime_matches and lifetime_matches >= 80:
        stale_warning = (
            f"No games in {days_since} days despite {lifetime_matches} lifetime matches."
        )

    return {
        "matches": matches[-30:],
        "last_played_at": last_at.isoformat(),
        "days_since_last": days_since,
        "months": months,
        "stale_warning": stale_warning,
        "sample_size": len(matches),
    }


async def _fetch_faceit_enrichment(client: FaceitClient, faceit_player_id: str) -> dict:
    lifetime_raw: dict = {}
    recent_items: list = []
    bans: list = []
    history_items: list = []

    stats = await client.get_player_stats(faceit_player_id)
    if stats:
        lifetime_raw = stats.get("lifetime") or {}

    recent = await client.get_player_recent_match_stats(faceit_player_id)
    if recent:
        recent_items = recent.get("items") or []

    try:
        history = await client.get_match_history(faceit_player_id, limit=50)
        history_items = history.get("items") or []
    except Exception as exc:
        logger.warning("FACEIT match history failed for %s: %s", faceit_player_id, exc)

    bans_resp = await client.get_player_bans(faceit_player_id)
    if bans_resp:
        bans = bans_resp.get("items") or []

    lifetime = _normalize_faceit_lifetime(lifetime_raw)
    recent_block = _aggregate_faceit_recent(recent_items)
    activity = _build_faceit_activity(
        history_items,
        recent_items,
        lifetime_matches=lifetime.get("matches"),
    )
    return {
        "lifetime": lifetime,
        "recent_20": recent_block,
        "activity": activity,
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
        "flags": _compute_faceit_flags(lifetime, recent_block, bans, activity),
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
