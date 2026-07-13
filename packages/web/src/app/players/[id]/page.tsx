import Link from "next/link";
import { notFound } from "next/navigation";
import { FaceitProfile, FaceitProfileStats } from "@/components/FaceitProfile";
import { MatchSourceBadge } from "@/components/MatchSourceBadge";
import { PlayerProfileDebug } from "@/components/PlayerProfileDebug";
import { PlayerProfileLinks } from "@/components/PlayerProfileLinks";
import { PlayerProfileSync } from "@/components/PlayerProfileSync";
import { api, formatDate, formatMap, formatMatchLabel, formatMatchScore, PlayerMatch } from "@/lib/api";

export default async function PlayerPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  let player;
  let matches: PlayerMatch[] = [];
  try {
    [player, matches] = await Promise.all([api.player(id), api.playerMatches(id, 200)]);
  } catch {
    notFound();
  }

  const leetify = player.latest_stats.leetify as Record<string, unknown> | undefined;
  const rating = leetify?.rating as Record<string, number> | undefined;
  const faceit = player.latest_stats.faceit as FaceitProfileStats | undefined;
  const faceitAccount = player.platform_accounts.find((a) => a.platform === "faceit");
  const faceitProfileUrl =
    faceit?.profile_url ?? faceitAccount?.profile_url ?? null;
  const faceitElo = faceit?.elo ?? null;

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
            <PlayerProfileLinks
              steam64Id={player.steam64_id}
              steamProfileUrl={player.profile_url}
              faceit={
                faceitProfileUrl
                  ? { profileUrl: faceitProfileUrl, elo: faceitElo, nickname: faceit?.nickname ?? faceitAccount?.nickname }
                  : null
              }
              leetifyAvailable={Boolean(leetify)}
            />
            <div style={{ marginTop: "0.35rem", fontSize: "0.85rem", color: "var(--muted)", fontFamily: "monospace" }}>
              {player.steam64_id}
            </div>
            <div style={{ marginTop: "0.85rem" }}>
              <PlayerProfileSync playerId={player.id} />
            </div>
            <div style={{ marginTop: "0.85rem" }}>
              <PlayerProfileDebug playerId={player.id} />
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

      {leetify && (
        <div className="card" style={{ marginBottom: "1.5rem" }}>
          <h2 style={{ fontSize: "1.1rem", marginBottom: "1rem", display: "flex", alignItems: "center", gap: "0.5rem" }}>
            <MatchSourceBadge source="leetify" size={22} />
            Leetify
          </h2>
          <div className="stat-grid" style={{ marginBottom: rating ? "1rem" : 0 }}>
            {leetify.total_matches != null && (
              <div className="stat-box">
                <div className="value">{String(leetify.total_matches)}</div>
                <div className="label">Matches</div>
              </div>
            )}
            {leetify.winrate != null && (
              <div className="stat-box">
                <div className="value">{Math.round(Number(leetify.winrate) * 100)}%</div>
                <div className="label">Winrate</div>
              </div>
            )}
          </div>
          {rating && (
            <div className="stat-grid">
              {Object.entries(rating).map(([key, val]) => (
                <div key={key} className="stat-box">
                  <div className="value">{typeof val === "number" ? Math.round(val) : "—"}</div>
                  <div className="label">{key.replace(/_/g, " ")}</div>
                </div>
              ))}
            </div>
          )}
        </div>
      )}

      {player.platform_accounts.length > 0 && (
        <div className="card" style={{ marginBottom: "1.5rem" }}>
          <h2 style={{ fontSize: "1.1rem", marginBottom: "1rem" }}>Linked Accounts</h2>
          {player.platform_accounts
            .filter((a) => a.platform !== "faceit")
            .map((a) => (
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

      <div className="card" style={{ marginBottom: "1.5rem" }}>
        <h2 style={{ fontSize: "1.1rem", marginBottom: "1rem" }}>
          Match History
          {player.match_count > 0 && (
            <span style={{ color: "var(--muted)", fontWeight: 400, fontSize: "0.9rem", marginLeft: "0.5rem" }}>
              ({player.match_count} in database
              {matches.length < player.match_count ? `, showing ${matches.length}` : ""})
            </span>
          )}
        </h2>
        {matches.length === 0 ? (
          <p style={{ color: "var(--muted)", fontSize: "0.9rem" }}>No matches indexed for this player yet.</p>
        ) : (
          <table>
            <thead>
              <tr>
                <th>Match</th>
                <th>Map</th>
                <th>Score</th>
                <th>K</th>
                <th>A</th>
                <th>D</th>
                <th>HS%</th>
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
                  <td>{formatMatchScore(m.source, m.score_team_a, m.score_team_b)}</td>
                  <td>{m.kills ?? "—"}</td>
                  <td>{m.assists ?? "—"}</td>
                  <td>{m.deaths ?? "—"}</td>
                  <td>{m.headshot_pct != null ? `${Math.round(m.headshot_pct)}%` : "—"}</td>
                  <td style={{ color: "var(--muted)", fontSize: "0.85rem" }}>{formatDate(m.played_at)}</td>
                    <td>
                      <MatchSourceBadge source={m.source} />
                    </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>

      <div className="card">
        <h2 style={{ fontSize: "1.1rem", marginBottom: "0.5rem" }}>First / Last Seen</h2>
        <p style={{ color: "var(--muted)", fontSize: "0.9rem" }}>
          First: {formatDate(player.first_seen_at)} · Last: {formatDate(player.last_seen_at)}
        </p>
      </div>
    </div>
  );
}
