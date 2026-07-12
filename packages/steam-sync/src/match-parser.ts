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

export interface GcParseDebug {
  roundstats_count: number;
  picked_round: number | null;
  picked_team_scores: number[];
  game_type: number | null;
  rank_type_ids: number[];
  watchable_game_map: string | null;
  final_map_field: string | null;
  final_map_id: number | null;
}

/** CS2 GC map_id → map name (active duty pool + common reserves). */
const CS2_MAP_IDS: Record<number, string> = {
  1: "de_dust2",
  2: "de_mirage",
  3: "de_inferno",
  4: "de_nuke",
  5: "de_overpass",
  6: "de_cache",
  7: "de_train",
  8: "de_cbble",
  9: "de_vertigo",
  10: "de_ancient",
  11: "de_anubis",
  12: "de_dust",
  13: "de_aztec",
  14: "de_tuscan",
  15: "de_mills",
  16: "de_thera",
  17: "de_grail",
  18: "de_jura",
  19: "de_boyard",
  20: "de_chalice",
  21: "de_basalt",
  22: "de_edin",
  23: "de_palacio",
  24: "de_assembly",
  25: "de_memento",
};

const PREMIER_GAME_TYPE = 1048584; // 0x100008
const PREMIER_RANK_TYPE_ID = 11;

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

function asRecord(value: unknown): Record<string, unknown> | undefined {
  return value && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : undefined;
}

function teamScoreTotal(entry: Record<string, unknown>): number {
  const scores = (entry.team_scores as number[]) ?? [];
  if (scores.length < 2) return 0;
  return scores[0] + scores[1];
}

function pickFinalRoundStats(match: Record<string, unknown>): Record<string, unknown> {
  const all = (match.roundstatsall as Record<string, unknown>[]) ?? [];
  if (all.length) {
    const withMeaningfulScore = all.filter((entry) => teamScoreTotal(entry) >= 8);
    const pool = withMeaningfulScore.length ? withMeaningfulScore : all;
    const sorted = [...pool].sort((a, b) => Number(b.round ?? 0) - Number(a.round ?? 0));
    return sorted[0] ?? all[all.length - 1];
  }

  return asRecord(match.roundstats_legacy) ?? {};
}

function extractMapName(
  match: Record<string, unknown>,
  roundstats: Record<string, unknown>,
  allRoundStats: Record<string, unknown>[]
): string | null {
  const watchable = asRecord(match.watchablematchinfo);
  for (const candidate of [watchable?.game_map, watchable?.game_mapgroup]) {
    const map = normalizeMapToken(String(candidate ?? ""));
    if (map) return map;
  }

  for (const entry of [...allRoundStats].reverse()) {
    const map = normalizeMapToken(String(entry.map ?? ""));
    if (map) return map;

    const mapId = Number(entry.map_id ?? 0);
    if (mapId && CS2_MAP_IDS[mapId]) {
      return CS2_MAP_IDS[mapId];
    }
  }

  const map = normalizeMapToken(String(roundstats.map ?? ""));
  if (map) return map;

  const mapId = Number(roundstats.map_id ?? 0);
  if (mapId && CS2_MAP_IDS[mapId]) {
    return CS2_MAP_IDS[mapId];
  }

  return null;
}

function normalizeMapToken(raw: string): string | null {
  if (!raw) return null;

  const deMatch = raw.match(/de_[a-z0-9_]+/i);
  if (deMatch) {
    return deMatch[0].toLowerCase();
  }

  const cleaned = raw.replace(/.*\//, "").replace(".dem.bz2", "").replace(".dem", "");
  if (cleaned.startsWith("de_")) {
    return cleaned.toLowerCase();
  }

  return null;
}

function inferMode(
  reservation: Record<string, unknown> | undefined,
  watchable: Record<string, unknown> | undefined
): string {
  const rankings = (reservation?.rankings as Record<string, unknown>[]) ?? [];
  const rankTypeIds = rankings.map((r) => Number(r.rank_type_id ?? 0)).filter(Boolean);
  const gameType = Number(reservation?.game_type ?? watchable?.game_type ?? 0);

  if (
    rankTypeIds.includes(PREMIER_RANK_TYPE_ID) ||
    rankings.some((r) => typeof r.leaderboard_name === "string" && r.leaderboard_name) ||
    gameType === PREMIER_GAME_TYPE ||
    (gameType & 0x100000) !== 0
  ) {
    return "premier";
  }

  // CS2 GC often omits premier bits on older payloads; default to premier for MM sync.
  return "premier";
}

function buildNameLookup(reservation: Record<string, unknown> | undefined): Map<number, string> {
  const lookup = new Map<number, string>();
  const rankings = (reservation?.rankings as Record<string, unknown>[]) ?? [];
  for (const ranking of rankings) {
    const accountId = Number(ranking.account_id ?? 0);
    const name = ranking.leaderboard_name ?? ranking.leaderboardName;
    if (accountId && typeof name === "string" && name.trim()) {
      lookup.set(accountId, name.trim());
    }
  }
  return lookup;
}

export function buildGcParseDebug(match: Record<string, unknown>): GcParseDebug {
  const allRoundStats = (match.roundstatsall as Record<string, unknown>[]) ?? [];
  const roundstats = pickFinalRoundStats(match);
  const reservation = asRecord(roundstats.reservation);
  const watchable = asRecord(match.watchablematchinfo);
  const rankings = (reservation?.rankings as Record<string, unknown>[]) ?? [];

  return {
    roundstats_count: allRoundStats.length,
    picked_round: roundstats.round != null ? Number(roundstats.round) : null,
    picked_team_scores: (roundstats.team_scores as number[]) ?? [],
    game_type: reservation?.game_type != null ? Number(reservation.game_type) : null,
    rank_type_ids: rankings.map((r) => Number(r.rank_type_id ?? 0)).filter(Boolean),
    watchable_game_map: watchable?.game_map != null ? String(watchable.game_map) : null,
    final_map_field: roundstats.map != null ? String(roundstats.map) : null,
    final_map_id: roundstats.map_id != null ? Number(roundstats.map_id) : null,
  };
}

export function normalizeGcMatch(
  match: Record<string, unknown>,
  shareCode: string | null,
  mySteam64Id: string
): NormalizedMatch | null {
  const matchId = String(match.matchid ?? match.match_id ?? "");
  if (!matchId) return null;

  const allRoundStats = (match.roundstatsall as Record<string, unknown>[]) ?? [];
  const roundstats = pickFinalRoundStats(match);
  const reservation = asRecord(roundstats.reservation);
  const watchable = asRecord(match.watchablematchinfo);
  const accountIds = (reservation?.account_ids as number[]) ?? [];
  const nameLookup = buildNameLookup(reservation);

  const map = extractMapName(match, roundstats, allRoundStats);
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
  const durationSeconds = Number(roundstats.match_duration ?? 0) || null;

  const players: NormalizedPlayer[] = accountIds.map((accountId, i) => {
    const steam64 = accountIdToSteam64(accountId);
    const team = i < accountIds.length / 2 ? "team_a" : "team_b";
    return {
      steam64_id: steam64,
      name: nameLookup.get(accountId) ?? null,
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
    map,
    mode: inferMode(reservation, watchable),
    played_at: playedAt,
    score_team_a: scoreA,
    score_team_b: scoreB,
    duration_seconds: durationSeconds,
    share_code: shareCode,
    raw_payload: match as Record<string, unknown>,
    players,
  };
}
