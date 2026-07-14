from datetime import datetime


def parse_sync_timestamp(value) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def touch_enrichment(raw: dict | None, **updates) -> dict:
    payload = dict(raw or {})
    enrichment = dict(payload.get("_enrichment") or {})
    enrichment.update(updates)
    payload["_enrichment"] = enrichment
    return payload


def get_match_sync_status(raw: dict | None, source: str) -> dict:
    enrichment = (raw or {}).get("_enrichment") or {}
    steam_at = parse_sync_timestamp(enrichment.get("steam_synced_at"))
    leetify_at = parse_sync_timestamp(enrichment.get("leetify_synced_at"))

    leetify_data = enrichment.get("leetify") or {}
    steam_synced = steam_at is not None or (
        source == "steam_gc" and bool(raw and (raw.get("matchid") or raw.get("roundstatsall")))
    )
    leetify_synced = leetify_at is not None or bool(leetify_data) or source == "leetify"

    if leetify_synced and leetify_at is None:
        leetify_at = parse_sync_timestamp(
            leetify_data.get("finished_at") or leetify_data.get("finishedAt")
        )

    return {
        "steam_synced": steam_synced,
        "steam_synced_at": steam_at,
        "leetify_synced": leetify_synced,
        "leetify_synced_at": leetify_at,
    }


def get_match_external_urls(match) -> dict[str, str | None]:
    """Build Leetify / FACEIT web URLs from stored match identity and enrichment."""
    enrichment = (match.raw_payload or {}).get("_enrichment") or {}
    leetify_data = enrichment.get("leetify") or {}

    leetify_game_id = enrichment.get("leetify_game_id") or leetify_data.get("id")
    if match.source == "leetify" and not leetify_game_id:
        leetify_game_id = match.source_match_id

    leetify_url = (
        f"https://leetify.com/app/match-details/{leetify_game_id}" if leetify_game_id else None
    )

    faceit_match_id: str | None = None
    if match.source == "faceit":
        faceit_match_id = match.source_match_id
    else:
        data_source = str(leetify_data.get("data_source") or "").lower()
        ds_id = leetify_data.get("data_source_match_id")
        if data_source == "faceit" and ds_id:
            faceit_match_id = str(ds_id)

    faceit_url = (
        f"https://www.faceit.com/en/cs2/room/{faceit_match_id}/scoreboard" if faceit_match_id else None
    )

    csstats_match_id = enrichment.get("csstats_match_id")
    if not csstats_match_id and match.source == "csstats":
        csstats_match_id = match.source_match_id
    if not csstats_match_id:
        csstats_match_id = (match.raw_payload or {}).get("csstats_match_id")

    csstats_url = f"https://csstats.gg/match/{csstats_match_id}" if csstats_match_id else None

    return {
        "leetify_url": leetify_url,
        "faceit_url": faceit_url,
        "csstats_url": csstats_url,
    }
