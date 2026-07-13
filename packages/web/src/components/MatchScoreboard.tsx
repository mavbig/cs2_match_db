import Link from "next/link";
import type { MatchPlayer } from "@/lib/api";

function sortPlayers(players: MatchPlayer[]): MatchPlayer[] {
  return [...players].sort((a, b) => {
    const scoreDiff = (b.score ?? 0) - (a.score ?? 0);
    if (scoreDiff !== 0) return scoreDiff;
    const killDiff = (b.kills ?? 0) - (a.kills ?? 0);
    if (killDiff !== 0) return killDiff;
    return (a.deaths ?? 99) - (b.deaths ?? 99);
  });
}

type TeamOutcome = "win" | "loss" | "draw" | null;

function teamOutcome(score: number | null, opponentScore: number | null): TeamOutcome {
  if (score == null || opponentScore == null) return null;
  if (score > opponentScore) return "win";
  if (score < opponentScore) return "loss";
  return "draw";
}

function outcomeClass(outcome: TeamOutcome): string {
  if (outcome === "win") return "team-win";
  if (outcome === "loss") return "team-loss";
  if (outcome === "draw") return "team-draw";
  return "";
}

function PlayerTable({
  players,
  teamClass,
}: {
  players: MatchPlayer[];
  teamClass: "team-a" | "team-b";
}) {
  const sorted = sortPlayers(players);

  return (
    <table className={`scoreboard-table scoreboard-table-${teamClass}`}>
      <thead>
        <tr>
          <th>Player</th>
          <th title="Have you played with this player before?">With you</th>
          <th>K</th>
          <th>A</th>
          <th>D</th>
          <th>★</th>
          <th>HSP</th>
          <th>Score</th>
        </tr>
      </thead>
      <tbody>
        {sorted.map((p) => (
          <tr key={p.player_id} className={p.is_me ? "is-me" : undefined}>
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
            <td style={{ textAlign: "center" }}>
              {p.is_me ? (
                "—"
              ) : (p.times_played_with_me ?? 0) > 0 ? (
                <span className="badge" title={`${p.times_played_with_me} matches with you`}>
                  ✓
                </span>
              ) : (
                "—"
              )}
            </td>
            <td>{p.kills ?? "—"}</td>
            <td>{p.assists ?? "—"}</td>
            <td>{p.deaths ?? "—"}</td>
            <td>{p.mvps ? `★${p.mvps}` : "—"}</td>
            <td>{p.headshot_pct != null ? `${Math.round(p.headshot_pct)}%` : "—"}</td>
            <td className="score-cell">{p.score ?? "—"}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

interface MatchScoreboardProps {
  players: MatchPlayer[];
  scoreTeamA: number | null;
  scoreTeamB: number | null;
  source: string;
  isFaceit: boolean;
}

export function MatchScoreboard({ players, scoreTeamA, scoreTeamB, source, isFaceit }: MatchScoreboardProps) {
  const teamA = players.filter((p) => p.team === "team_a");
  const teamB = players.filter((p) => p.team === "team_b");
  const hasTeams = teamA.length > 0 && teamB.length > 0;

  const myTeam = players.find((p) => p.is_me)?.team;
  const teamLabel = (team: "team_a" | "team_b") => {
    if (myTeam === team) return "Your team";
    if (myTeam) return "Enemy team";
    return team === "team_a" ? "Team A" : "Team B";
  };

  const outcomeA = teamOutcome(scoreTeamA, scoreTeamB);
  const outcomeB = teamOutcome(scoreTeamB, scoreTeamA);

  if (!hasTeams) {
    const sorted = sortPlayers(players);
    return (
      <div className="card">
        <h2 style={{ fontSize: "1.1rem", marginBottom: "1rem" }}>Scoreboard</h2>
        <PlayerTable players={sorted} teamClass="team-a" />
      </div>
    );
  }

  const scoreLabel =
    scoreTeamA != null && scoreTeamB != null
      ? source === "steam_gc" && scoreTeamA <= 1 && scoreTeamB <= 1 && scoreTeamA + scoreTeamB === 1
        ? null
        : `${scoreTeamA} : ${scoreTeamB}`
      : null;

  return (
    <div className="card">
      <h2 style={{ fontSize: "1.1rem", marginBottom: "1rem" }}>Scoreboard</h2>
      <div className="scoreboard-grid">
        <div className={`team-panel team-a ${outcomeClass(outcomeA)}`}>
          <div className={`team-header team-a ${outcomeClass(outcomeA)}`}>
            <span>{teamLabel("team_a")}</span>
            {scoreTeamA != null && <span className="team-score">{scoreTeamA}</span>}
          </div>
          <PlayerTable players={teamA} teamClass="team-a" />
        </div>
        <div className={`team-panel team-b ${outcomeClass(outcomeB)}`}>
          <div className={`team-header team-b ${outcomeClass(outcomeB)}`}>
            <span>{teamLabel("team_b")}</span>
            {scoreTeamB != null && <span className="team-score">{scoreTeamB}</span>}
          </div>
          <PlayerTable players={teamB} teamClass="team-b" />
        </div>
      </div>
      {scoreLabel && (
        <p className="scoreboard-final" style={{ marginTop: "1rem", color: "var(--muted)", fontSize: "0.9rem" }}>
          Final score: {scoreLabel}
        </p>
      )}
    </div>
  );
}
