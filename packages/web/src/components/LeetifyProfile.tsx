import { MatchSourceBadge } from "@/components/MatchSourceBadge";

export interface LeetifyRanks {
  faceit?: number | null;
  faceit_elo?: number | null;
  leetify?: number | null;
  premier?: number | null;
  wingman?: number | null;
  renown?: number | null;
}

export interface LeetifyProfileStats {
  name?: string;
  total_matches?: number;
  winrate?: number;
  rating?: Record<string, number>;
  ranks?: LeetifyRanks;
  stats?: Record<string, number>;
}

const PERCENTILE_RATINGS = new Set(["aim", "utility", "positioning"]);
const DELTA_RATINGS = new Set(["clutch", "opening", "t_leetify", "ct_leetify"]);

const RATING_LABELS: Record<string, string> = {
  aim: "Aim",
  clutch: "Clutch",
  opening: "Opening",
  utility: "Utility",
  t_leetify: "T side",
  ct_leetify: "CT side",
  positioning: "Positioning",
};

const RATING_ORDER = ["aim", "positioning", "utility", "opening", "clutch", "t_leetify", "ct_leetify"];

function formatLeetifyRating(key: string, value: number): string {
  if (PERCENTILE_RATINGS.has(key)) {
    return String(Math.round(value));
  }
  if (DELTA_RATINGS.has(key) || Math.abs(value) < 2) {
    const sign = value > 0 ? "+" : "";
    return `${sign}${value.toFixed(2)}`;
  }
  return String(Math.round(value));
}

function ratingLabel(key: string): string {
  return RATING_LABELS[key] ?? key.replace(/_/g, " ");
}

export function LeetifyProfile({ leetify }: { leetify: LeetifyProfileStats }) {
  const rating = leetify.rating ?? {};
  const ranks = leetify.ranks ?? {};
  const ratingEntries = RATING_ORDER.filter((key) => rating[key] != null).map((key) => [
    key,
    rating[key] as number,
  ]);
  const extraRatingEntries = Object.entries(rating).filter(([key]) => !RATING_ORDER.includes(key));

  return (
    <div className="card" style={{ marginBottom: "1.5rem" }}>
      <h2 style={{ fontSize: "1.1rem", marginBottom: "1rem", display: "flex", alignItems: "center", gap: "0.5rem" }}>
        <MatchSourceBadge source="leetify" size={22} />
        Leetify
      </h2>

      <div className="stat-grid" style={{ marginBottom: "1rem" }}>
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
        {ranks.leetify != null && (
          <div className="stat-box">
            <div className="value">{formatLeetifyRating("leetify", ranks.leetify)}</div>
            <div className="label">Leetify rating</div>
          </div>
        )}
        {ranks.premier != null && (
          <div className="stat-box">
            <div className="value">{ranks.premier.toLocaleString()}</div>
            <div className="label">Premier</div>
          </div>
        )}
        {ranks.faceit_elo != null && (
          <div className="stat-box">
            <div className="value">{ranks.faceit_elo}</div>
            <div className="label">FACEIT ELO</div>
          </div>
        )}
        {ranks.faceit != null && (
          <div className="stat-box">
            <div className="value">{ranks.faceit}</div>
            <div className="label">FACEIT level</div>
          </div>
        )}
      </div>

      {(ratingEntries.length > 0 || extraRatingEntries.length > 0) && (
        <div>
          <h3 style={{ fontSize: "0.95rem", marginBottom: "0.75rem", color: "var(--muted)" }}>
            Rating breakdown
            <span style={{ fontWeight: 400, marginLeft: "0.5rem", fontSize: "0.8rem" }}>
              (percentiles 0–100; clutch/opening/side ratings as ± deltas)
            </span>
          </h3>
          <div className="stat-grid">
            {[...ratingEntries, ...extraRatingEntries].map(([key, val]) => (
              <div key={key} className="stat-box">
                <div className="value">{formatLeetifyRating(key, val)}</div>
                <div className="label">{ratingLabel(key)}</div>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
