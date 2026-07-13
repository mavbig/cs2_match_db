"use client";

import { useState } from "react";
import { api } from "@/lib/api";

interface ProfileDebugSummary {
  faceit_captured_at?: string | null;
  faceit_lifetime_in_snapshot?: Record<string, unknown> | null;
  faceit_recent_in_snapshot?: Record<string, unknown> | null;
  faceit_normalized_lifetime?: Record<string, unknown> | null;
  faceit_api_lifetime_keys?: string[];
  faceit_merged_lifetime_keys?: string[];
}

interface ProfileDebugResponse {
  summary?: ProfileDebugSummary;
}

export function PlayerProfileDebug({ playerId }: { playerId: string }) {
  const [open, setOpen] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [debugJson, setDebugJson] = useState<string | null>(null);
  const [summary, setSummary] = useState<ProfileDebugSummary | null>(null);
  const [copied, setCopied] = useState(false);

  async function loadDebug() {
    setLoading(true);
    setError(null);
    try {
      const data = (await api.playerProfileDebug(playerId)) as ProfileDebugResponse & Record<string, unknown>;
      setSummary(data.summary ?? null);
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
        Sync profile first. The summary below shows what the UI should display for FACEIT lifetime stats.
      </p>
      {error && <p className="match-actions-status">{error}</p>}
      {open && summary && (
        <div className="player-profile-debug-summary card">
          <h3 style={{ fontSize: "0.95rem", marginBottom: "0.65rem" }}>FACEIT debug summary</h3>
          {summary.faceit_captured_at && (
            <p style={{ fontSize: "0.82rem", color: "var(--muted)", marginBottom: "0.65rem" }}>
              Snapshot captured: {new Date(summary.faceit_captured_at).toLocaleString()}
            </p>
          )}
          <p style={{ fontSize: "0.82rem", marginBottom: "0.35rem" }}>
            <strong>Lifetime in snapshot (UI source):</strong>
          </p>
          <pre className="player-profile-debug-output" style={{ maxHeight: 180 }}>
            {JSON.stringify(summary.faceit_lifetime_in_snapshot ?? {}, null, 2)}
          </pre>
          <p style={{ fontSize: "0.82rem", margin: "0.75rem 0 0.35rem" }}>
            <strong>Parser output:</strong>
          </p>
          <pre className="player-profile-debug-output" style={{ maxHeight: 180 }}>
            {JSON.stringify(summary.faceit_normalized_lifetime ?? {}, null, 2)}
          </pre>
        </div>
      )}
      {open && debugJson && (
        <pre className="player-profile-debug-output">{debugJson}</pre>
      )}
    </div>
  );
}
