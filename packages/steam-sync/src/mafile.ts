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
