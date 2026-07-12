"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";
import { api } from "@/lib/api";

interface MatchActionsProps {
  matchId: string;
  demoUrl?: string | null;
}

export function MatchActions({ matchId, demoUrl }: MatchActionsProps) {
  const router = useRouter();
  const [syncing, setSyncing] = useState(false);
  const [message, setMessage] = useState<string | null>(null);

  async function handleSync() {
    setSyncing(true);
    setMessage(null);
    try {
      const result = await api.syncMatch(matchId);
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
      <button type="button" className="btn btn-primary" onClick={handleSync} disabled={syncing}>
        {syncing ? "Syncing…" : "Sync match"}
      </button>
      <button type="button" className="btn" onClick={handleDemoDownload}>
        Download demo
      </button>
      {message && <span className="match-actions-status">{message}</span>}
    </div>
  );
}
