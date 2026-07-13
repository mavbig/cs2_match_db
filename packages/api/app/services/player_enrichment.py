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
    text = _normalize_numeric_string(value)
    m = re.search(r"\d+", text)
    return int(m.group()) if m else None


def _parse_stat_float(value) -> float | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = _normalize_numeric_string(value)
    m = re.search(r"([\d.]+)", text)
    return float(m.group(1)) if m else None


def _normalize_numeric_string(value) -> str:
    text = str(value).strip().replace(" ", "")
    if re.fullmatch(r"\d{1,3}(?:,\d{3})+(?:\.\d+)?", text):
        return text.replace(",", "")
    return text.replace(",", ".")


def _get_player_stat(player_stats: dict, *keys: str):
    if not player_stats:
        return None
    lowered = {str(k).lower(): v for k, v in player_stats.items()}
    for key in keys:
        val = lowered.get(key.lower())
        if val is not None and str(val).strip() != "":
            return val
    return None


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


def _merge_faceit_lifetime_map(stats_response: dict | None) -> dict:
    if not stats_response:
        return {}

    merged: dict = dict(stats_response.get("lifetime") or {})

    def merge_missing(src: dict) -> None:
        for key, value in src.items():
            if value is None or str(value).strip() == "":
                continue
            label = str(key).strip()
            if merged.get(label) in (None, ""):
                merged[label] = value

    for segment in stats_response.get("segments") or []:
        if not isinstance(segment, dict):
            continue
        label = str(segment.get("label") or "").lower()
        seg_type = str(segment.get("type") or "").lower()
        if label in ("overall", "total", "lifetime") or seg_type in ("overall", "total"):
            merge_missing(segment.get("stats") or {})

    if not _get_player_stat(merged, "Total Kills", "Kills", "Average Kills"):
        totals = _aggregate_map_segment_totals(stats_response.get("segments") or [])
        merge_missing(totals)

    segments = stats_response.get("segments") or []
    weighted_fields = (
        ("Average K/R Ratio", ("Average K/R Ratio",)),
        ("Average Kills", ("Average Kills", "Kills / Match", "Average Kills per Match")),
        ("Average Deaths", ("Average Deaths", "Deaths / Match", "Average Deaths per Match")),
        ("Average Assists", ("Average Assists", "Assists / Match", "Average Assists per Match")),
    )
    for target_key, source_keys in weighted_fields:
        if _get_player_stat(merged, target_key, *source_keys) is None:
            average = _weighted_segment_average(segments, *source_keys)
            if average is not None:
                merged[target_key] = average

    return merged


def _weighted_segment_average(segments: list, *keys: str) -> float | None:
    total_matches = 0.0
    weighted_sum = 0.0
    for segment in segments:
        if not isinstance(segment, dict):
            continue
        if str(segment.get("type") or "").lower() != "map":
            continue
        stats = segment.get("stats") or {}
        matches = _parse_stat_float(
            _get_player_stat(stats, "Matches", "Games", "Total Matches")
        )
        value = _parse_stat_float(_get_player_stat(stats, *keys))
        if matches and value is not None:
            weighted_sum += value * matches
            total_matches += matches
    if total_matches <= 0:
        return None
    return round(weighted_sum / total_matches, 2)


def _aggregate_map_segment_totals(segments: list) -> dict:
    total_kills = 0.0
    total_deaths = 0.0
    total_assists = 0.0
    total_rounds = 0.0
    found = False

    for segment in segments:
        if not isinstance(segment, dict):
            continue
        if str(segment.get("type") or "").lower() != "map":
            continue
        stats = segment.get("stats") or {}
        matches = _parse_stat_float(_get_player_stat(stats, "Matches", "Games"))
        if not matches:
            continue

        kills = _parse_stat_float(_get_player_stat(stats, "Kills", "Total Kills"))
        deaths = _parse_stat_float(_get_player_stat(stats, "Deaths", "Total Deaths"))
        assists = _parse_stat_float(_get_player_stat(stats, "Assists", "Total Assists"))
        rounds = _parse_stat_float(_get_player_stat(stats, "Rounds", "Total Rounds"))
        avg_kills = _parse_stat_float(
            _get_player_stat(stats, "Average Kills", "Kills / Match", "Average Kills per Match")
        )
        avg_deaths = _parse_stat_float(
            _get_player_stat(stats, "Average Deaths", "Deaths / Match", "Average Deaths per Match")
        )
        avg_assists = _parse_stat_float(
            _get_player_stat(stats, "Average Assists", "Assists / Match", "Average Assists per Match")
        )

        if kills is not None:
            total_kills += kills
        elif avg_kills is not None:
            total_kills += avg_kills * matches

        if deaths is not None:
            total_deaths += deaths
        elif avg_deaths is not None:
            total_deaths += avg_deaths * matches

        if assists is not None:
            total_assists += assists
        elif avg_assists is not None:
            total_assists += avg_assists * matches

        if rounds is not None:
            total_rounds += rounds

        found = True

    if not found:
        return {}

    totals: dict[str, float] = {}
    if total_kills > 0:
        totals["Kills"] = round(total_kills, 2)
    if total_deaths > 0:
        totals["Deaths"] = round(total_deaths, 2)
    if total_assists > 0:
        totals["Assists"] = round(total_assists, 2)
    if total_rounds > 0:
        totals["Rounds"] = round(total_rounds, 2)
    return totals


def _build_faceit_profile_debug(
    *,
    faceit_player_id: str,
    stats_response: dict | None,
    lifetime_raw: dict,
    lifetime_normalized: dict,
    recent_items: list,
) -> dict:
    recent_stats = (recent_items[0].get("stats") or {}) if recent_items else {}
    segments = []
    for segment in (stats_response or {}).get("segments") or []:
        if not isinstance(segment, dict):
            continue
        stats = segment.get("stats") or {}
        segments.append(
            {
                "type": segment.get("type"),
                "label": segment.get("label"),
                "stat_keys": sorted(stats.keys()),
                "stats": stats,
            }
        )

    return {
        "faceit_player_id": faceit_player_id,
        "stats_response_keys": sorted((stats_response or {}).keys()),
        "api_lifetime": stats_response.get("lifetime") if stats_response else None,
        "merged_lifetime": lifetime_raw,
        "normalized_lifetime": lifetime_normalized,
        "segments": segments,
        "recent_match_stat_keys": sorted(recent_stats.keys()),
        "recent_match_sample": recent_stats,
        "recent_match_count": len(recent_items),
    }


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

    if block.get("kr") is None and block.get("avg_kills") is not None and rounds and match_count > 0:
        try:
            round_count = float(rounds)
            if round_count > 0:
                block["kr"] = round((float(block["avg_kills"]) * match_count) / round_count, 2)
        except (TypeError, ValueError, ZeroDivisionError):
            pass

    kd = block.get("kd")
    if kd is not None:
        try:
            kd_value = float(kd)
        except (TypeError, ValueError):
            kd_value = 0
        if kd_value > 0:
            if block.get("avg_kills") is None and block.get("avg_deaths") is not None:
                block["avg_kills"] = round(kd_value * float(block["avg_deaths"]), 2)
            if block.get("avg_deaths") is None and block.get("avg_kills") is not None:
                block["avg_deaths"] = round(float(block["avg_kills"]) / kd_value, 2)

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
            _get_player_stat(
                lifetime,
                "Average K/D Ratio",
                "Average K/D",
                "K/D Ratio",
                "KDR",
                "Average KDR",
            )
        ),
        "kr": _extract_faceit_kr(lifetime),
        "adr": _parse_stat_float(
            _get_player_stat(
                lifetime,
                "ADR",
                "Average Damage per Round",
                "Damage / Round",
                "Average ADR",
            )
        ),
        "hs_pct": _parse_stat_float(
            _get_player_stat(
                lifetime,
                "Average Headshots %",
                "Headshots %",
                "Average Headshots",
                "Headshot %",
            )
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
        "total_kills": _parse_stat_float(
            _get_player_stat(lifetime, "Total Kills", "Kills", "Kill Count")
            or _get_player_stat_substring(lifetime, "total kills")
        ),
        "total_deaths": _parse_stat_float(
            _get_player_stat(lifetime, "Total Deaths", "Deaths")
            or _get_player_stat_substring(lifetime, "total deaths")
        ),
        "total_assists": _parse_stat_float(
            _get_player_stat(lifetime, "Total Assists", "Assists")
            or _get_player_stat_substring(lifetime, "total assists")
        ),
        "rounds": _parse_stat_float(
            _get_player_stat(
                lifetime,
                "Rounds",
                "Total Rounds",
                "Rounds Played",
                "Total Rounds Played",
            )
            or _get_player_stat_substring(lifetime, "rounds played")
            or _get_player_stat_substring(lifetime, "total rounds")
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


def _parse_faceit_timestamp(raw) -> datetime | None:
    if raw is None:
        return None
    try:
        if isinstance(raw, str):
            text = raw.strip()
            if not text:
                return None
            if text.isdigit():
                value = int(text)
            else:
                return datetime.fromisoformat(text.replace("Z", "+00:00")).astimezone(timezone.utc)
        elif isinstance(raw, (int, float)):
            value = int(raw)
        else:
            return None
        # FACEIT per-match stats use epoch milliseconds; history uses epoch seconds.
        if value > 1_000_000_000_000:
            value //= 1000
        return datetime.fromtimestamp(value, tz=timezone.utc)
    except (TypeError, ValueError, OSError):
        return None


def _faceit_item_finished_at(item: dict) -> datetime | None:
    stats = item.get("stats") or {}
    for candidate in (
        item.get("finished_at"),
        item.get("date"),
        _get_player_stat(stats, "Match Finished At", "Match Finished"),
    ):
        finished_at = _parse_faceit_timestamp(candidate)
        if finished_at:
            return finished_at
    return None


def _faceit_match_id(item: dict) -> str:
    direct = item.get("match_id") or item.get("matchId")
    if direct:
        return str(direct)
    stats = item.get("stats") or {}
    from_stats = _get_player_stat(stats, "Match Id", "Match ID", "MatchId")
    return str(from_stats) if from_stats else ""


def _parse_match_result(stats: dict) -> str | None:
    raw = _get_player_stat(stats, "Result", "Game Result", "Win")
    if raw is None:
        return None
    if isinstance(raw, bool):
        return "win" if raw else "loss"
    if isinstance(raw, (int, float)):
        value = int(raw)
        if value == 1:
            return "win"
        if value == 0:
            return "loss"
        return None
    text = str(raw).strip().lower()
    if text in ("1", "win", "won", "true", "w"):
        return "win"
    if text in ("0", "loss", "lost", "lose", "false", "l"):
        return "loss"
    return None


def _history_match_result(item: dict, faceit_player_id: str) -> str | None:
    results = item.get("results") or {}
    winner = results.get("winner")
    if not winner:
        return None
    teams = item.get("teams") or {}
    for faction_id, team in teams.items():
        if not isinstance(team, dict):
            continue
        for player in team.get("roster") or []:
            if str(player.get("player_id")) != str(faceit_player_id):
                continue
            return "win" if str(faction_id) == str(winner) else "loss"
    return None


def _activity_history_cap(lifetime_matches: int | None) -> int:
    if lifetime_matches is None:
        return 200
    if lifetime_matches <= 0:
        return 50
    if lifetime_matches <= 50:
        return lifetime_matches
    return min(lifetime_matches, 200)


def _build_activity_periods(
    matches: list[dict],
    now: datetime,
    *,
    lifetime_matches: int | None,
) -> tuple[list[dict], str]:
    first_at = datetime.fromisoformat(matches[0]["finished_at"].replace("Z", "+00:00"))
    last_at = datetime.fromisoformat(matches[-1]["finished_at"].replace("Z", "+00:00"))
    span_days = max(0, (last_at - first_at).days)

    use_weeks = len(matches) < 100 and span_days <= 150
    if use_weeks:
        period_counts: Counter[str] = Counter()
        for entry in matches:
            dt = datetime.fromisoformat(entry["finished_at"].replace("Z", "+00:00"))
            week_start = (dt - timedelta(days=dt.weekday())).date()
            period_counts[week_start.isoformat()] += 1

        periods: list[dict] = []
        cursor = now.date() - timedelta(days=now.weekday())
        for _ in range(12):
            key = cursor.isoformat()
            periods.append(
                {
                    "month": key,
                    "label": cursor.strftime("%d %b"),
                    "count": period_counts.get(key, 0),
                }
            )
            cursor -= timedelta(days=7)
        periods.reverse()
        granularity = "week"
    else:
        month_counts: Counter[str] = Counter()
        for entry in matches:
            dt = datetime.fromisoformat(entry["finished_at"].replace("Z", "+00:00"))
            month_counts[dt.strftime("%Y-%m")] += 1

        period_count = 24 if (lifetime_matches or len(matches)) < 200 or span_days > 300 else 12
        periods = []
        cursor = datetime(now.year, now.month, 1, tzinfo=timezone.utc)
        for _ in range(period_count):
            key = cursor.strftime("%Y-%m")
            periods.append(
                {
                    "month": key,
                    "label": cursor.strftime("%b '%y"),
                    "count": month_counts.get(key, 0),
                }
            )
            cursor = (cursor.replace(day=1) - timedelta(days=1)).replace(day=1)
        periods.reverse()
        granularity = "month"

    max_count = max((p["count"] for p in periods), default=1) or 1
    for bucket in periods:
        bucket["height_pct"] = round(100 * bucket["count"] / max_count)

    return periods, granularity


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
    faceit_player_id: str,
    lifetime_matches: int | None,
) -> dict:
    result_by_match: dict[str, str] = {}
    for item in recent_items:
        match_id = _faceit_match_id(item)
        if not match_id:
            continue
        result = _parse_match_result(item.get("stats") or {})
        if result:
            result_by_match[match_id] = result

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
        match_id = _faceit_match_id(item)
        finished_at = _faceit_item_finished_at(item)
        if not match_id or not finished_at:
            continue
        result = result_by_match.get(match_id) or _history_match_result(item, faceit_player_id)
        add_match(match_id, finished_at, result)

    for item in recent_items:
        match_id = _faceit_match_id(item)
        finished_at = _faceit_item_finished_at(item)
        if not match_id or not finished_at:
            continue
        result = result_by_match.get(match_id) or _parse_match_result(item.get("stats") or {})
        add_match(match_id, finished_at, result)

    if not matches:
        return {
            "matches": [],
            "last_played_at": None,
            "days_since_last": None,
            "months": [],
            "chart_granularity": "month",
            "stale_warning": None,
            "sample_size": 0,
        }

    matches.sort(key=lambda entry: entry["finished_at"])
    now = datetime.now(timezone.utc)
    last_at = datetime.fromisoformat(matches[-1]["finished_at"].replace("Z", "+00:00"))
    days_since = max(0, (now - last_at).days)

    months, chart_granularity = _build_activity_periods(
        matches,
        now,
        lifetime_matches=lifetime_matches,
    )

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
        "matches": matches[-50:],
        "last_played_at": last_at.isoformat(),
        "days_since_last": days_since,
        "months": months,
        "chart_granularity": chart_granularity,
        "stale_warning": stale_warning,
        "sample_size": len(matches),
    }


async def _fetch_faceit_enrichment(client: FaceitClient, faceit_player_id: str) -> dict:
    recent_items: list = []
    bans: list = []
    history_items: list = []

    stats_response = await client.get_player_stats(faceit_player_id)
    lifetime_raw = _merge_faceit_lifetime_map(stats_response)
    lifetime = _normalize_faceit_lifetime(lifetime_raw)
    history_cap = _activity_history_cap(lifetime.get("matches"))
    stats_cap = min(history_cap, 100)

    try:
        history_items = await client.get_all_match_history(faceit_player_id, max_items=history_cap)
    except Exception as exc:
        logger.warning("FACEIT match history failed for %s: %s", faceit_player_id, exc)
        history_items = []

    try:
        recent_items = await client.get_all_player_recent_match_stats(
            faceit_player_id,
            max_items=stats_cap,
        )
    except Exception as exc:
        logger.warning("FACEIT recent match stats failed for %s: %s", faceit_player_id, exc)
        recent_items = []

    bans_resp = await client.get_player_bans(faceit_player_id)
    if bans_resp:
        bans = bans_resp.get("items") or []

    recent_block = _aggregate_faceit_recent(recent_items)
    activity = _build_faceit_activity(
        history_items,
        recent_items,
        faceit_player_id=faceit_player_id,
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
        "_profile_debug": _build_faceit_profile_debug(
            faceit_player_id=faceit_player_id,
            stats_response=stats_response,
            lifetime_raw=lifetime_raw,
            lifetime_normalized=lifetime,
            recent_items=recent_items,
        ),
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
