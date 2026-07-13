function normalizeCountryCode(code: string | null | undefined): string | null {
  if (!code) return null;
  const trimmed = code.trim();
  if (trimmed.length !== 2) return null;
  const upper = trimmed.toUpperCase();
  if (upper.charCodeAt(0) < 65 || upper.charCodeAt(0) > 90) return null;
  if (upper.charCodeAt(1) < 65 || upper.charCodeAt(1) > 90) return null;
  return upper;
}

/** ISO 3166-1 alpha-2 → flag image URL (works on Windows; emoji flags often render as "AT"). */
export function countryFlagSrc(code: string | null | undefined, width = 40): string | null {
  const normalized = normalizeCountryCode(code);
  if (!normalized) return null;
  return `https://flagcdn.com/w${width}/${normalized.toLowerCase()}.png`;
}

export function countryLabel(code: string | null | undefined): string {
  if (!code) return "";
  try {
    return new Intl.DisplayNames(["en"], { type: "region" }).of(code.toUpperCase()) ?? code.toUpperCase();
  } catch {
    return code.toUpperCase();
  }
}
