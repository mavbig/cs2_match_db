import Link from "next/link";
import { notFound } from "next/navigation";
import { FaceitProfile, FaceitProfileStats } from "@/components/FaceitProfile";
import { api, formatDate } from "@/lib/api";

export default async function PlayerPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  let player;
  try {
    player = await api.player(id);
  } catch {
    notFound();
  }

  const leetify = player.latest_stats.leetify as Record<string, unknown> | undefined;
  const rating = leetify?.rating as Record<string, number> | undefined;
  const faceit = player.latest_stats.faceit as FaceitProfileStats | undefined;

  return (
    <div>
      <div style={{ marginBottom: "1.5rem" }}>
        <Link href="/search" style={{ color: "var(--muted)", fontSize: "0.9rem" }}>
          ← Back to search
        </Link>
        <div style={{ display: "flex", gap: "1.25rem", alignItems: "center", marginTop: "0.75rem" }}>
          {player.avatar_url && (
            <img src={player.avatar_url} alt="" style={{ width: 80, height: 80, borderRadius: 10 }} />
          )}
          <div>
            <h1 style={{ fontSize: "1.75rem", fontWeight: 700 }}>{player.current_name ?? "Unknown"}</h1>
            <div style={{ display: "flex", gap: "1rem", marginTop: "0.35rem", fontSize: "0.9rem" }}>
              <a href={player.profile_url ?? "#"} target="_blank" rel="noopener noreferrer">
                Steam Profile
              </a>
              <span style={{ color: "var(--muted)", fontFamily: "monospace" }}>{player.steam64_id}</span>
            </div>
          </div>
        </div>
      </div>

      <div className="stat-grid" style={{ marginBottom: "1.5rem" }}>
        <div className="stat-box">
          <div className="value">{player.times_played_with_me ?? 0}</div>
          <div className="label">Times Played With You</div>
        </div>
        <div className="stat-box">
          <div className="value">{player.match_count}</div>
          <div className="label">Matches in DB</div>
        </div>
        {faceit?.elo != null && (
          <div className="stat-box">
            <div className="value">{faceit.elo}</div>
            <div className="label">FACEIT ELO</div>
          </div>
        )}
        {faceit?.lifetime?.kd != null && (
          <div className="stat-box">
            <div className="value">{faceit.lifetime.kd.toFixed(2)}</div>
            <div className="label">FACEIT K/D</div>
          </div>
        )}
      </div>

      {faceit && <FaceitProfile faceit={faceit} />}

      {rating && (
        <div className="card" style={{ marginBottom: "1.5rem" }}>
          <h2 style={{ fontSize: "1.1rem", marginBottom: "1rem" }}>Leetify Ratings</h2>
          <div className="stat-grid">
            {Object.entries(rating).map(([key, val]) => (
              <div key={key} className="stat-box">
                <div className="value">{typeof val === "number" ? Math.round(val) : "—"}</div>
                <div className="label">{key.replace(/_/g, " ")}</div>
              </div>
            ))}
          </div>
        </div>
      )}

      {player.platform_accounts.length > 0 && (
        <div className="card" style={{ marginBottom: "1.5rem" }}>
          <h2 style={{ fontSize: "1.1rem", marginBottom: "1rem" }}>Platform Accounts</h2>
          {player.platform_accounts.map((a) => (
            <div key={a.external_id} style={{ marginBottom: "0.5rem" }}>
              <span className="badge">{a.platform}</span>{" "}
              {a.profile_url ? (
                <a href={a.profile_url} target="_blank" rel="noopener noreferrer">
                  {a.nickname ?? a.external_id}
                </a>
              ) : (
                a.nickname ?? a.external_id
              )}
            </div>
          ))}
        </div>
      )}

      {player.name_history.length > 0 && (
        <div className="card" style={{ marginBottom: "1.5rem" }}>
          <h2 style={{ fontSize: "1.1rem", marginBottom: "1rem" }}>Name History</h2>
          <div style={{ display: "flex", flexWrap: "wrap", gap: "0.5rem" }}>
            {player.name_history.map((n) => (
              <span key={n} className="badge">
                {n}
              </span>
            ))}
          </div>
        </div>
      )}

      <div className="card">
        <h2 style={{ fontSize: "1.1rem", marginBottom: "0.5rem" }}>First / Last Seen</h2>
        <p style={{ color: "var(--muted)", fontSize: "0.9rem" }}>
          First: {formatDate(player.first_seen_at)} · Last: {formatDate(player.last_seen_at)}
        </p>
      </div>
    </div>
  );
}
