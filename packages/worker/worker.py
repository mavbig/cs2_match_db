import json
import logging
import re
from datetime import datetime, timezone
from uuid import UUID

import httpx
from arq import cron
from arq.connections import RedisSettings
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from config import settings

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("worker")

engine = create_async_engine(settings.database_url)
Session = async_sessionmaker(engine, expire_on_commit=False)

FACEIT_BASE = "https://open.faceit.com/data/v4"


async def _get_setting(session: AsyncSession, key: str) -> str | None:
    from sqlalchemy import text

    result = await session.execute(text("SELECT value FROM app_settings WHERE key = :k"), {"k": key})
    row = result.fetchone()
    return row[0] if row else None


def _parse_stat_int(value) -> int | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return int(value)
    m = re.search(r"\d+", str(value))
    return int(m.group()) if m else None


def _parse_headshot_pct(value) -> float | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    m = re.search(r"([\d.]+)", str(value))
    return float(m.group(1)) if m else None


def _extract_map(detail: dict, stats: dict) -> str | None:
    voting = detail.get("voting") or {}
    map_info = voting.get("map") or {}
    pick = map_info.get("pick")
    if isinstance(pick, list) and pick:
        return str(pick[0])
    if isinstance(pick, str):
        return pick

    rounds = stats.get("rounds") or []
    if rounds:
        round_stats = rounds[0].get("round_stats") or {}
        if round_stats.get("Map"):
            return str(round_stats["Map"])

    return detail.get("map") or item_map_from_metadata(detail)


def item_map_from_metadata(detail: dict) -> str | None:
    metadata = detail.get("metadata") or []
    for entry in metadata:
        if entry.get("key") == "map":
            return entry.get("value")
    return None


async def _faceit_headers(session: AsyncSession) -> dict:
    faceit_key = await _get_setting(session, "faceit_api_key") or settings.faceit_api_key
    return {"Authorization": f"Bearer {faceit_key}"}


async def _resolve_steam64(
    client: httpx.AsyncClient,
    headers: dict,
    faceit_player_id: str,
    cache: dict[str, str | None],
) -> str | None:
    if faceit_player_id in cache:
        return cache[faceit_player_id]

    resp = await client.get(f"{FACEIT_BASE}/players/{faceit_player_id}", headers=headers)
    if resp.status_code != 200:
        cache[faceit_player_id] = None
        return None

    data = resp.json()
    steam64 = (data.get("games") or {}).get("cs2", {}).get("game_player_id")
    cache[faceit_player_id] = str(steam64) if steam64 else None
    return cache[faceit_player_id]


async def _upsert_player(
    session: AsyncSession,
    steam64_id: str,
    name: str | None,
    now: datetime,
) -> str:
    from sqlalchemy import text

    result = await session.execute(
        text(
            "INSERT INTO players (id, steam64_id, current_name, first_seen_at, last_seen_at) "
            "VALUES (gen_random_uuid(), :sid, :name, :now, :now) "
            "ON CONFLICT (steam64_id) DO UPDATE SET "
            "last_seen_at = :now, current_name = COALESCE(EXCLUDED.current_name, players.current_name) "
            "RETURNING id"
        ),
        {"sid": steam64_id, "name": name, "now": now},
    )
    return str(result.fetchone()[0])


class SteamClient:
    async def get_player_summaries(self, api_key: str, steam64_ids: list[str]) -> list[dict]:
        if not api_key:
            return []
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(
                "https://api.steampowered.com/ISteamUser/GetPlayerSummaries/v0002/",
                params={"key": api_key, "steamids": ",".join(steam64_ids)},
            )
            resp.raise_for_status()
            return resp.json().get("response", {}).get("players", [])


class LeetifyClient:
    def __init__(self, api_key: str):
        self.api_key = api_key

    async def get_profile(self, steam64_id: str) -> dict | None:
        headers = {"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(
                "https://api-public.cs-prod.leetify.com/v3/profile",
                params={"steamId": steam64_id},
                headers=headers,
            )
            if resp.status_code in (404, 401):
                return None
            resp.raise_for_status()
            return resp.json()


async def enrich_player(ctx, player_id: str):
    from sqlalchemy import text

    async with Session() as session:
        pid = str(UUID(player_id))
        result = await session.execute(
            text("SELECT steam64_id, current_name FROM players WHERE id = :id"),
            {"id": pid},
        )
        row = result.fetchone()
        if not row:
            return

        steam64_id, current_name = row[0], row[1]
        steam_key = await _get_setting(session, "steam_api_key") or settings.steam_api_key
        faceit_key = await _get_setting(session, "faceit_api_key") or settings.faceit_api_key
        leetify_key = await _get_setting(session, "leetify_api_key") or settings.leetify_api_key
        now = datetime.now(timezone.utc)

        if steam_key:
            summaries = await SteamClient().get_player_summaries(steam_key, [steam64_id])
            if summaries:
                s = summaries[0]
                name = s.get("personaname")
                await session.execute(
                    text(
                        "UPDATE players SET current_name = :name, avatar_url = :avatar, "
                        "profile_url = :url, last_seen_at = :now WHERE id = :id"
                    ),
                    {
                        "name": name,
                        "avatar": s.get("avatarfull"),
                        "url": s.get("profileurl"),
                        "now": now,
                        "id": pid,
                    },
                )
                if name and name != current_name:
                    hist = await session.execute(
                        text(
                            "SELECT id FROM player_name_history WHERE player_id = :pid AND name = :name"
                        ),
                        {"pid": pid, "name": name},
                    )
                    if not hist.fetchone():
                        await session.execute(
                            text(
                                "INSERT INTO player_name_history (id, player_id, name, first_seen_at, last_seen_at) "
                                "VALUES (gen_random_uuid(), :pid, :name, :now, :now)"
                            ),
                            {"pid": pid, "name": name, "now": now},
                        )

        profile = await LeetifyClient(leetify_key).get_profile(steam64_id)
        if profile:
            await session.execute(
                text(
                    "INSERT INTO player_stat_snapshots (id, player_id, source, captured_at, payload) "
                    "VALUES (gen_random_uuid(), :pid, 'leetify', :now, CAST(:payload AS jsonb))"
                ),
                {"pid": pid, "now": now, "payload": json.dumps(profile)},
            )

        if faceit_key:
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.get(
                    f"{FACEIT_BASE}/players",
                    params={"game": "cs2", "game_player_id": steam64_id},
                    headers={"Authorization": f"Bearer {faceit_key}"},
                )
                if resp.status_code == 200:
                    data = resp.json()
                    cs2 = data.get("games", {}).get("cs2", {})
                    existing = await session.execute(
                        text(
                            "SELECT id FROM player_platform_accounts WHERE platform = 'faceit' AND external_id = :eid"
                        ),
                        {"eid": data.get("player_id", "")},
                    )
                    if not existing.fetchone():
                        await session.execute(
                            text(
                                "INSERT INTO player_platform_accounts (id, player_id, platform, external_id, nickname, profile_url) "
                                "VALUES (gen_random_uuid(), :pid, 'faceit', :eid, :nick, :url)"
                            ),
                            {
                                "pid": pid,
                                "eid": data.get("player_id", ""),
                                "nick": data.get("nickname"),
                                "url": f"https://www.faceit.com/en/players/{data.get('nickname')}",
                            },
                        )
                    await session.execute(
                        text(
                            "INSERT INTO player_stat_snapshots (id, player_id, source, captured_at, payload) "
                            "VALUES (gen_random_uuid(), :pid, 'faceit', :now, CAST(:payload AS jsonb))"
                        ),
                        {
                            "pid": pid,
                            "now": now,
                            "payload": json.dumps(
                                {
                                    "elo": cs2.get("faceit_elo"),
                                    "skill_level": cs2.get("skill_level"),
                                    "nickname": data.get("nickname"),
                                }
                            ),
                        },
                    )

        await session.commit()
        logger.info("Enriched player %s", steam64_id)


async def _import_faceit_match(
    session: AsyncSession,
    client: httpx.AsyncClient,
    headers: dict,
    match_id: str,
    history_item: dict,
    my_steam64: str | None,
    player_cache: dict[str, str | None],
) -> bool:
    from sqlalchemy import text

    detail_resp = await client.get(f"{FACEIT_BASE}/matches/{match_id}", headers=headers)
    if detail_resp.status_code != 200:
        logger.warning("FACEIT match detail failed for %s: %s", match_id, detail_resp.status_code)
        return False

    stats_resp = await client.get(f"{FACEIT_BASE}/matches/{match_id}/stats", headers=headers)
    stats = stats_resp.json() if stats_resp.status_code == 200 else {}

    detail = detail_resp.json()
    map_name = _extract_map(detail, stats)

    results = detail.get("results") or {}
    score = results.get("score") or {}
    score_a = _parse_stat_int(score.get("faction1"))
    score_b = _parse_stat_int(score.get("faction2"))

    rounds = stats.get("rounds") or []
    if (score_a is None or score_b is None) and rounds:
        round_stats = rounds[0].get("round_stats") or {}
        score_text = round_stats.get("Score")
        if score_text and "/" in str(score_text):
            left, _, right = str(score_text).partition("/")
            score_a = score_a if score_a is not None else _parse_stat_int(left)
            score_b = score_b if score_b is not None else _parse_stat_int(right)
        teams = rounds[0].get("teams") or []
        if len(teams) >= 2:
            if score_a is None:
                score_a = _parse_stat_int((teams[0].get("team_stats") or {}).get("Final Score"))
            if score_b is None:
                score_b = _parse_stat_int((teams[1].get("team_stats") or {}).get("Final Score"))

    finished_at = history_item.get("finished_at") or detail.get("finished_at")
    played_at = (
        datetime.fromtimestamp(finished_at, tz=timezone.utc)
        if finished_at
        else datetime.now(timezone.utc)
    )

    raw_payload = {"history": history_item, "detail": detail, "stats": stats}

    match_row = await session.execute(
        text(
            "INSERT INTO matches (id, source, source_match_id, map, mode, played_at, score_team_a, score_team_b, raw_payload) "
            "VALUES (gen_random_uuid(), 'faceit', :mid, :map, 'faceit', :played, :sa, :sb, CAST(:raw AS jsonb)) "
            "ON CONFLICT (source, source_match_id) DO UPDATE SET "
            "map = EXCLUDED.map, played_at = EXCLUDED.played_at, "
            "score_team_a = EXCLUDED.score_team_a, score_team_b = EXCLUDED.score_team_b, "
            "raw_payload = EXCLUDED.raw_payload "
            "RETURNING id"
        ),
        {
            "mid": match_id,
            "map": map_name,
            "played": played_at,
            "sa": score_a,
            "sb": score_b,
            "raw": json.dumps(raw_payload),
        },
    )
    db_match_id = str(match_row.fetchone()[0])
    now = datetime.now(timezone.utc)

    if not rounds:
        return True

    team_index = 0
    for team in rounds[0].get("teams") or []:
        team_index += 1
        team_key = "team_a" if team_index == 1 else "team_b"

        for fp in team.get("players") or []:
            faceit_pid = fp.get("player_id")
            nickname = fp.get("nickname")
            if not faceit_pid:
                continue

            steam64 = await _resolve_steam64(client, headers, faceit_pid, player_cache)
            if not steam64:
                logger.debug("Skipping FACEIT player %s (%s) — no Steam ID linked", nickname, faceit_pid)
                continue

            player_db_id = await _upsert_player(session, steam64, nickname, now)
            ps = fp.get("player_stats") or {}

            await session.execute(
                text(
                    "INSERT INTO match_players (id, match_id, player_id, team, kills, deaths, assists, mvps, headshot_pct, score, is_me) "
                    "VALUES (gen_random_uuid(), :mid, :pid, :team, :k, :d, :a, :mvp, :hsp, :score, :is_me) "
                    "ON CONFLICT (match_id, player_id) DO UPDATE SET "
                    "team = EXCLUDED.team, kills = EXCLUDED.kills, deaths = EXCLUDED.deaths, "
                    "assists = EXCLUDED.assists, mvps = EXCLUDED.mvps, headshot_pct = EXCLUDED.headshot_pct, "
                    "score = EXCLUDED.score, is_me = EXCLUDED.is_me"
                ),
                {
                    "mid": db_match_id,
                    "pid": player_db_id,
                    "team": team_key,
                    "k": _parse_stat_int(ps.get("Kills")),
                    "d": _parse_stat_int(ps.get("Deaths")),
                    "a": _parse_stat_int(ps.get("Assists")),
                    "mvp": _parse_stat_int(ps.get("MVPs")),
                    "hsp": _parse_headshot_pct(ps.get("Headshots %") or ps.get("Headshots")),
                    "score": _parse_stat_int(ps.get("K/R Ratio") or ps.get("Score")),
                    "is_me": bool(my_steam64 and steam64 == my_steam64),
                },
            )

    return True


async def sync_faceit_matches(ctx):
    from sqlalchemy import text

    async with Session() as session:
        faceit_key = await _get_setting(session, "faceit_api_key") or settings.faceit_api_key
        faceit_nick = await _get_setting(session, "faceit_nickname") or settings.faceit_nickname
        my_steam64 = await _get_setting(session, "my_steam64_id") or settings.my_steam64_id

        if not faceit_key or not faceit_nick:
            logger.warning("FACEIT not configured, skipping sync")
            return

        job_result = await session.execute(
            text(
                "INSERT INTO sync_jobs (id, job_type, status, started_at, matches_imported) "
                "VALUES (gen_random_uuid(), 'faceit', 'running', :now, 0) RETURNING id"
            ),
            {"now": datetime.now(timezone.utc)},
        )
        job_id = str(job_result.fetchone()[0])
        imported = 0
        headers = await _faceit_headers(session)
        player_cache: dict[str, str | None] = {}

        try:
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.get(
                    f"{FACEIT_BASE}/players",
                    params={"nickname": faceit_nick},
                    headers=headers,
                )
                resp.raise_for_status()
                player = resp.json()

                offset = 0
                while offset < 100:
                    hist_resp = await client.get(
                        f"{FACEIT_BASE}/players/{player['player_id']}/history",
                        params={"game": "cs2", "offset": offset, "limit": 20},
                        headers=headers,
                    )
                    hist_resp.raise_for_status()
                    history = hist_resp.json()

                    items = history.get("items", [])
                    if not items:
                        break

                    for item in items:
                        match_id = item.get("match_id")
                        if not match_id:
                            continue

                        ok = await _import_faceit_match(
                            session, client, headers, match_id, item, my_steam64, player_cache
                        )
                        if ok:
                            imported += 1

                    offset += 20
                    if len(items) < 20:
                        break

            await session.execute(
                text(
                    "UPDATE sync_jobs SET status = 'completed', finished_at = :now, matches_imported = :n WHERE id = :id"
                ),
                {"now": datetime.now(timezone.utc), "n": imported, "id": job_id},
            )
            await session.commit()
            logger.info("FACEIT sync complete: %d matches", imported)
        except Exception as e:
            await session.execute(
                text(
                    "UPDATE sync_jobs SET status = 'failed', finished_at = :now, error_message = :err WHERE id = :id"
                ),
                {"now": datetime.now(timezone.utc), "err": str(e), "id": job_id},
            )
            await session.commit()
            logger.exception("FACEIT sync failed")


async def process_enrichment_jobs(ctx):
    from sqlalchemy import text

    async with Session() as session:
        result = await session.execute(
            text(
                "SELECT id, job_type FROM sync_jobs WHERE status = 'pending' AND job_type LIKE 'enrich_player:%' LIMIT 20"
            )
        )
        jobs = result.fetchall()

    for job_id, job_type in jobs:
        player_id = job_type.split(":", 1)[1]
        try:
            await enrich_player(ctx, player_id)
            async with Session() as session:
                from sqlalchemy import text

                await session.execute(
                    text("UPDATE sync_jobs SET status = 'completed', finished_at = :now WHERE id = :id"),
                    {"now": datetime.now(timezone.utc), "id": str(job_id)},
                )
                await session.commit()
        except Exception as e:
            logger.exception("Enrichment failed for %s", player_id)


async def run_enrichment_batch(ctx):
    from sqlalchemy import text

    async with Session() as session:
        result = await session.execute(
            text("SELECT id FROM players ORDER BY last_seen_at DESC LIMIT 50")
        )
        player_ids = [str(row[0]) for row in result.fetchall()]

    for pid in player_ids:
        await enrich_player(ctx, pid)


class WorkerSettings:
    redis_settings = RedisSettings.from_dsn(settings.redis_url)
    functions = [enrich_player, sync_faceit_matches, process_enrichment_jobs, run_enrichment_batch]
    cron_jobs = [
        cron(sync_faceit_matches, hour={0, 6, 12, 18}, minute=0),
        cron(process_enrichment_jobs, minute={0, 5, 10, 15, 20, 25, 30, 35, 40, 45, 50, 55}),
    ]
