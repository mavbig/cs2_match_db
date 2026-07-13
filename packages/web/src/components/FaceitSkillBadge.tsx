export function FaceitSkillBadge({
  level,
  elo,
  challenger,
}: {
  level?: number | null;
  elo?: number | null;
  challenger?: boolean;
}) {
  if (level == null && elo == null) return null;

  let iconSrc: string | null = null;
  if (challenger) {
    iconSrc = "/faceit/levels/Challenger.png";
  } else if (level != null && level >= 1 && level <= 10) {
    iconSrc = `/faceit/levels/${level}.png`;
  }

  const title =
    challenger && level != null
      ? `Challenger · Level ${level}`
      : level != null
        ? `Level ${level}`
        : undefined;

  return (
    <span className="faceit-skill-badge" title={title}>
      {iconSrc && (
        // eslint-disable-next-line @next/next/no-img-element
        <img src={iconSrc} alt={title ?? "FACEIT skill level"} width={28} height={28} className="faceit-skill-icon" />
      )}
      {elo != null && <span className="faceit-skill-elo">{elo.toLocaleString()}</span>}
    </span>
  );
}
