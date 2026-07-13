"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";
import { api } from "@/lib/api";

export function PlayerProfileSync({ playerId }: { playerId: string }) {
  const router = useRouter();
  const [syncing, setSyncing] = useState(false);
  const [message, setMessage] = useState<string | null>(null);

  async function handleSync() {
    setSyncing(true);
    setMessage(null);
    try {
      const result = await api.syncPlayer(playerId);
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

  return (
    <div className="player-profile-sync">
      <button
        type="button"
        className="btn btn-primary"
        onClick={handleSync}
        disabled={syncing}
        title="Fetches Steam, Leetify, and FACEIT stats for this player (like csstats.gg)."
      >
        {syncing ? "Syncing profile…" : "Sync profile"}
      </button>
      {message && <span className="match-actions-status">{message}</span>}
    </div>
  );
}
