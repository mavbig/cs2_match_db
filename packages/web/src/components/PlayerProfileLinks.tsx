import { FaceitIcon, LeetifyIcon, SteamIcon } from "@/components/ProviderIcons";

export interface PlayerProfileLinkData {
  steam64Id: string;
  steamProfileUrl?: string | null;
  faceit?: {
    profileUrl: string;
    elo?: number | null;
    nickname?: string | null;
  } | null;
  leetifyAvailable?: boolean;
}

function steamUrl(steam64Id: string, stored?: string | null): string {
  if (stored && stored.startsWith("http")) {
    return stored;
  }
  return `https://steamcommunity.com/profiles/${steam64Id}`;
}

function leetifyUrl(steam64Id: string): string {
  return `https://leetify.com/app/profile/${steam64Id}`;
}

export function PlayerProfileLinks({
  steam64Id,
  steamProfileUrl,
  faceit,
  leetifyAvailable,
}: PlayerProfileLinkData) {
  const steamHref = steamUrl(steam64Id, steamProfileUrl);
  const hasFaceit = Boolean(faceit?.profileUrl);
  const hasLeetify = Boolean(leetifyAvailable);

  return (
    <div className="profile-links">
      <a
        href={steamHref}
        target="_blank"
        rel="noopener noreferrer"
        className="profile-link is-available"
        title="Steam profile"
      >
        <SteamIcon size={20} />
        <span className="profile-link-label">Steam</span>
      </a>

      {hasFaceit ? (
        <a
          href={faceit!.profileUrl}
          target="_blank"
          rel="noopener noreferrer"
          className="profile-link is-available profile-link-faceit"
          title={faceit!.elo != null ? `FACEIT — ${faceit!.elo} ELO` : "FACEIT profile"}
        >
          <FaceitIcon size={20} />
          <span className="profile-link-label">
            FACEIT{faceit!.elo != null ? ` · ${faceit!.elo}` : ""}
          </span>
        </a>
      ) : (
        <span className="profile-link is-unavailable" title="No FACEIT profile linked">
          <FaceitIcon size={20} />
          <span className="profile-link-label">FACEIT</span>
        </span>
      )}

      {hasLeetify ? (
        <a
          href={leetifyUrl(steam64Id)}
          target="_blank"
          rel="noopener noreferrer"
          className="profile-link is-available profile-link-leetify"
          title="View on Leetify"
        >
          <LeetifyIcon size={20} />
          <span className="profile-link-label">Leetify</span>
        </a>
      ) : (
        <span className="profile-link is-unavailable" title="No Leetify data for this player">
          <LeetifyIcon size={20} />
          <span className="profile-link-label">Leetify</span>
        </span>
      )}
    </div>
  );
}
