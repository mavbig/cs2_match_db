import { completeJob, fetchSyncConfig, postMatches, startJob } from "./api-poster.js";
import { GcClient } from "./gc-client.js";
import { getAccountName, getRefreshToken, getSharedSecret, isThrottleError, loadMaFile, throttleBackoffMs } from "./mafile.js";

const POLL_INTERVAL = Number(process.env.SYNC_POLL_INTERVAL ?? 300) * 1000;

const BOT_USERNAME = process.env.STEAM_BOT_USERNAME ?? "";
const BOT_PASSWORD = process.env.STEAM_BOT_PASSWORD ?? "";
const BOT_SHARED_SECRET = process.env.STEAM_BOT_SHARED_SECRET ?? "";

let gcClient: GcClient | null = null;
let lastSyncedShareCode: string | null = null;
let loginBlockedUntil = 0;

async function ensureGcConnected(): Promise<GcClient> {
  if (Date.now() < loginBlockedUntil) {
    const waitMin = Math.ceil((loginBlockedUntil - Date.now()) / 60000);
    throw new Error(`Steam login throttled — retry in ~${waitMin} minutes`);
  }

  if (gcClient) {
    try {
      await gcClient.waitForGc(10000);
      return gcClient;
    } catch {
      gcClient = null;
    }
  }

  const mafile = loadMaFile();
  const username = getAccountName(mafile, BOT_USERNAME);
  const refreshToken = getRefreshToken(mafile);
  const sharedSecret = getSharedSecret(mafile) ?? BOT_SHARED_SECRET.trim();

  if (!username) {
    throw new Error("Set STEAM_BOT_USERNAME or use a maFile with account_name");
  }

  if (!refreshToken && (!BOT_PASSWORD || !sharedSecret)) {
    throw new Error(
      "Set STEAM_BOT_MAFILE_PATH (recommended, uses Session.RefreshToken), " +
        "or STEAM_BOT_PASSWORD + STEAM_BOT_SHARED_SECRET"
    );
  }

  gcClient = new GcClient();
  try {
    await gcClient.login(username, BOT_PASSWORD, sharedSecret || undefined);
    await gcClient.waitForGc();
    return gcClient;
  } catch (err) {
    gcClient = null;
    if (isThrottleError(err)) {
      loginBlockedUntil = Date.now() + throttleBackoffMs();
      console.error(
        `[steam-sync] AccountLoginDeniedThrottle — too many login attempts. ` +
          `Waiting ${Math.round(throttleBackoffMs() / 60000)} minutes. ` +
          `Stop the container with: docker compose stop steam-sync`
      );
    }
    throw err;
  }
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

  const mafile = loadMaFile();
  if (mafile?.Session?.RefreshToken) {
    console.log("[steam-sync] maFile detected with RefreshToken — will use token login");
  }

  let fullSyncDone = false;

  while (true) {
    try {
      if (Date.now() < loginBlockedUntil) {
        const waitMin = Math.ceil((loginBlockedUntil - Date.now()) / 60000);
        console.log(`[steam-sync] Login cooldown active, ${waitMin} min remaining...`);
      } else if (!fullSyncDone) {
        await runFullSync();
        fullSyncDone = true;
      } else {
        await runIncrementalSync();
      }
    } catch (err) {
      console.error("[steam-sync] Sync loop error:", err instanceof Error ? err.message : err);
      gcClient = null;
    }

    await new Promise((r) => setTimeout(r, POLL_INTERVAL));
  }
}

main().catch((err) => {
  console.error("[steam-sync] Fatal error:", err);
  process.exit(1);
});
