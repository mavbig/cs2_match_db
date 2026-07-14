export const PROVIDER_ICONS: Record<string, { src: string; label: string }> = {
  steam: { src: "/icons/steam.svg", label: "Steam" },
  steam_gc: { src: "/icons/steam.svg", label: "Steam" },
  faceit: { src: "/icons/faceit.svg", label: "FACEIT" },
  leetify: { src: "/icons/leetify.svg", label: "Leetify" },
  csstats: { src: "https://csstats.gg/favicon.ico", label: "csstats.gg" },
};

export function ProviderIcon({
  provider,
  size = 20,
  className = "",
}: {
  provider: string;
  size?: number;
  className?: string;
}) {
  const config = PROVIDER_ICONS[provider];
  if (!config) {
    return null;
  }

  return (
    <img
      src={config.src}
      alt=""
      width={size}
      height={size}
      className={`provider-icon-img ${className}`.trim()}
      aria-hidden="true"
    />
  );
}
