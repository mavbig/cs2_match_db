import Link from "next/link";
import { api, formatDate, formatMap, formatMatchLabel, formatMatchScore } from "@/lib/api";

export default async function DashboardPage() {
  let data;
  try {
    data = await api.dashboard();
  } catch {
    return (
      <div className="card">
        <h1>CS2 Match DB</h1>
        <p style={{ color: "var(--muted)", marginTop: "0.5rem" }}>
          API not reachable. Start the stack with <code>docker compose up</code> and configure settings.
        </p>
        <Link href="/settings" className="btn btn-primary" style={{ marginTop: "1rem", display: "inline-flex" }}>
          Go to Settings
        </Link>
      </div>
    );
  }

  const { recent_matches, top_teammates, sync_status } = data;

  return (
    <div>
      <div style={{ marginBottom: "1.5rem" }}>
        <h1 style={{ fontSize: "1.75rem", fontWeight: 700 }}>Dashboard</h1>
        <p style={{ color: "var(--muted)", marginTop: "0.25rem" }}>
          Your CS2 match history and player database
        </p>
      </div>

      {!sync_status.steam_configured && (
        <div className="card" style={{ marginBottom: "1.5rem", borderColor: "var(--warning)" }}>
          <strong>Setup required</strong>
          <p style={{ color: "var(--muted)", marginTop: "0.5rem" }}>
            Add your Steam auth code and share code in Settings to start syncing matches.
          </p>
          <Link href="/settings" className="btn btn-primary" style={{ marginTop: "0.75rem", display: "inline-flex" }}>
            Complete Setup
          </Link>
        </div>
      )}

      <div className="stat-grid" style={{ marginBottom: "1.5rem" }}>
        <div className="stat-box">
          <div className="value">{sync_status.total_matches}</div>
          <div className="label">Total Matches</div>
        </div>
        <div className="stat-box">
          <div className="value">{sync_status.total_players}</div>
          <div className="label">Players Indexed</div>
        </div>
        <div className="stat-box">
          <div className="value">{sync_status.steam_configured ? "✓" : "—"}</div>
          <div className="label">Steam Sync</div>
        </div>
        <div className="stat-box">
          <div className="value">{sync_status.faceit_configured ? "✓" : "—"}</div>
          <div className="label">FACEIT Sync</div>
        </div>
      </div>

      <div className="grid-2">
        <div className="card">
          <h2 style={{ fontSize: "1.1rem", marginBottom: "1rem" }}>Recent Matches</h2>
          {recent_matches.length === 0 ? (
            <p style={{ color: "var(--muted)" }}>No matches yet.</p>
          ) : (
            <table>
              <thead>
                <tr>
                  <th>Match</th>
                  <th>Map</th>
                  <th>Score</th>
                  <th>Date</th>
                </tr>
              </thead>
              <tbody>
                {recent_matches.map((m) => (
                  <tr key={m.id}>
                    <td>
                      <Link href={`/matches/${m.id}`}>{formatMatchLabel(m)}</Link>
                      <span className="badge badge-blue" style={{ marginLeft: "0.5rem" }}>
                        {m.source}
                      </span>
                    </td>
                    <td>{formatMap(m.map)}</td>
                    <td>{formatMatchScore(m.source, m.score_team_a, m.score_team_b)}</td>
                    <td style={{ color: "var(--muted)", fontSize: "0.85rem" }}>{formatDate(m.played_at)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>

        <div className="card">
          <h2 style={{ fontSize: "1.1rem", marginBottom: "1rem" }}>Most Played With</h2>
          {top_teammates.length === 0 ? (
            <p style={{ color: "var(--muted)" }}>No co-play data yet.</p>
          ) : (
            <table>
              <thead>
                <tr>
                  <th>Player</th>
                  <th>Times</th>
                  <th>Last</th>
                </tr>
              </thead>
              <tbody>
                {top_teammates.map((t) => (
                  <tr key={t.player.id}>
                    <td>
                      <Link href={`/players/${t.player.id}`}>
                        {t.player.current_name ?? t.player.steam64_id}
                      </Link>
                    </td>
                    <td>
                      <span className="badge badge-green">{t.times_together}</span>
                    </td>
                    <td style={{ color: "var(--muted)", fontSize: "0.85rem" }}>
                      {formatDate(t.last_together)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      </div>
    </div>
  );
}
