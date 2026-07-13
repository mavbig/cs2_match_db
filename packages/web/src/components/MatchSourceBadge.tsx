import { PROVIDER_ICONS } from "@/components/ProviderIcon";

export function MatchSourceBadge({ source, size = 22 }: { source: string; size?: number }) {
  const config = PROVIDER_ICONS[source];
  if (!config) {
    return <span className="badge">{source}</span>;
  }

  return (
    <span className="match-source-badge" title={config.label}>
      <img src={config.src} alt={config.label} width={size} height={size} className="match-source-icon" />
    </span>
  );
}
