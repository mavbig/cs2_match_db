import fs from "fs";
import path from "path";
import SteamUser from "steam-user";
import SteamTotp from "steam-totp";
import NodeCS2 from "node-cs2";
import { EventEmitter } from "events";

import {
  getAccountName,
  getClientRefreshToken,
  getRefreshToken,
  getRefreshTokenAudiences,
  getSharedSecret,
  loadMaFile,
} from "./mafile.js";
import { normalizeGcMatch, parseShareCode, buildGcParseDebug } from "./match-parser.js";
import type { NormalizedMatch } from "./match-parser.js";
import { getNextMatchSharingCode, ShareCodeChainEnd } from "./steam-api.js";

const MATCH_CHAIN_DELAY_MS = Number(process.env.SYNC_MATCH_DELAY_MS ?? 500);

const SENTRY_DIR = process.env.STEAM_SENTRY_DIR ?? "/data/steam";
const GC_CONNECT_TIMEOUT_MS = Number(process.env.GC_CONNECT_TIMEOUT_MS ?? 180_000);
const MATCH_REQUEST_TIMEOUT_MS = Number(process.env.MATCH_REQUEST_TIMEOUT_MS ?? 45_000);
const GC_DEBUG = process.env.STEAM_SYNC_DEBUG === "1";

interface PendingMatchRequest {
  shareCode: string;
  resolve: (matches: Record<string, unknown>[]) => void;
  reject: (err: Error) => void;
  timer: ReturnType<typeof setTimeout>;
}

function sentryPath(username: string): string {
  const safe = username.replace(/[^a-zA-Z0-9_-]/g, "_");
  return path.join(SENTRY_DIR, `${safe}.sentry`);
}

export class GcClient extends EventEmitter {
  private user: InstanceType<typeof SteamUser>;
  private csgo: InstanceType<typeof NodeCS2>;
  private connected = false;
  private gcSessionStarted = false;
  private pendingMatchRequest: PendingMatchRequest | null = null;

  constructor() {
    super();
    this.user = new SteamUser({ promptSteamGuardCode: false, autoRelogin: true });
    this.csgo = new NodeCS2(this.user);

    this.on("error", () => {
      /* default handler so login errors do not crash the process */
    });

    this.user.on("error", (err: Error) => {
      console.error("[steam-sync] Steam client error:", err.message);
    });

    this.user.on("appLaunched", (appid: number) => {
      if (appid !== 730) return;
      console.log("[steam-sync] CS2 app launched — sending GC hello");
      this.csgo.helloGC();
    });

    this.csgo.on("connectedToGC", () => {
      console.log("[steam-sync] Connected to CS2 Game Coordinator");
      this.connected = true;
      this.emit("ready");
    });

    this.csgo.on("disconnectedFromGC", (reason: number) => {
      console.warn("[steam-sync] Disconnected from GC:", reason);
      this.connected = false;
    });

    this.csgo.on("error", (err: Error & { code?: number; country?: string }) => {
      const extra = [err.code != null ? `code=${err.code}` : null, err.country ? `country=${err.country}` : null]
        .filter(Boolean)
        .join(" ");
      console.error(`[steam-sync] GC fatal error: ${err.message}${extra ? ` (${extra})` : ""}`);
    });

    if (GC_DEBUG) {
      this.csgo.on("debug", (msg: string) => {
        console.log("[steam-sync][gc-debug]", msg);
      });
    }

    this.csgo.on("matchList", (matches: Record<string, unknown>[], proto?: { msgrequestid?: number }) => {
      const count = matches?.length ?? 0;
      const msgRequestId = proto?.msgrequestid;
      if (GC_DEBUG || this.pendingMatchRequest) {
        console.log(
          `[steam-sync] matchList: ${count} match(es)` +
            (msgRequestId != null ? `, msgrequestid=${msgRequestId}` : "")
        );
      }

      const pending = this.pendingMatchRequest;
      if (pending) {
        this.pendingMatchRequest = null;
        clearTimeout(pending.timer);
        pending.resolve(matches ?? []);
      } else if (count > 0) {
        console.log("[steam-sync] Unsolicited matchList ignored");
      }

      this.emit("matchList", matches);
    });
  }

  async login(username: string, password: string, sharedSecret?: string): Promise<void> {
    const mafile = loadMaFile();
    const rawRefresh = getRefreshToken(mafile);
    const refreshToken = getClientRefreshToken(mafile);
    const accountName = getAccountName(mafile, username);

    if (rawRefresh && !refreshToken) {
      const aud = getRefreshTokenAudiences(rawRefresh);
      console.log(
        `[steam-sync] RefreshToken audiences [${aud.join(", ")}] — web/mobile only; using password + TOTP`
      );
    }

    if (refreshToken) {
      try {
        await this.logOn({ refreshToken }, accountName, "client refresh token");
        await this.startGcSession();
        return;
      } catch (err) {
        const msg = err instanceof Error ? err.message : String(err);
        console.warn(`[steam-sync] Client refresh token login failed (${msg}), trying password + TOTP...`);
        this.resetGcSession();
      }
    }

    const secret = sharedSecret ?? getSharedSecret(mafile);
    if (!secret) {
      throw new Error(
        "Missing credentials: set STEAM_BOT_MAFILE_PATH to your .maFile (with Session.RefreshToken), " +
          "or set STEAM_BOT_SHARED_SECRET + STEAM_BOT_PASSWORD. See README → Steam Guard (TOTP)."
      );
    }
    if (!password) {
      throw new Error("STEAM_BOT_PASSWORD is required when refresh token login is unavailable.");
    }

    await this.logOn(
      {
        accountName,
        password,
        twoFactorCode: SteamTotp.generateAuthCode(secret),
      },
      accountName,
      "password + TOTP"
    );
    await this.startGcSession();
  }

  private resetGcSession(): void {
    this.connected = false;
    this.gcSessionStarted = false;
  }

  private startGcSession(): Promise<void> {
    if (this.connected) return Promise.resolve();
    if (this.gcSessionStarted) return this.waitForGc();

    this.gcSessionStarted = true;
    console.log("[steam-sync] Starting CS2 session (app 730) for Game Coordinator...");
    this.user.gamesPlayed([730], true);
    return this.waitForGc();
  }

  private logOn(
    logOnOptions: Record<string, unknown>,
    accountName: string,
    method: string
  ): Promise<void> {
    return new Promise((resolve, reject) => {
      fs.mkdirSync(SENTRY_DIR, { recursive: true });
      const sentryFile = sentryPath(accountName);

      if (!logOnOptions.refreshToken && fs.existsSync(sentryFile)) {
        logOnOptions.sentry = fs.readFileSync(sentryFile);
        console.log("[steam-sync] Using saved sentry file for", accountName);
      }

      const cleanup = () => {
        this.user.off("loggedOn", onLoggedOn);
        this.user.off("error", onError);
        this.user.off("steamGuard", onSteamGuard);
        this.user.off("sentry", onSentry);
      };

      const onLoggedOn = () => {
        console.log("[steam-sync] Logged on to Steam");
        cleanup();
        resolve();
      };

      const onError = (err: Error) => {
        cleanup();
        reject(err);
      };

      const onSteamGuard = () => {
        cleanup();
        reject(
          new Error(
            "Bot account uses email Steam Guard. Link Mobile Authenticator and use .maFile with shared_secret."
          )
        );
      };

      const onSentry = (sentry: Buffer) => {
        fs.writeFileSync(sentryFile, sentry);
        console.log("[steam-sync] Saved sentry file for future logins");
      };

      this.user.once("loggedOn", onLoggedOn);
      this.user.once("error", onError);
      this.user.once("steamGuard", onSteamGuard);
      this.user.once("sentry", onSentry);

      console.log(`[steam-sync] Logging in bot account (${method})...`);
      try {
        this.user.logOn(logOnOptions);
      } catch (err) {
        cleanup();
        reject(err instanceof Error ? err : new Error(String(err)));
      }
    });
  }

  waitForGc(timeoutMs = GC_CONNECT_TIMEOUT_MS): Promise<void> {
    if (this.connected) return Promise.resolve();
    return new Promise((resolve, reject) => {
      const timer = setTimeout(() => {
        cleanup();
        reject(
          new Error(
            `GC connection timeout after ${Math.round(timeoutMs / 1000)}s — ` +
              "ensure the bot account owns CS2 and is not stuck in another session"
          )
        );
      }, timeoutMs);

      const onReady = () => {
        cleanup();
        resolve();
      };

      const onGcError = (err: Error) => {
        cleanup();
        reject(err);
      };

      const cleanup = () => {
        clearTimeout(timer);
        this.off("ready", onReady);
        this.csgo.off("error", onGcError);
      };

      this.once("ready", onReady);
      this.csgo.once("error", onGcError);
    });
  }

  requestGame(shareCode: string): Promise<Record<string, unknown>[]> {
    if (this.pendingMatchRequest) {
      return Promise.reject(new Error("Another share code request is already in flight"));
    }

    const decoded = parseShareCode(shareCode);
    if (!decoded) {
      return Promise.reject(new Error(`Invalid share code format: ${shareCode}`));
    }

    return new Promise((resolve, reject) => {
      const timer = setTimeout(() => {
        if (this.pendingMatchRequest?.shareCode === shareCode) {
          this.pendingMatchRequest = null;
        }
        reject(new Error(`Request timeout for share code: ${shareCode}`));
      }, MATCH_REQUEST_TIMEOUT_MS);

      this.pendingMatchRequest = {
        shareCode,
        resolve,
        reject,
        timer,
      };

      try {
        if (GC_DEBUG) {
          console.log(
            `[steam-sync] GC requestGame ${shareCode} ` +
              `(matchId=${decoded.matchId}, outcomeId=${decoded.outcomeId}, token=${decoded.token})`
          );
        } else {
          console.log(`[steam-sync] GC requestGame ${shareCode}`);
        }
        this.csgo.requestGame(shareCode);
      } catch (err) {
        clearTimeout(timer);
        this.pendingMatchRequest = null;
        reject(err instanceof Error ? err : new Error(String(err)));
      }
    });
  }

  async fetchMatchByShareCode(shareCode: string, mySteam64Id: string): Promise<NormalizedMatch | null> {
    const matches = await this.requestGame(shareCode);
    if (!matches.length) return null;
    return normalizeGcMatch(matches[0], shareCode, mySteam64Id);
  }

  async walkShareCodeChain(
    startShareCode: string,
    authCode: string,
    mySteam64Id: string,
    steamApiKey: string,
    maxMatches = 200
  ): Promise<NormalizedMatch[]> {
    if (!steamApiKey) {
      throw new Error("Steam Web API key is required for share code chain walking");
    }

    const results: NormalizedMatch[] = [];
    const seen = new Set<string>();
    let currentCode: string | null = startShareCode;
    let count = 0;

    while (currentCode && count < maxMatches) {
      if (seen.has(currentCode)) break;
      seen.add(currentCode);

      console.log(`[steam-sync] Fetching match ${count + 1}: ${currentCode}`);

      try {
        const matches = await this.requestGame(currentCode);
        if (!matches.length) {
          console.warn(`[steam-sync] GC returned empty matchList for ${currentCode}`);
          break;
        }

        const normalized = normalizeGcMatch(matches[0], currentCode, mySteam64Id);
        if (GC_DEBUG && matches[0]) {
          console.log(`[steam-sync] Parse debug ${currentCode}:`, JSON.stringify(buildGcParseDebug(matches[0])));
        }
        if (normalized) {
          results.push(normalized);
          count++;
        }

        try {
          currentCode = await getNextMatchSharingCode(
            steamApiKey,
            mySteam64Id,
            authCode,
            currentCode
          );
          console.log(`[steam-sync] Next share code: ${currentCode}`);
        } catch (err) {
          if (err instanceof ShareCodeChainEnd) {
            console.log(`[steam-sync] Share code chain ended: ${err.message}`);
          } else {
            console.error(`[steam-sync] Failed to get next share code after ${currentCode}:`, err);
          }
          break;
        }

        await sleep(MATCH_CHAIN_DELAY_MS);
      } catch (err) {
        console.error(`[steam-sync] Error fetching ${currentCode}:`, err);
        break;
      }
    }

    return results;
  }
}

function sleep(ms: number): Promise<void> {
  return new Promise((r) => setTimeout(r, ms));
}

export { parseShareCode };
