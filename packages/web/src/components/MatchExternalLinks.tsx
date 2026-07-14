import { ProviderIcon } from "@/components/ProviderIcon";

export function MatchExternalLinks({
  leetifyUrl,
  faceitUrl,
  csstatsUrl,
}: {
  leetifyUrl?: string | null;
  faceitUrl?: string | null;
  csstatsUrl?: string | null;
}) {
  if (!leetifyUrl && !faceitUrl && !csstatsUrl) {
    return null;
  }

  return (
    <div className="match-external-links">
      {csstatsUrl && (
        <a
          href={csstatsUrl}
          target="_blank"
          rel="noopener noreferrer"
          className="profile-link is-available profile-link-csstats"
          title="Open this match on csstats.gg"
        >
          <ProviderIcon provider="csstats" size={18} />
          <span className="profile-link-label">csstats match</span>
        </a>
      )}
      {leetifyUrl && (
        <a
          href={leetifyUrl}
          target="_blank"
          rel="noopener noreferrer"
          className="profile-link is-available profile-link-leetify"
          title="Open this match on Leetify"
        >
          <ProviderIcon provider="leetify" size={18} />
          <span className="profile-link-label">Leetify match</span>
        </a>
      )}
      {faceitUrl && (
        <a
          href={faceitUrl}
          target="_blank"
          rel="noopener noreferrer"
          className="profile-link is-available profile-link-faceit"
          title="Open this match on FACEIT"
        >
          <ProviderIcon provider="faceit" size={18} />
          <span className="profile-link-label">FACEIT match</span>
        </a>
      )}
    </div>
  );
}
