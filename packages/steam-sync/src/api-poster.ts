import type { NormalizedMatch } from "./match-parser.js";

const API_URL = process.env.API_URL ?? "http://localhost:8000";
const SYNC_TOKEN = process.env.API_SYNC_TOKEN ?? "change-this-sync-token";

export async function fetchSyncConfig(): Promise<{
  my_steam64_id: string;
  steam_auth_code: string;
  steam_oldest_share_code: string;
  steam_api_key: string;
  last_synced_share_code?: string | null;
  force_full_sync?: boolean;
} | null> {
  try {
    const resp = await fetch(`${API_URL}/api/v1/sync/config`, {
      headers: { "X-Sync-Token": SYNC_TOKEN },
    });
    if (!resp.ok) return null;
    return resp.json();
  } catch {
    return null;
  }
}

export async function postMatches(matches: NormalizedMatch[]): Promise<{ created: number; updated: number }> {
  const resp = await fetch(`${API_URL}/api/v1/ingest/matches`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "X-Sync-Token": SYNC_TOKEN,
    },
    body: JSON.stringify({ matches }),
  });
  if (!resp.ok) {
    const text = await resp.text();
    throw new Error(`Ingest failed: ${resp.status} ${text}`);
  }
  return resp.json();
}

export async function startJob(jobType: string): Promise<string | null> {
  const resp = await fetch(`${API_URL}/api/v1/sync/jobs/start?job_type=${jobType}`, {
    method: "POST",
    headers: { "X-Sync-Token": SYNC_TOKEN },
  });
  if (!resp.ok) return null;
  const data = await resp.json();
  return data.id;
}

export async function completeJob(jobId: string, matchesImported: number, error?: string): Promise<void> {
  const params = new URLSearchParams({ matches_imported: String(matchesImported) });
  if (error) params.set("error", error);
  await fetch(`${API_URL}/api/v1/sync/jobs/${jobId}/complete?${params}`, {
    method: "POST",
    headers: { "X-Sync-Token": SYNC_TOKEN },
  });
}

export async function ackForceFullSync(): Promise<void> {
  await fetch(`${API_URL}/api/v1/sync/ack-force-full`, {
    method: "POST",
    headers: { "X-Sync-Token": SYNC_TOKEN },
  });
}

export async function saveLastSyncedShareCode(shareCode: string): Promise<void> {
  await fetch(`${API_URL}/api/v1/sync/last-share-code`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "X-Sync-Token": SYNC_TOKEN,
    },
    body: JSON.stringify({ share_code: shareCode }),
  });
}

export async function getPendingShareCodeJobs(): Promise<string[]> {
  // Share code jobs are tracked via sync_jobs table; steam-sync polls API for pending imports
  return [];
}
