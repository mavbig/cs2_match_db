"use client";

import { useEffect, useState } from "react";
import { api, Settings, SyncJob } from "@/lib/api";

function sleep(ms: number) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function formatSyncJobMessage(job: SyncJob | undefined): string {
  if (!job) return "Sync finished (job details unavailable).";
  if (job.status === "failed") {
    return job.error_message || "Sync failed.";
  }
  if (job.error_message) {
    return job.error_message;
  }
  return `Sync completed — ${job.matches_imported} matches processed.`;
}

function isPositiveMessage(message: string): boolean {
  return (
    message.includes("success") ||
    message.includes("triggered") ||
    message.includes("queued") ||
    message.includes("new") ||
    message.includes("updated") ||
    message.includes("enriched") ||
    message.includes("completed") ||
    message.includes("running")
  );
}

export default function SettingsPage() {
  const [settings, setSettings] = useState<Settings | null>(null);
  const [form, setForm] = useState({
    my_steam64_id: "",
    steam_auth_code: "",
    steam_oldest_share_code: "",
    steam_api_key: "",
    faceit_api_key: "",
    faceit_nickname: "",
    leetify_api_key: "",
  });
  const [shareCode, setShareCode] = useState("");
  const [message, setMessage] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    api.settings().then(setSettings).catch(() => setSettings(null));
  }, []);

  async function handleSave(e: React.FormEvent) {
    e.preventDefault();
    setLoading(true);
    setMessage(null);
    try {
      const payload: Record<string, string> = {};
      if (form.my_steam64_id) payload.my_steam64_id = form.my_steam64_id;
      if (form.steam_auth_code) payload.steam_auth_code = form.steam_auth_code;
      if (form.steam_oldest_share_code) payload.steam_oldest_share_code = form.steam_oldest_share_code;
      if (form.steam_api_key) payload.steam_api_key = form.steam_api_key;
      if (form.faceit_api_key) payload.faceit_api_key = form.faceit_api_key;
      if (form.faceit_nickname) payload.faceit_nickname = form.faceit_nickname;
      if (form.leetify_api_key) payload.leetify_api_key = form.leetify_api_key;

      const updated = await api.updateSettings(payload);
      setSettings(updated);
      setMessage("Settings saved successfully.");
    } catch (err) {
      setMessage(err instanceof Error ? err.message : "Failed to save");
    } finally {
      setLoading(false);
    }
  }

  async function pollSyncJob(jobId: string, label: string) {
    for (let i = 0; i < 720; i++) {
      await sleep(5000);
      try {
        const jobs = await api.syncJobs(10);
        const job = jobs.find((j) => j.id === jobId);
        if (!job) continue;
        if (job.status === "running" || job.status === "pending") {
          const progress =
            job.matches_imported > 0
              ? ` — ${job.matches_imported} matches processed so far`
              : "";
          setMessage(`${label} running${progress}…`);
          continue;
        }
        setMessage(formatSyncJobMessage(job));
        return;
      } catch {
        // keep polling
      }
    }
    setMessage(`${label} is still running — check docker compose logs -f worker`);
  }

  async function triggerSync(type: string) {
    setMessage(null);
    try {
      const job = await api.triggerSync(type);
      if (type === "steam_gc") {
        setMessage(
          "Steam full sync queued — steam-sync will start within ~15 seconds. Check progress: docker compose logs -f steam-sync"
        );
      } else if (type === "leetify_import") {
        setMessage("Leetify import started — fetching your match history…");
        void pollSyncJob(job.id, "Leetify import");
      } else if (type === "faceit") {
        setMessage("FACEIT sync started — this may take 30+ minutes…");
        void pollSyncJob(job.id, "FACEIT sync");
      } else if (type === "leetify") {
        setMessage("Leetify enrichment started…");
        void pollSyncJob(job.id, "Leetify enrichment");
      } else {
        setMessage(`${type} sync triggered.`);
      }
    } catch (err) {
      setMessage(err instanceof Error ? err.message : "Sync failed");
    }
  }

  async function importShareCode() {
    if (!shareCode.trim()) return;
    setMessage(null);
    try {
      const res = await api.importShareCode(shareCode.trim());
      setMessage(res.message);
      setShareCode("");
    } catch (err) {
      setMessage(err instanceof Error ? err.message : "Import failed");
    }
  }

  const step = !settings?.my_steam64_id ? 1 : !settings?.steam_auth_code_set ? 2 : !settings?.steam_oldest_share_code_set ? 3 : 4;

  return (
    <div>
      <h1 style={{ fontSize: "1.75rem", fontWeight: 700, marginBottom: "0.5rem" }}>Settings</h1>
      <p style={{ color: "var(--muted)", marginBottom: "1.5rem" }}>
        Configure your match sync and API keys
      </p>

      {!settings?.onboarding_complete && (
        <div className="card" style={{ marginBottom: "1.5rem", borderColor: "var(--accent)" }}>
          <h2 style={{ fontSize: "1.1rem", marginBottom: "0.75rem" }}>Onboarding — Step {step} of 4</h2>
          <ol style={{ color: "var(--muted)", paddingLeft: "1.25rem", lineHeight: 1.8, fontSize: "0.9rem" }}>
            <li style={{ color: step === 1 ? "var(--text)" : undefined }}>Enter your Steam64 ID</li>
            <li style={{ color: step === 2 ? "var(--text)" : undefined }}>
              Generate a{" "}
              <a
                href="https://help.steampowered.com/en/wizard/HelpWithGameIssue/?appid=730&issueid=128"
                target="_blank"
                rel="noopener noreferrer"
              >
                Match History Auth Code
              </a>
            </li>
            <li style={{ color: step === 3 ? "var(--text)" : undefined }}>
              Copy your oldest share code from CS2 → Watch → Your Matches
            </li>
            <li style={{ color: step === 4 ? "var(--text)" : undefined }}>Add API keys and trigger sync</li>
          </ol>
        </div>
      )}

      {message && (
        <div
          className="card"
          style={{
            marginBottom: "1rem",
            borderColor: isPositiveMessage(message) ? "var(--accent2)" : "var(--border)",
          }}
        >
          {message}
        </div>
      )}

      <form onSubmit={handleSave} className="card" style={{ marginBottom: "1.5rem" }}>
        <h2 style={{ fontSize: "1.1rem", marginBottom: "1rem" }}>Account & Sync</h2>

        <div style={{ display: "grid", gap: "1rem" }}>
          <label>
            <div style={{ marginBottom: "0.35rem", fontSize: "0.85rem", color: "var(--muted)" }}>Your Steam64 ID</div>
            <input
              className="input"
              placeholder={settings?.my_steam64_id ?? "76561198..."}
              value={form.my_steam64_id}
              onChange={(e) => setForm({ ...form, my_steam64_id: e.target.value })}
            />
          </label>

          <label>
            <div style={{ marginBottom: "0.35rem", fontSize: "0.85rem", color: "var(--muted)" }}>
              Match History Auth Code {settings?.steam_auth_code_set && <span className="badge badge-green">set</span>}
            </div>
            <input
              className="input"
              type="password"
              placeholder="XXXX-XXXX-XXXX"
              value={form.steam_auth_code}
              onChange={(e) => setForm({ ...form, steam_auth_code: e.target.value })}
            />
          </label>

          <label>
            <div style={{ marginBottom: "0.35rem", fontSize: "0.85rem", color: "var(--muted)" }}>
              Oldest Share Code {settings?.steam_oldest_share_code_set && <span className="badge badge-green">set</span>}
            </div>
            <input
              className="input"
              placeholder="CSGO-XXXXX-XXXXX-XXXXX-XXXXX-XXXXX"
              value={form.steam_oldest_share_code}
              onChange={(e) => setForm({ ...form, steam_oldest_share_code: e.target.value })}
            />
          </label>

          <label>
            <div style={{ marginBottom: "0.35rem", fontSize: "0.85rem", color: "var(--muted)" }}>
              Steam Web API Key {settings?.steam_api_key_set && <span className="badge badge-green">set</span>}
            </div>
            <input
              className="input"
              type="password"
              placeholder="Steam API key"
              value={form.steam_api_key}
              onChange={(e) => setForm({ ...form, steam_api_key: e.target.value })}
            />
          </label>

          <label>
            <div style={{ marginBottom: "0.35rem", fontSize: "0.85rem", color: "var(--muted)" }}>
              FACEIT API Key {settings?.faceit_api_key_set && <span className="badge badge-green">set</span>}
            </div>
            <input
              className="input"
              type="password"
              placeholder="FACEIT API key"
              value={form.faceit_api_key}
              onChange={(e) => setForm({ ...form, faceit_api_key: e.target.value })}
            />
          </label>

          <label>
            <div style={{ marginBottom: "0.35rem", fontSize: "0.85rem", color: "var(--muted)" }}>FACEIT Nickname</div>
            <input
              className="input"
              placeholder={settings?.faceit_nickname ?? "Your FACEIT nick"}
              value={form.faceit_nickname}
              onChange={(e) => setForm({ ...form, faceit_nickname: e.target.value })}
            />
          </label>

          <label>
            <div style={{ marginBottom: "0.35rem", fontSize: "0.85rem", color: "var(--muted)" }}>
              Leetify API Key (optional) {settings?.leetify_api_key_set && <span className="badge badge-green">set</span>}
            </div>
            <input
              className="input"
              type="password"
              placeholder="Leetify API key"
              value={form.leetify_api_key}
              onChange={(e) => setForm({ ...form, leetify_api_key: e.target.value })}
            />
          </label>
        </div>

        <button type="submit" className="btn btn-primary" style={{ marginTop: "1.25rem" }} disabled={loading}>
          {loading ? "Saving…" : "Save Settings"}
        </button>
      </form>

      <div className="card" style={{ marginBottom: "1.5rem" }}>
        <h2 style={{ fontSize: "1.1rem", marginBottom: "1rem" }}>Manual Actions</h2>
        <div style={{ display: "flex", gap: "0.75rem", flexWrap: "wrap", marginBottom: "1rem" }}>
          <button type="button" className="btn" onClick={() => triggerSync("steam_gc")}>
            Trigger Steam Sync
          </button>
          <button type="button" className="btn" onClick={() => triggerSync("faceit")}>
            Sync FACEIT (may take 30+ min)
          </button>
          <button type="button" className="btn" onClick={() => triggerSync("enrichment")}>
            Trigger Enrichment
          </button>
          <button type="button" className="btn" onClick={() => triggerSync("leetify")}>
            Enrich existing matches (Leetify)
          </button>
          <button type="button" className="btn btn-primary" onClick={() => triggerSync("leetify_import")}>
            Import all from Leetify
          </button>
        </div>
        <p style={{ color: "var(--muted)", fontSize: "0.85rem", marginBottom: "1rem" }}>
          Leetify import walks your full Leetify history (same API the website uses) in 6-month chunks,
          then fetches full match details. Steam sync only reaches back to your oldest share code.
          Large imports can take 30+ minutes.
        </p>

        <div style={{ display: "flex", gap: "0.75rem" }}>
          <input
            className="input"
            placeholder="CSGO-XXXXX-XXXXX-XXXXX-XXXXX-XXXXX"
            value={shareCode}
            onChange={(e) => setShareCode(e.target.value)}
          />
          <button type="button" className="btn btn-primary" onClick={importShareCode}>
            Import Share Code
          </button>
        </div>
      </div>
    </div>
  );
}
