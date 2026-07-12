// Browser calls same-origin /api/v1/* (proxied by Next.js to the FastAPI backend).
// Server components use API_INTERNAL_URL inside Docker.
const API_URL =
  typeof window === "undefined"
    ? process.env.API_INTERNAL_URL ?? process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000"
    : "";

async function fetchApi<T>(path: string, options?: RequestInit): Promise<T> {
  let resp: Response;
  try {
    resp = await fetch(`${API_URL}${path}`, {
      ...options,
      headers: {
        "Content-Type": "application/json",
        ...options?.headers,
      },
      cache: "no-store",
    });
  } catch {
    throw new Error(
      "Could not reach the API. If you access the site remotely, rebuild the web container after pulling the latest code."
    );
  }
  if (!resp.ok) {
    const text = await resp.text();
    throw new Error(text || `API error ${resp.status}`);
  }
  return resp.json();
}

export interface Player {
  id: string;
  steam64_id: string;
  current_name: string | null;
  avatar_url: string | null;
  profile_url: string | null;
  first_seen_at: string;
  last_seen_at: string;
}

export interface MatchGcDebug {
  match_id: string;
  source_match_id: string;
  share_code: string | null;
  stored: {
    map: string | null;
    mode: string | null;
    score_team_a: number | null;
    score_team_b: number | null;
    played_at: string | null;
    duration_seconds: number | null;
  };
  parse_hints: Record<string, unknown> | null;
  raw_payload: Record<string, unknown> | null;
}

export interface MatchCount {
  total: number;
}

export interface MatchSummary {
  id: string;
  source: string;
  source_match_id: string;
  map: string | null;
  mode: string | null;
  played_at: string | null;
  score_team_a: number | null;
  score_team_b: number | null;
  player_count: number;
}

export interface PlayerMatch extends MatchSummary {
  kills: number | null;
  deaths: number | null;
  assists: number | null;
  mvps: number | null;
  headshot_pct: number | null;
  score: number | null;
}

export interface MatchPlayer {
  player_id: string;
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

export interface MatchDetail extends MatchSummary {
  source_match_id: string;
  duration_seconds: number | null;
  share_code: string | null;
  players: MatchPlayer[];
}

export interface PlayerDetail extends Player {
  name_history: string[];
  platform_accounts: { platform: string; external_id: string; nickname: string | null; profile_url: string | null }[];
  latest_stats: Record<string, unknown>;
  match_count: number;
  times_played_with_me: number | null;
}

export interface PlayedWith {
  player: Player;
  times_together: number;
  first_together: string | null;
  last_together: string | null;
  shared_matches: MatchSummary[];
}

export interface SyncStatus {
  last_steam_sync: string | null;
  last_faceit_sync: string | null;
  total_matches: number;
  total_players: number;
  pending_jobs: number;
  steam_configured: boolean;
  faceit_configured: boolean;
}

export interface Dashboard {
  recent_matches: MatchSummary[];
  top_teammates: PlayedWith[];
  sync_status: SyncStatus;
}

export interface Settings {
  my_steam64_id: string | null;
  steam_auth_code_set: boolean;
  steam_oldest_share_code_set: boolean;
  steam_api_key_set: boolean;
  faceit_api_key_set: boolean;
  faceit_nickname: string | null;
  leetify_api_key_set: boolean;
  onboarding_complete: boolean;
}

export const api = {
  health: () => fetchApi<{ status: string }>("/api/v1/health"),
  dashboard: () => fetchApi<Dashboard>("/api/v1/dashboard"),
  matches: (limit = 20, offset = 0) => fetchApi<MatchSummary[]>(`/api/v1/matches?limit=${limit}&offset=${offset}`),
  matchCount: () => fetchApi<MatchCount>("/api/v1/matches/count"),
  match: (id: string) => fetchApi<MatchDetail>(`/api/v1/matches/${id}`),
  matchGcDebug: (id: string) => fetchApi<MatchGcDebug>(`/api/v1/matches/${id}/gc-debug`),
  searchPlayers: (q: string) => fetchApi<{ players: Player[] }>(`/api/v1/players?q=${encodeURIComponent(q)}`),
  player: (id: string) => fetchApi<PlayerDetail>(`/api/v1/players/${id}`),
  playerMatches: (id: string, limit = 100, offset = 0) =>
    fetchApi<PlayerMatch[]>(`/api/v1/players/${id}/matches?limit=${limit}&offset=${offset}`),
  playedWith: (steam64: string) => fetchApi<PlayedWith>(`/api/v1/players/by-steam/${steam64}/played-with`),
  lookupPlayer: (steam_url_or_id: string) =>
    fetchApi<PlayerDetail>("/api/v1/players/lookup", {
      method: "POST",
      body: JSON.stringify({ steam_url_or_id }),
    }),
  settings: () => fetchApi<Settings>("/api/v1/settings"),
  updateSettings: (data: Record<string, string>) =>
    fetchApi<Settings>("/api/v1/settings", { method: "PUT", body: JSON.stringify(data) }),
  triggerSync: (jobType: string) =>
    fetchApi<{ id: string; job_type: string; status: string }>(`/api/v1/sync/trigger/${jobType}`, { method: "POST" }),
  importShareCode: (share_code: string) =>
    fetchApi<{ job_id: string; message: string }>("/api/v1/import/share-code", {
      method: "POST",
      body: JSON.stringify({ share_code }),
    }),
};

export function formatDate(iso: string | null): string {
  if (!iso) return "—";
  return new Date(iso).toLocaleString();
}

export function formatMap(map: string | null): string {
  if (!map) return "Unknown";
  return map.replace(/^de_/, "").replace(/_/g, " ");
}

export function formatMatchLabel(match: Pick<MatchSummary, "source_match_id" | "id" | "played_at">): string {
  if (match.source_match_id) {
    return `#${match.source_match_id.slice(-8)}`;
  }
  if (match.played_at) {
    return new Date(match.played_at).toLocaleString(undefined, {
      month: "short",
      day: "numeric",
      hour: "2-digit",
      minute: "2-digit",
    });
  }
  return match.id.slice(0, 8);
}

export function formatMatchScore(
  source: string,
  scoreA: number | null,
  scoreB: number | null
): string {
  if (scoreA == null || scoreB == null) return "? : ?";
  if (source === "steam_gc" && scoreA <= 1 && scoreB <= 1 && scoreA + scoreB === 1) {
    return scoreA > scoreB ? "Win" : "Loss";
  }
  return `${scoreA} : ${scoreB}`;
}
