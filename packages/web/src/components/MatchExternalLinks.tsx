import { ProviderIcon } from "@/components/ProviderIcon";

export function MatchExternalLinks({
  leetifyUrl,
  faceitUrl,
}: {
  leetifyUrl?: string | null;
  faceitUrl?: string | null;
}) {
  if (!leetifyUrl && !faceitUrl) {
    return null;
  }

  return (
    <div className="match-external-links">
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
