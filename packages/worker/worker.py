import json
import logging
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


async def _get_setting(session: AsyncSession, key: str) -> str | None:
    from sqlalchemy import text

    result = await session.execute(text("SELECT value FROM app_settings WHERE key = :k"), {"k": key})
    row = result.fetchone()
    return row[0] if row else None


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
                    "https://open.faceit.com/data/v4/players",
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


async def sync_faceit_matches(ctx):
    from sqlalchemy import text

    async with Session() as session:
        faceit_key = await _get_setting(session, "faceit_api_key") or settings.faceit_api_key
        faceit_nick = await _get_setting(session, "faceit_nickname") or settings.faceit_nickname

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

        try:
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.get(
                    "https://open.faceit.com/data/v4/players",
                    params={"nickname": faceit_nick},
                    headers={"Authorization": f"Bearer {faceit_key}"},
                )
                resp.raise_for_status()
                player = resp.json()

            offset = 0
            while offset < 100:
                async with httpx.AsyncClient(timeout=30) as client:
                    resp = await client.get(
                        f"https://open.faceit.com/data/v4/players/{player['player_id']}/history",
                        params={"game": "cs2", "offset": offset, "limit": 20},
                        headers={"Authorization": f"Bearer {faceit_key}"},
                    )
                    resp.raise_for_status()
                    history = resp.json()

                items = history.get("items", [])
                if not items:
                    break

                for item in items:
                    match_id = item.get("match_id")
                    if not match_id:
                        continue

                    played_at = datetime.fromtimestamp(item.get("finished_at", 0), tz=timezone.utc)
                    await session.execute(
                        text(
                            "INSERT INTO matches (id, source, source_match_id, map, mode, played_at, raw_payload) "
                            "VALUES (gen_random_uuid(), 'faceit', :mid, NULL, 'faceit', :played, CAST(:raw AS jsonb)) "
                            "ON CONFLICT (source, source_match_id) DO NOTHING"
                        ),
                        {"mid": match_id, "played": played_at, "raw": json.dumps(item)},
                    )
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
