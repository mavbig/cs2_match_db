import { FaceitActivity, FaceitActivityTimeline } from "@/components/FaceitActivityTimeline";
import { countryFlag, countryLabel } from "@/lib/country";

export interface FaceitStatBlock {
  matches?: number | null;
  win_rate_pct?: number | null;
  kd?: number | null;
  kr?: number | null;
  adr?: number | null;
  hs_pct?: number | null;
  avg_kills?: number | null;
  avg_deaths?: number | null;
  avg_assists?: number | null;
  entry_success_pct?: number | null;
  kast_pct?: number | null;
  match_count?: number;
}

export interface FaceitBan {
  type?: string | null;
  reason?: string | null;
  game?: string | null;
  starts_at?: string | null;
  ends_at?: string | null;
}

export interface FaceitFlag {
  severity: "high" | "medium" | "low";
  label: string;
  detail: string;
}

export interface FaceitProfileStats {
  player_id?: string;
  nickname?: string;
  profile_url?: string;
  verified?: boolean;
  country?: string;
  elo?: number;
  skill_level?: number;
  lifetime?: FaceitStatBlock;
  recent_20?: FaceitStatBlock;
  activity?: FaceitActivity;
  bans?: FaceitBan[];
  flags?: FaceitFlag[];
}

function formatStat(value: number | null | undefined, suffix = ""): string {
  if (value == null) return "—";
  const rounded = Number.isInteger(value) ? String(value) : value.toFixed(2);
  return `${rounded}${suffix}`;
}

function StatGrid({ title, stats, matchLabel }: { title: string; stats: FaceitStatBlock; matchLabel?: string }) {
  const count = stats.match_count ?? stats.matches;
  return (
    <div>
      <h3 style={{ fontSize: "0.95rem", marginBottom: "0.75rem", color: "var(--muted)" }}>
        {title}
        {count != null && (
          <span style={{ fontWeight: 400, marginLeft: "0.5rem" }}>
            ({count} {matchLabel ?? "matches"})
          </span>
        )}
      </h3>
      <div className="stat-grid">
        <div className="stat-box">
          <div className="value">{formatStat(stats.kd)}</div>
          <div className="label">K/D</div>
        </div>
        <div className="stat-box">
          <div className="value">{formatStat(stats.kr)}</div>
          <div className="label">K/R</div>
        </div>
        <div className="stat-box">
          <div className="value">{formatStat(stats.adr)}</div>
          <div className="label">ADR</div>
        </div>
        <div className="stat-box">
          <div className="value">{formatStat(stats.hs_pct, "%")}</div>
          <div className="label">Headshot %</div>
        </div>
        {stats.win_rate_pct != null && (
          <div className="stat-box">
            <div className="value">{formatStat(stats.win_rate_pct, "%")}</div>
            <div className="label">Win rate</div>
          </div>
        )}
        <div className="stat-box">
          <div className="value">{formatStat(stats.avg_kills)}</div>
          <div className="label">Avg kills</div>
        </div>
        <div className="stat-box">
          <div className="value">{formatStat(stats.avg_deaths)}</div>
          <div className="label">Avg deaths</div>
        </div>
        <div className="stat-box">
          <div className="value">{formatStat(stats.avg_assists)}</div>
          <div className="label">Avg assists</div>
        </div>
        {stats.entry_success_pct != null && (
          <div className="stat-box">
            <div className="value">{formatStat(stats.entry_success_pct, "%")}</div>
            <div className="label">Entry success</div>
          </div>
        )}
        {stats.kast_pct != null && (
          <div className="stat-box">
            <div className="value">{formatStat(stats.kast_pct, "%")}</div>
            <div className="label">KAST</div>
          </div>
        )}
      </div>
    </div>
  );
}

export function FaceitProfile({ faceit }: { faceit: FaceitProfileStats }) {
  const lifetime = faceit.lifetime ?? {};
  const recent = faceit.recent_20 ?? {};
  const bans = faceit.bans ?? [];
  const flags = faceit.flags ?? [];
  const hasLifetime = Object.values(lifetime).some((v) => v != null);
  const hasRecent = (recent.match_count ?? 0) > 0;

  return (
    <div className="card" style={{ marginBottom: "1.5rem" }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", gap: "1rem", marginBottom: "1rem" }}>
        <div>
          <h2 style={{ fontSize: "1.1rem" }}>FACEIT CS2</h2>
          <div style={{ display: "flex", gap: "0.5rem", marginTop: "0.35rem", flexWrap: "wrap" }}>
            {faceit.profile_url ? (
              <a href={faceit.profile_url} target="_blank" rel="noopener noreferrer">
                {faceit.nickname ?? "Profile"}
              </a>
            ) : (
              faceit.nickname
            )}
            {faceit.skill_level != null && <span className="badge badge-blue">Lvl {faceit.skill_level}</span>}
            {faceit.elo != null && <span className="badge">{faceit.elo} ELO</span>}
            {faceit.verified && <span className="badge badge-green">Verified</span>}
            {faceit.country && (
              <span className="badge" title={countryLabel(faceit.country)}>
                {countryFlag(faceit.country)} {countryLabel(faceit.country)}
              </span>
            )}
          </div>
        </div>
      </div>

      {bans.length > 0 && (
        <div
          style={{
            background: "rgba(248, 81, 73, 0.1)",
            border: "1px solid var(--danger)",
            borderRadius: 8,
            padding: "0.75rem 1rem",
            marginBottom: "1rem",
          }}
        >
          <strong style={{ color: "var(--danger)" }}>Ban history</strong>
          <ul style={{ marginTop: "0.5rem", paddingLeft: "1.25rem", fontSize: "0.9rem" }}>
            {bans.map((ban, i) => (
              <li key={i} style={{ marginBottom: "0.25rem" }}>
                {ban.reason ?? "No reason given"}
                {ban.type && ` (${ban.type})`}
                {ban.starts_at && ` · ${new Date(ban.starts_at).toLocaleDateString()}`}
                {ban.ends_at && ` → ${new Date(ban.ends_at).toLocaleDateString()}`}
              </li>
            ))}
          </ul>
        </div>
      )}

      {flags.length > 0 && (
        <div style={{ marginBottom: "1rem" }}>
          <h3 style={{ fontSize: "0.95rem", marginBottom: "0.5rem", color: "var(--muted)" }}>
            Unusual patterns
            <span style={{ fontWeight: 400, marginLeft: "0.5rem", fontSize: "0.8rem" }}>
              (heuristic — not proof of cheating)
            </span>
          </h3>
          <div style={{ display: "flex", flexDirection: "column", gap: "0.5rem" }}>
            {flags.map((flag, i) => (
              <div
                key={i}
                style={{
                  padding: "0.6rem 0.85rem",
                  borderRadius: 8,
                  border: "1px solid var(--border)",
                  background:
                    flag.severity === "high"
                      ? "rgba(248, 81, 73, 0.08)"
                      : flag.severity === "medium"
                        ? "rgba(210, 153, 34, 0.08)"
                        : "var(--surface2)",
                }}
              >
                <span
                  className={
                    flag.severity === "high"
                      ? "badge badge-red"
                      : flag.severity === "medium"
                        ? "badge badge-yellow"
                        : "badge"
                  }
                  style={{ marginRight: "0.5rem" }}
                >
                  {flag.severity}
                </span>
                <strong>{flag.label}</strong>
                <span style={{ color: "var(--muted)", marginLeft: "0.5rem", fontSize: "0.85rem" }}>
                  {flag.detail}
                </span>
              </div>
            ))}
          </div>
        </div>
      )}

      {faceit.activity && (
        <div style={{ marginBottom: "1.25rem" }}>
          <h3 style={{ fontSize: "0.95rem", marginBottom: "0.75rem", color: "var(--muted)" }}>
            Activity timeline
          </h3>
          <FaceitActivityTimeline activity={faceit.activity} />
        </div>
      )}

      {(hasLifetime || hasRecent) && (
        <div className="grid-2">
          {hasLifetime && <StatGrid title="Lifetime" stats={lifetime} />}
          {hasRecent && <StatGrid title="Last 20 matches" stats={recent} matchLabel="games sampled" />}
        </div>
      )}

      {!hasLifetime && !hasRecent && bans.length === 0 && (
        <p style={{ color: "var(--muted)", fontSize: "0.9rem" }}>
          Linked FACEIT account — run enrichment to refresh detailed stats.
        </p>
      )}
    </div>
  );
}
