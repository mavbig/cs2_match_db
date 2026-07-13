function normalizeCountryCode(code: string | null | undefined): string | null {
  if (!code) return null;
  const trimmed = code.trim();
  if (trimmed.length !== 2) return null;
  const upper = trimmed.toUpperCase();
  if (upper.charCodeAt(0) < 65 || upper.charCodeAt(0) > 90) return null;
  if (upper.charCodeAt(1) < 65 || upper.charCodeAt(1) > 90) return null;
  return upper;
}

const FLAGCDN_WIDTHS = [20, 40] as const;
type FlagcdnWidth = (typeof FLAGCDN_WIDTHS)[number];

function snapFlagcdnWidth(width: number): FlagcdnWidth {
  return width <= 20 ? 20 : 40;
}

/** ISO 3166-1 alpha-2 → flag image URL (works on Windows; emoji flags often render as "AT"). */
export function countryFlagSrc(code: string | null | undefined, width: FlagcdnWidth | number = 40): string | null {
  const normalized = normalizeCountryCode(code);
  if (!normalized) return null;
  const w = typeof width === "number" ? snapFlagcdnWidth(width) : width;
  return `https://flagcdn.com/w${w}/${normalized.toLowerCase()}.png`;
}

export function countryLabel(code: string | null | undefined): string {
  if (!code) return "";
  try {
    return new Intl.DisplayNames(["en"], { type: "region" }).of(code.toUpperCase()) ?? code.toUpperCase();
  } catch {
    return code.toUpperCase();
  }
}
