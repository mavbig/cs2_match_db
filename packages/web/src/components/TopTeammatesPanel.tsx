"use client";

import Link from "next/link";
import { useState } from "react";
import { api, formatDate, type PlayedWith } from "@/lib/api";

const PAGE_SIZE = 10;

export function TopTeammatesPanel({
  initialTeammates,
  initialHasMore,
}: {
  initialTeammates: PlayedWith[];
  initialHasMore: boolean;
}) {
  const [teammates, setTeammates] = useState(initialTeammates);
  const [hasMore, setHasMore] = useState(initialHasMore);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function loadMore() {
    setLoading(true);
    setError(null);
    try {
      const page = await api.teammates(PAGE_SIZE, teammates.length);
      setTeammates((prev) => [...prev, ...page.teammates]);
      setHasMore(page.has_more);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load more players");
    } finally {
      setLoading(false);
    }
  }

  if (teammates.length === 0) {
    return <p style={{ color: "var(--muted)" }}>No co-play data yet.</p>;
  }

  return (
    <div>
      <table>
        <thead>
          <tr>
            <th>Player</th>
            <th>Times</th>
            <th>Last</th>
          </tr>
        </thead>
        <tbody>
          {teammates.map((t) => (
            <tr key={t.player.id}>
              <td>
                <Link href={`/players/${t.player.id}`}>{t.player.current_name ?? t.player.steam64_id}</Link>
              </td>
              <td>
                <span className="badge badge-green">{t.times_together}</span>
              </td>
              <td style={{ color: "var(--muted)", fontSize: "0.85rem" }}>{formatDate(t.last_together)}</td>
            </tr>
          ))}
        </tbody>
      </table>
      {error && <p className="match-actions-status" style={{ marginTop: "0.75rem" }}>{error}</p>}
      {hasMore && (
        <button
          type="button"
          className="btn btn-ghost"
          style={{ marginTop: "0.75rem" }}
          onClick={loadMore}
          disabled={loading}
        >
          {loading ? "Loading…" : `Show ${PAGE_SIZE} more`}
        </button>
      )}
    </div>
  );
}
