import { formatDate, formatRelativeTime } from "@/lib/api";

export interface FaceitActivityMatch {
  match_id: string;
  finished_at: string;
  result?: "win" | "loss" | null;
}

export interface FaceitActivityMonth {
  month: string;
  label: string;
  count: number;
  height_pct?: number;
}

export interface FaceitActivity {
  matches: FaceitActivityMatch[];
  last_played_at: string | null;
  days_since_last: number | null;
  months: FaceitActivityMonth[];
  stale_warning?: string | null;
  sample_size?: number;
}

export function FaceitActivityTimeline({ activity }: { activity: FaceitActivity }) {
  const hasDots = activity.matches.length > 0;
  const hasMonths = activity.months.some((m) => m.count > 0);

  if (!hasDots && !hasMonths) {
    return (
      <p style={{ color: "var(--muted)", fontSize: "0.9rem", margin: 0 }}>
        No recent FACEIT match history yet. Click Sync profile above, then hard-refresh this page.
      </p>
    );
  }

  return (
    <div className="faceit-activity">
      {activity.last_played_at && (
        <div className="faceit-activity-summary">
          <strong>Last FACEIT game:</strong>{" "}
          {formatRelativeTime(activity.last_played_at)} ({formatDate(activity.last_played_at)})
          {activity.sample_size != null && activity.sample_size > 0 && (
            <span style={{ color: "var(--muted)", marginLeft: "0.5rem" }}>
              · {activity.sample_size} games sampled
            </span>
          )}
        </div>
      )}

      {activity.stale_warning && (
        <div className="faceit-activity-warning">{activity.stale_warning}</div>
      )}

      {hasDots && (
        <div>
          <div className="faceit-activity-label">Recent games (newest →)</div>
          <div className="faceit-activity-dots" aria-label="Recent FACEIT match results">
            {activity.matches.map((match) => (
              <span
                key={match.match_id}
                className={`faceit-activity-dot${match.result ? ` is-${match.result}` : ""}`}
                title={`${match.result === "win" ? "Win" : match.result === "loss" ? "Loss" : "Game"} · ${formatDate(match.finished_at)}`}
              />
            ))}
          </div>
        </div>
      )}

      {hasMonths && (
        <div>
          <div className="faceit-activity-label">Games per month</div>
          <div className="faceit-activity-chart">
            {activity.months.map((month) => (
              <div key={month.month} className="faceit-activity-bar-col" title={`${month.label}: ${month.count} games`}>
                <div className="faceit-activity-bar-track">
                  <div
                    className="faceit-activity-bar-fill"
                    style={{ height: `${month.height_pct ?? (month.count > 0 ? 20 : 0)}%` }}
                  />
                </div>
                <span className="faceit-activity-bar-label">{month.label}</span>
                {month.count > 0 && <span className="faceit-activity-bar-count">{month.count}</span>}
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
