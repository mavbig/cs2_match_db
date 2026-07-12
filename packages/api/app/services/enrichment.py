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
    leetify_synced = leetify_at is not None or bool(leetify_data)

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
