import Link from "next/link";
import { notFound } from "next/navigation";
import { MatchDebugPanel } from "@/components/MatchDebugPanel";
import { api, formatDate, formatMap, formatMatchScore } from "@/lib/api";

export default async function MatchDetailPage({
  params,
  searchParams,
}: {
  params: Promise<{ id: string }>;
  searchParams: Promise<{ debug?: string }>;
}) {
  const { id } = await params;
  const { debug: debugParam } = await searchParams;
  const showDebug = debugParam === "1";

  let match;
  try {
    match = await api.match(id);
  } catch {
    notFound();
  }

  let gcDebug = null;
  if (showDebug && match.source === "steam_gc") {
    try {
      gcDebug = await api.matchGcDebug(id);
    } catch {
      gcDebug = null;
    }
  }

  const teamA = match.players.filter((p) => p.team === "team_a");
  const teamB = match.players.filter((p) => p.team === "team_b");
  const allPlayers = teamA.length ? [...teamA, ...teamB] : match.players;
  const isFaceit = match.source === "faceit";
  const showPing = !isFaceit || allPlayers.some((p) => p.ping != null);

  return (
    <div>
      <div style={{ marginBottom: "1.5rem" }}>
        <Link href="/matches" className="btn btn-ghost" style={{ padding: "0.35rem 0", marginBottom: "0.5rem" }}>
          ← Back to matches
        </Link>
        <h1 className="page-title">{formatMap(match.map)}</h1>
        <div
          style={{
            display: "flex",
            gap: "0.75rem",
            marginTop: "0.5rem",
            color: "var(--muted)",
            fontSize: "0.9rem",
            flexWrap: "wrap",
            alignItems: "center",
          }}
        >
          <span>Match #{match.source_match_id.slice(-8)}</span>
          <span>{formatDate(match.played_at)}</span>
          <span>Score: {formatMatchScore(match.source, match.score_team_a, match.score_team_b)}</span>
          <span className="badge">{match.source}</span>
          {match.mode && <span className="badge badge-orange">{match.mode}</span>}
          {match.source === "steam_gc" && (
            <Link href={showDebug ? `/matches/${id}` : `/matches/${id}?debug=1`} className="btn btn-ghost" style={{ padding: "0.25rem 0.6rem", fontSize: "0.8rem" }}>
              {showDebug ? "Hide debug" : "Show GC debug"}
            </Link>
          )}
        </div>
      </div>

      <div className="card">
        <h2 style={{ fontSize: "1.1rem", marginBottom: "1rem" }}>Scoreboard</h2>
        {isFaceit && !showPing && (
          <p style={{ color: "var(--muted)", fontSize: "0.85rem", marginBottom: "0.75rem" }}>
            FACEIT&apos;s open API does not include per-player ping. Score is estimated from K/A/MVP when not provided.
          </p>
        )}
        <table>
          <thead>
            <tr>
              <th>Player</th>
              {showPing && <th>Ping</th>}
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
              <tr key={p.player_id} style={p.is_me ? { background: "rgba(59, 158, 255, 0.08)" } : {}}>
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
                {showPing && <td>{p.ping ?? "—"}</td>}
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

      {gcDebug && <MatchDebugPanel debug={gcDebug} />}
    </div>
  );
}

