import { completeJob, fetchSyncConfig, postMatches, startJob } from "./api-poster.js";
import { GcClient } from "./gc-client.js";

const POLL_INTERVAL = Number(process.env.SYNC_POLL_INTERVAL ?? 300) * 1000;

const BOT_USERNAME = process.env.STEAM_BOT_USERNAME ?? "";
const BOT_PASSWORD = process.env.STEAM_BOT_PASSWORD ?? "";
const BOT_SHARED_SECRET = process.env.STEAM_BOT_SHARED_SECRET ?? "";

let gcClient: GcClient | null = null;
let lastSyncedShareCode: string | null = null;

async function ensureGcConnected(): Promise<GcClient> {
  if (gcClient) {
    try {
      await gcClient.waitForGc(10000);
      return gcClient;
    } catch {
      gcClient = null;
    }
  }

  if (!BOT_USERNAME || !BOT_PASSWORD) {
    throw new Error("STEAM_BOT_USERNAME and STEAM_BOT_PASSWORD must be set");
  }

  gcClient = new GcClient();
  await gcClient.login(BOT_USERNAME, BOT_PASSWORD, BOT_SHARED_SECRET || undefined);
  await gcClient.waitForGc();
  return gcClient;
}

async function runFullSync(): Promise<number> {
  const config = await fetchSyncConfig();
  if (!config?.steam_auth_code || !config?.steam_oldest_share_code || !config?.my_steam64_id) {
    console.log("[steam-sync] Steam sync not configured yet, skipping");
    return 0;
  }

  const jobId = await startJob("steam_gc");
  let imported = 0;

  try {
    const client = await ensureGcConnected();
    const matches = await client.walkShareCodeChain(
      config.steam_oldest_share_code,
      config.steam_auth_code,
      config.my_steam64_id,
      200
    );

    if (matches.length) {
      const batchSize = 10;
      for (let i = 0; i < matches.length; i += batchSize) {
        const batch = matches.slice(i, i + batchSize);
        const result = await postMatches(batch);
        imported += result.created + result.updated;
      }
      lastSyncedShareCode = matches[0]?.share_code ?? lastSyncedShareCode;
    }

    if (jobId) await completeJob(jobId, imported);
    console.log(`[steam-sync] Full sync complete: ${imported} matches`);
    return imported;
  } catch (err) {
    const msg = err instanceof Error ? err.message : String(err);
    console.error("[steam-sync] Full sync failed:", msg);
    if (jobId) await completeJob(jobId, imported, msg);
    return imported;
  }
}

async function runIncrementalSync(): Promise<number> {
  const config = await fetchSyncConfig();
  if (!config?.steam_auth_code || !config?.my_steam64_id) return 0;

  const jobId = await startJob("steam_gc");
  let imported = 0;

  try {
    const client = await ensureGcConnected();

    const shareCode = lastSyncedShareCode ?? config.steam_oldest_share_code;
    if (!shareCode) return 0;

    const match = await client.fetchMatchByShareCode(shareCode, config.my_steam64_id);
    if (match) {
      const result = await postMatches([match]);
      imported = result.created + result.updated;
      lastSyncedShareCode = match.share_code;
    }

    if (jobId) await completeJob(jobId, imported);
    return imported;
  } catch (err) {
    const msg = err instanceof Error ? err.message : String(err);
    if (jobId) await completeJob(jobId, imported, msg);
    return imported;
  }
}

async function main(): Promise<void> {
  console.log("[steam-sync] Starting CS2 match sync service");
  console.log(`[steam-sync] Poll interval: ${POLL_INTERVAL / 1000}s`);

  let fullSyncDone = false;

  while (true) {
    try {
      if (!fullSyncDone) {
        const count = await runFullSync();
        fullSyncDone = count >= 0;
      } else {
        await runIncrementalSync();
      }
    } catch (err) {
      console.error("[steam-sync] Sync loop error:", err);
      gcClient = null;
    }

    await new Promise((r) => setTimeout(r, POLL_INTERVAL));
  }
}

main().catch((err) => {
  console.error("[steam-sync] Fatal error:", err);
  process.exit(1);
});
