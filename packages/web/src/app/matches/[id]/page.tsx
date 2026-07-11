import Link from "next/link";
import { notFound } from "next/navigation";
import { api, formatDate, formatMap } from "@/lib/api";

export default async function MatchDetailPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  let match;
  try {
    match = await api.match(id);
  } catch {
    notFound();
  }

  const teamA = match.players.filter((p) => p.team === "team_a");
  const teamB = match.players.filter((p) => p.team === "team_b");
  const allPlayers = teamA.length ? [...teamA, ...teamB] : match.players;

  return (
    <div>
      <div style={{ marginBottom: "1.5rem" }}>
        <Link href="/matches" style={{ color: "var(--muted)", fontSize: "0.9rem" }}>
          ← Back to matches
        </Link>
        <h1 style={{ fontSize: "1.75rem", fontWeight: 700, marginTop: "0.5rem" }}>
          {formatMap(match.map)}
        </h1>
        <div style={{ display: "flex", gap: "1rem", marginTop: "0.5rem", color: "var(--muted)", fontSize: "0.9rem" }}>
          <span>{formatDate(match.played_at)}</span>
          <span>
            Score: {match.score_team_a ?? "?"} : {match.score_team_b ?? "?"}
          </span>
          <span className="badge">{match.source}</span>
          {match.mode && <span className="badge">{match.mode}</span>}
        </div>
      </div>

      <div className="card">
        <h2 style={{ fontSize: "1.1rem", marginBottom: "1rem" }}>Scoreboard</h2>
        <table>
          <thead>
            <tr>
              <th>Player</th>
              <th>Ping</th>
              <th>K</th>
              <th>A</th>
              <th>D</th>
              <th>★</th>
              <th>HSP</th>
              <th>Score</th>
            </tr>
          </thead>
          <tbody>
            {allPlayers.map((p) => (
              <tr key={p.player_id} style={p.is_me ? { background: "rgba(88,166,255,0.08)" } : {}}>
                <td>
                  <Link href={`/players/${p.player_id}`}>
                    {p.name ?? p.steam64_id}
                    {p.is_me && (
                      <span className="badge badge-blue" style={{ marginLeft: "0.5rem" }}>
                        you
                      </span>
                    )}
                  </Link>
                </td>
                <td>{p.ping ?? "—"}</td>
                <td>{p.kills ?? "—"}</td>
                <td>{p.assists ?? "—"}</td>
                <td>{p.deaths ?? "—"}</td>
                <td>{p.mvps ? `★${p.mvps}` : "—"}</td>
                <td>{p.headshot_pct != null ? `${Math.round(p.headshot_pct)}%` : "—"}</td>
                <td>{p.score ?? "—"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
