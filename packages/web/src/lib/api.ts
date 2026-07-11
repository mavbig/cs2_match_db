const API_URL =
  typeof window === "undefined"
    ? process.env.API_INTERNAL_URL ?? process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000"
    : process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

async function fetchApi<T>(path: string, options?: RequestInit): Promise<T> {
  const resp = await fetch(`${API_URL}${path}`, {
    ...options,
    headers: {
      "Content-Type": "application/json",
      ...options?.headers,
    },
    cache: "no-store",
  });
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

export interface MatchSummary {
  id: string;
  source: string;
  map: string | null;
  mode: string | null;
  played_at: string | null;
  score_team_a: number | null;
  score_team_b: number | null;
  player_count: number;
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
  match: (id: string) => fetchApi<MatchDetail>(`/api/v1/matches/${id}`),
  searchPlayers: (q: string) => fetchApi<{ players: Player[] }>(`/api/v1/players?q=${encodeURIComponent(q)}`),
  player: (id: string) => fetchApi<PlayerDetail>(`/api/v1/players/${id}`),
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
