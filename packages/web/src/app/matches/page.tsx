import Link from "next/link";
import { api, formatDate, formatMap, formatMatchLabel, formatMatchScore, MatchSummary } from "@/lib/api";

export default async function MatchesPage() {
  let matches: MatchSummary[] = [];
  try {
    matches = await api.matches(50);
  } catch {
    matches = [];
  }

  return (
    <div>
      <h1 style={{ fontSize: "1.75rem", fontWeight: 700, marginBottom: "1.5rem" }}>Match History</h1>
      <div className="card">
        {matches.length === 0 ? (
          <p style={{ color: "var(--muted)" }}>No matches indexed yet.</p>
        ) : (
          <table>
            <thead>
              <tr>
                <th>Match</th>
                <th>Map</th>
                <th>Mode</th>
                <th>Score</th>
                <th>Players</th>
                <th>Date</th>
                <th>Source</th>
              </tr>
            </thead>
            <tbody>
              {matches.map((m) => (
                <tr key={m.id}>
                  <td>
                    <Link href={`/matches/${m.id}`}>{formatMatchLabel(m)}</Link>
                  </td>
                  <td>{formatMap(m.map)}</td>
                  <td>{m.mode ?? "—"}</td>
                  <td>{formatMatchScore(m.source, m.score_team_a, m.score_team_b)}</td>
                  <td>{m.player_count}</td>
                  <td style={{ color: "var(--muted)", fontSize: "0.85rem" }}>{formatDate(m.played_at)}</td>
                  <td>
                    <span className="badge">{m.source}</span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
}
