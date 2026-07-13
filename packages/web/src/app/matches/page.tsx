import Link from "next/link";
import { MatchPagination } from "@/components/MatchPagination";
import { MatchSourceBadge } from "@/components/MatchSourceBadge";
import { api, formatDate, formatMap, formatMatchLabel, formatMatchScore, MatchSummary } from "@/lib/api";

const PAGE_SIZE = 25;

export default async function MatchesPage({
  searchParams,
}: {
  searchParams: Promise<{ page?: string }>;
}) {
  const { page: pageParam } = await searchParams;
  const page = Math.max(1, parseInt(pageParam ?? "1", 10) || 1);
  const offset = (page - 1) * PAGE_SIZE;

  let matches: MatchSummary[] = [];
  let total = 0;

  try {
    [matches, { total }] = await Promise.all([
      api.matches(PAGE_SIZE, offset),
      api.matchCount(),
    ]);
  } catch {
    matches = [];
  }

  return (
    <div>
      <h1 className="page-title">Match History</h1>
      <p className="page-subtitle">{total > 0 ? `${total} matches indexed` : "Your synced match history"}</p>
      <div className="card">
        {matches.length === 0 ? (
          <p style={{ color: "var(--muted)" }}>No matches indexed yet.</p>
        ) : (
          <>
            <table>
              <thead>
                <tr>
                  <th>Match</th>
                  <th>Map</th>
                  <th>Mode</th>
                  <th>Score</th>
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
                    <td>
                      <span className="badge badge-orange">{m.mode ?? "—"}</span>
                    </td>
                    <td>{formatMatchScore(m.source, m.score_team_a, m.score_team_b)}</td>
                    <td style={{ color: "var(--muted)", fontSize: "0.85rem" }}>{formatDate(m.played_at)}</td>
                    <td>
                      <MatchSourceBadge source={m.source} />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
            <MatchPagination page={page} total={total} limit={PAGE_SIZE} />
          </>
        )}
      </div>
    </div>
  );
}
