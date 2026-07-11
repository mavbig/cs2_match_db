export interface SyncConfig {
  mySteam64Id: string;
  steamAuthCode: string;
  oldestShareCode: string;
  steamApiKey: string;
}

export interface NormalizedPlayer {
  steam64_id: string;
  name: string | null;
  team: string | null;
  kills: number | null;
  deaths: number | null;
  assists: number | null;
  mvps: number | null;
  headshot_pct: number | null;
  score: number | null;
  ping: number | null;
  is_me: boolean;
}

export interface NormalizedMatch {
  source: string;
  source_match_id: string;
  map: string | null;
  mode: string | null;
  played_at: string | null;
  score_team_a: number | null;
  score_team_b: number | null;
  duration_seconds: number | null;
  share_code: string | null;
  raw_payload: Record<string, unknown> | null;
  players: NormalizedPlayer[];
}

export function accountIdToSteam64(accountId: number): string {
  return (BigInt(accountId) + 76561197960265728n).toString();
}

export function parseShareCode(shareCode: string): {
  matchId: bigint;
  outcomeId: bigint;
  token: number;
} | null {
  const cleaned = shareCode.replace(/^CSGO-?/i, "").replace(/-/g, "");
  if (cleaned.length < 25) return null;

  try {
    const chars = "ABCDEFGHJKLMNOPQRSTUVWXYZabcdefhijkmnopqrstuvwxyz23456789";
    let bits = "";
    for (const c of cleaned.toUpperCase()) {
      const idx = chars.indexOf(c);
      if (idx === -1) continue;
      bits += idx.toString(2).padStart(6, "0");
    }
    const matchId = BigInt("0b" + bits.slice(0, 64));
    const outcomeId = BigInt("0b" + bits.slice(64, 128));
    const token = parseInt(bits.slice(128, 160), 2);
    return { matchId, outcomeId, token };
  } catch {
    return null;
  }
}

export function normalizeGcMatch(
  match: Record<string, unknown>,
  shareCode: string | null,
  mySteam64Id: string
): NormalizedMatch | null {
  const matchId = String(match.matchid ?? match.match_id ?? "");
  if (!matchId) return null;

  const roundstats = (match.roundstatsall as Record<string, unknown>[])?.[0]
    ?? (match.roundstats_legacy as Record<string, unknown>)
    ?? {};

  const reservation = roundstats.reservation as Record<string, unknown> | undefined;
  const accountIds = (reservation?.account_ids as number[]) ?? [];

  const map = String(roundstats.map ?? match.game_map ?? "").replace(/.*\//, "").replace(".dem.bz2", "");
  const matchTime = Number(match.matchtime ?? roundstats.match_time ?? 0);
  const playedAt = matchTime ? new Date(matchTime * 1000).toISOString() : null;

  const kills = (roundstats.kills as number[]) ?? [];
  const deaths = (roundstats.deaths as number[]) ?? [];
  const assists = (roundstats.assists as number[]) ?? [];
  const scores = (roundstats.scores as number[]) ?? [];
  const mvps = (roundstats.mvps as number[]) ?? [];
  const pings = (roundstats.pings as number[]) ?? [];
  const hsp = (roundstats.enemy_headshots as number[]) ?? [];

  const teamScores = (roundstats.team_scores as number[]) ?? [];
  const scoreA = teamScores[0] ?? null;
  const scoreB = teamScores[1] ?? null;

  const playerNames = (match.player_names as string[]) ?? [];

  const players: NormalizedPlayer[] = accountIds.map((accountId, i) => {
    const steam64 = accountIdToSteam64(accountId);
    const team = i < accountIds.length / 2 ? "team_a" : "team_b";
    return {
      steam64_id: steam64,
      name: playerNames[i] ?? null,
      team,
      kills: kills[i] ?? null,
      deaths: deaths[i] ?? null,
      assists: assists[i] ?? null,
      mvps: mvps[i] ?? null,
      headshot_pct: hsp[i] != null ? hsp[i] : null,
      score: scores[i] ?? null,
      ping: pings[i] ?? null,
      is_me: steam64 === mySteam64Id,
    };
  });

  return {
    source: "steam_gc",
    source_match_id: matchId,
    map: map || null,
    mode: "premier",
    played_at: playedAt,
    score_team_a: scoreA,
    score_team_b: scoreB,
    duration_seconds: null,
    share_code: shareCode,
    raw_payload: match as Record<string, unknown>,
    players,
  };
}
