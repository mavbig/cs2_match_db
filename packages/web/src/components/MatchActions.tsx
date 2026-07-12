"use client";

import { useRouter } from "next/navigation";
import { useState, type ReactNode } from "react";
import { LeetifyIcon, SteamIcon } from "@/components/ProviderIcons";
import { api, formatRelativeTime, type MatchSyncStatus } from "@/lib/api";

interface MatchActionsProps {
  matchId: string;
  demoUrl?: string | null;
  source: string;
  syncStatus: MatchSyncStatus;
}

function SyncBadge({
  label,
  synced,
  syncedAt,
  icon,
}: {
  label: string;
  synced: boolean;
  syncedAt: string | null;
  icon: ReactNode;
}) {
  const statusText = synced
    ? syncedAt
      ? `Synced ${formatRelativeTime(syncedAt)}`
      : "Synced"
    : "Not synced";

  return (
    <div
      className={`sync-badge${synced ? " is-synced" : ""}`}
      title={`${label}: ${statusText}`}
    >
      {icon}
      <div className="sync-badge-text">
        <span className="sync-badge-label">{label}</span>
        <span className="sync-badge-time">{statusText}</span>
      </div>
    </div>
  );
}

export function MatchActions({ matchId, demoUrl, source, syncStatus }: MatchActionsProps) {
  const router = useRouter();
  const [syncing, setSyncing] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [status, setStatus] = useState(syncStatus);

  async function handleSync() {
    setSyncing(true);
    setMessage(null);
    try {
      const result = await api.syncMatch(matchId);
      if (result.sync_status) {
        setStatus(result.sync_status);
      }
      const parts: string[] = [];
      if (result.sources.length) {
        parts.push(`Updated: ${result.sources.join(", ")}`);
      }
      if (result.errors.length) {
        parts.push(result.errors.join("; "));
      }
      setMessage(parts.join(" · ") || "Nothing to update");
      router.refresh();
    } catch (err) {
      setMessage(err instanceof Error ? err.message : "Sync failed");
    } finally {
      setSyncing(false);
    }
  }

  async function handleDemoDownload() {
    setMessage(null);
    try {
      const { demo_url } = await api.matchDemoUrl(matchId);
      window.open(demo_url, "_blank", "noopener,noreferrer");
    } catch {
      if (demoUrl) {
        window.open(demoUrl, "_blank", "noopener,noreferrer");
        return;
      }
      setMessage("No demo URL available for this match");
    }
  }

  return (
    <div className="match-actions">
      <div className="match-actions-row">
        <div className="match-actions-buttons">
          <button type="button" className="btn btn-primary" onClick={handleSync} disabled={syncing}>
            {syncing ? "Syncing…" : "Sync match"}
          </button>
          <button type="button" className="btn" onClick={handleDemoDownload}>
            Download demo
          </button>
        </div>
        <div className="sync-badges">
          {source === "steam_gc" && (
            <SyncBadge
              label="Steam"
              synced={status.steam_synced}
              syncedAt={status.steam_synced_at}
              icon={<SteamIcon />}
            />
          )}
          <SyncBadge
            label="Leetify"
            synced={status.leetify_synced}
            syncedAt={status.leetify_synced_at}
            icon={<LeetifyIcon />}
          />
        </div>
      </div>
      {message && <span className="match-actions-status">{message}</span>}
    </div>
  );
}
