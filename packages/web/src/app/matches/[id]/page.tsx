import Link from "next/link";
import { notFound } from "next/navigation";
import { MatchActions } from "@/components/MatchActions";
import { MatchDebugPanel } from "@/components/MatchDebugPanel";
import { MatchScoreboard } from "@/components/MatchScoreboard";
import { MatchSourceBadge } from "@/components/MatchSourceBadge";
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

  const isFaceit = match.source === "faceit";

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
          <MatchSourceBadge source={match.source} size={24} />
          {match.mode && <span className="badge badge-orange">{match.mode}</span>}
          {match.source === "steam_gc" && (
            <Link href={showDebug ? `/matches/${id}` : `/matches/${id}?debug=1`} className="btn btn-ghost" style={{ padding: "0.25rem 0.6rem", fontSize: "0.8rem" }}>
              {showDebug ? "Hide debug" : "Show GC debug"}
            </Link>
          )}
        </div>
        <MatchActions
          matchId={id}
          demoUrl={match.demo_url}
          source={match.source}
          syncStatus={match.sync_status}
        />
      </div>

      <MatchScoreboard
        players={match.players}
        scoreTeamA={match.score_team_a}
        scoreTeamB={match.score_team_b}
        source={match.source}
        isFaceit={isFaceit}
      />

      {gcDebug && <MatchDebugPanel debug={gcDebug} />}
    </div>
  );
}

