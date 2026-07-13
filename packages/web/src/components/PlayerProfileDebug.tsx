"use client";

import { useState } from "react";
import { api } from "@/lib/api";

export function PlayerProfileDebug({ playerId }: { playerId: string }) {
  const [open, setOpen] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [debugJson, setDebugJson] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);

  async function loadDebug() {
    setLoading(true);
    setError(null);
    try {
      const data = await api.playerProfileDebug(playerId);
      setDebugJson(JSON.stringify(data, null, 2));
      setOpen(true);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load debug data");
    } finally {
      setLoading(false);
    }
  }

  async function handleCopy() {
    if (!debugJson) {
      await loadDebug();
      return;
    }
    try {
      await navigator.clipboard.writeText(debugJson);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 2000);
    } catch {
      setError("Could not copy to clipboard");
    }
  }

  return (
    <div className="player-profile-debug">
      <div className="player-profile-debug-actions">
        <button type="button" className="btn" onClick={loadDebug} disabled={loading}>
          {loading ? "Loading debug…" : open ? "Refresh debug" : "Load profile debug"}
        </button>
        <button type="button" className="btn" onClick={handleCopy} disabled={loading}>
          {copied ? "Copied" : "Copy debug JSON"}
        </button>
      </div>
      <p className="player-profile-debug-hint">
        Shows raw FACEIT/Leetify snapshot payloads and parser output. Sync profile first, then copy and paste here.
      </p>
      {error && <p className="match-actions-status">{error}</p>}
      {open && debugJson && (
        <pre className="player-profile-debug-output">{debugJson}</pre>
      )}
    </div>
  );
}
