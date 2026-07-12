import fs from "fs";

export interface MaFileSession {
  SteamID?: string;
  AccessToken?: string;
  RefreshToken?: string;
  SessionID?: string | null;
}

export interface MaFile {
  account_name?: string;
  shared_secret?: string;
  identity_secret?: string;
  Session?: MaFileSession;
}

export function loadMaFile(): MaFile | null {
  const mafilePath = process.env.STEAM_BOT_MAFILE_PATH?.trim();
  if (!mafilePath) return null;
  if (!fs.existsSync(mafilePath)) {
    console.warn(`[steam-sync] STEAM_BOT_MAFILE_PATH not found: ${mafilePath}`);
    return null;
  }
  try {
    return JSON.parse(fs.readFileSync(mafilePath, "utf8")) as MaFile;
  } catch (err) {
    console.error("[steam-sync] Failed to parse maFile:", err);
    return null;
  }
}

export function getSharedSecret(mafile: MaFile | null): string | undefined {
  const fromEnv = process.env.STEAM_BOT_SHARED_SECRET?.trim();
  if (fromEnv) return fromEnv;
  return mafile?.shared_secret?.trim() || undefined;
}

export function getRefreshToken(mafile: MaFile | null): string | undefined {
  return mafile?.Session?.RefreshToken?.trim() || undefined;
}

/** Decode JWT payload without verification (audience check only). */
function decodeJwtPayload(token: string): Record<string, unknown> | null {
  const parts = token.split(".");
  if (parts.length < 2) return null;
  try {
    const base64 = parts[1].replace(/-/g, "+").replace(/_/g, "/");
    const padded = base64 + "=".repeat((4 - (base64.length % 4)) % 4);
    return JSON.parse(Buffer.from(padded, "base64").toString("utf8")) as Record<string, unknown>;
  } catch {
    return null;
  }
}

export function getRefreshTokenAudiences(token: string): string[] {
  const payload = decodeJwtPayload(token);
  if (!payload) return [];
  const aud = payload.aud;
  if (Array.isArray(aud)) return aud.map(String);
  if (typeof aud === "string") return [aud];
  return [];
}

/** steam-user GC login requires a client-scoped refresh token (not web/mobile-only). */
export function isClientRefreshToken(token: string): boolean {
  return getRefreshTokenAudiences(token).includes("client");
}

export function getClientRefreshToken(mafile: MaFile | null): string | undefined {
  const token = getRefreshToken(mafile);
  if (!token) return undefined;
  if (isClientRefreshToken(token)) return token;
  return undefined;
}

export function getAccountName(mafile: MaFile | null, fallback: string): string {
  return (process.env.STEAM_BOT_USERNAME?.trim() || mafile?.account_name?.trim() || fallback).trim();
}

export function isThrottleError(err: unknown): boolean {
  const msg = err instanceof Error ? err.message : String(err);
  return (
    msg.includes("AccountLoginDeniedThrottle") ||
    msg.includes("RateLimitExceeded") ||
    msg.includes("AccountLoginDenied")
  );
}

/** Steam temporary lockout after too many login attempts — wait before retrying. */
export function throttleBackoffMs(): number {
  return Number(process.env.STEAM_LOGIN_THROTTLE_MS ?? 30 * 60 * 1000);
}
