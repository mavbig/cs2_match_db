import { countryFlagSrc, countryLabel } from "@/lib/country";

export function CountryFlag({ code, size = 16 }: { code: string; size?: number }) {
  const src = countryFlagSrc(code, size <= 20 ? 20 : 40);
  if (!src) return null;
  return (
    // eslint-disable-next-line @next/next/no-img-element
    <img
      src={src}
      alt=""
      width={size}
      height={Math.round(size * 0.75)}
      className="country-flag-img"
      aria-hidden
    />
  );
}

export function CountryBadge({ code }: { code: string }) {
  const label = countryLabel(code);
  return (
    <span className="badge country-badge" title={label}>
      <CountryFlag code={code} />
      {label}
    </span>
  );
}
