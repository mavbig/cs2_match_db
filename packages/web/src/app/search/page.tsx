"use client";

import { useState } from "react";
import Link from "next/link";
import { PlayerProfileLinks } from "@/components/PlayerProfileLinks";
import { api, formatDate, Player, PlayerDetail } from "@/lib/api";

export default function SearchPage() {
  const [query, setQuery] = useState("");
  const [results, setResults] = useState<Player[]>([]);
  const [lookup, setLookup] = useState<PlayerDetail | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function handleSearch(e: React.FormEvent) {
    e.preventDefault();
    if (!query.trim()) return;
    setLoading(true);
    setError(null);
    setLookup(null);
    try {
      const isUrl = query.includes("steamcommunity") || /^\d{17}$/.test(query.trim());
      if (isUrl) {
        const detail = await api.lookupPlayer(query.trim());
        setLookup(detail);
        setResults([]);
      } else {
        const res = await api.searchPlayers(query.trim());
        setResults(res.players);
        setLookup(null);
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "Search failed");
    } finally {
      setLoading(false);
    }
  }

  return (
    <div>
      <h1 className="page-title">Player Search</h1>
      <p className="page-subtitle">Paste a Steam profile URL, Steam64 ID, or search by name</p>

      <form onSubmit={handleSearch} style={{ display: "flex", gap: "0.75rem", marginBottom: "1.5rem" }}>
        <input
          className="input"
          placeholder="https://steamcommunity.com/id/mavbig or player name"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
        />
        <button type="submit" className="btn btn-primary" disabled={loading}>
          {loading ? "Searching…" : "Search"}
        </button>
      </form>

      {error && (
        <div className="card" style={{ borderColor: "var(--danger)", marginBottom: "1rem", color: "var(--danger)" }}>
          {error}
        </div>
      )}

      {lookup && (
        <div className="card" style={{ marginBottom: "1.5rem" }}>
          <div style={{ display: "flex", gap: "1rem", alignItems: "center", marginBottom: "1rem" }}>
            {lookup.avatar_url && (
              <img src={lookup.avatar_url} alt="" style={{ width: 64, height: 64, borderRadius: 8 }} />
            )}
            <div>
              <h2 style={{ fontSize: "1.25rem" }}>
                <Link href={`/players/${lookup.id}`}>{lookup.current_name ?? lookup.steam64_id}</Link>
              </h2>
              <PlayerProfileLinks
                steam64Id={lookup.steam64_id}
                steamProfileUrl={lookup.profile_url}
                faceit={(() => {
                  const acct = lookup.platform_accounts.find((a) => a.platform === "faceit");
                  const faceit = lookup.latest_stats.faceit as { profile_url?: string; elo?: number; nickname?: string } | undefined;
                  const url = faceit?.profile_url ?? acct?.profile_url;
                  return url ? { profileUrl: url, elo: faceit?.elo, nickname: faceit?.nickname ?? acct?.nickname } : null;
                })()}
                leetifyAvailable={Boolean(lookup.latest_stats.leetify)}
              />
            </div>
          </div>

          <div className="stat-grid" style={{ marginBottom: "1rem" }}>
            <div className="stat-box">
              <div className="value">{lookup.times_played_with_me ?? 0}</div>
              <div className="label">Times Played Together</div>
            </div>
            <div className="stat-box">
              <div className="value">{lookup.match_count}</div>
              <div className="label">Matches in DB</div>
            </div>
          </div>

          {lookup.name_history.length > 0 && (
            <div>
              <h3 style={{ fontSize: "0.9rem", color: "var(--muted)", marginBottom: "0.5rem" }}>Name History</h3>
              <div style={{ display: "flex", flexWrap: "wrap", gap: "0.5rem" }}>
                {lookup.name_history.map((n) => (
                  <span key={n} className="badge">
                    {n}
                  </span>
                ))}
              </div>
            </div>
          )}

          <Link href={`/players/${lookup.id}`} className="btn" style={{ marginTop: "1rem", display: "inline-flex" }}>
            View Full Profile
          </Link>
        </div>
      )}

      {results.length > 0 && (
        <div className="card">
          <h2 style={{ fontSize: "1.1rem", marginBottom: "1rem" }}>Results</h2>
          <table>
            <thead>
              <tr>
                <th>Name</th>
                <th>Steam64</th>
                <th>Last Seen</th>
              </tr>
            </thead>
            <tbody>
              {results.map((p) => (
                <tr key={p.id}>
                  <td>
                    <Link href={`/players/${p.id}`}>{p.current_name ?? "Unknown"}</Link>
                  </td>
                  <td style={{ fontFamily: "monospace", fontSize: "0.85rem" }}>{p.steam64_id}</td>
                  <td style={{ color: "var(--muted)", fontSize: "0.85rem" }}>{formatDate(p.last_seen_at)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
