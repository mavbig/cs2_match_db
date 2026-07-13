const SOURCE_ICONS: Record<string, { src: string; label: string }> = {
  steam_gc: { src: "/icons/steam.svg", label: "Steam" },
  faceit: { src: "/icons/faceit.svg", label: "FACEIT" },
  leetify: { src: "/icons/leetify.svg", label: "Leetify" },
};

export function MatchSourceBadge({ source, size = 22 }: { source: string; size?: number }) {
  const config = SOURCE_ICONS[source];
  if (!config) {
    return <span className="badge">{source}</span>;
  }

  return (
    <span className="match-source-badge" title={config.label}>
      <img src={config.src} alt={config.label} width={size} height={size} className="match-source-icon" />
    </span>
  );
}
