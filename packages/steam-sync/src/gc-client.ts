import SteamUser from "steam-user";
import SteamTotp from "steam-totp";
import GlobalOffensive from "globaloffensive";
import { EventEmitter } from "events";

import { normalizeGcMatch, parseShareCode } from "./match-parser.js";
import type { NormalizedMatch } from "./match-parser.js";

export class GcClient extends EventEmitter {
  private user: InstanceType<typeof SteamUser>;
  private csgo: InstanceType<typeof GlobalOffensive>;
  private connected = false;
  private pendingRequests = new Map<string, { resolve: (m: Record<string, unknown>[]) => void; reject: (e: Error) => void }>();

  constructor() {
    super();
    this.user = new SteamUser();
    this.csgo = new GlobalOffensive(this.user);

    this.user.on("loggedOn", () => {
      console.log("[steam-sync] Logged on to Steam");
      this.user.gamesPlayed([730]);
    });

    this.user.on("error", (err: Error) => {
      console.error("[steam-sync] Steam error:", err.message);
      this.emit("error", err);
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

    this.csgo.on("matchList", (matches: Record<string, unknown>[], _data: unknown, requestId?: string) => {
      if (requestId && this.pendingRequests.has(requestId)) {
        const { resolve } = this.pendingRequests.get(requestId)!;
        this.pendingRequests.delete(requestId);
        resolve(matches ?? []);
      }
      this.emit("matchList", matches);
    });
  }

  login(username: string, password: string, sharedSecret?: string): Promise<void> {
    return new Promise((resolve, reject) => {
      const logOnOptions: Record<string, string> = { accountName: username, password };

      if (sharedSecret) {
        logOnOptions.twoFactorCode = SteamTotp.generateAuthCode(sharedSecret);
      }

      this.user.once("loggedOn", () => resolve());
      this.user.once("error", reject);
      this.user.logOn(logOnOptions);
    });
  }

  waitForGc(timeoutMs = 60000): Promise<void> {
    if (this.connected) return Promise.resolve();
    return new Promise((resolve, reject) => {
      const timer = setTimeout(() => reject(new Error("GC connection timeout")), timeoutMs);
      this.once("ready", () => {
        clearTimeout(timer);
        resolve();
      });
    });
  }

  requestGame(shareCode: string): Promise<Record<string, unknown>[]> {
    return new Promise((resolve, reject) => {
      const requestId = `req_${Date.now()}`;
      const timer = setTimeout(() => {
        this.pendingRequests.delete(requestId);
        reject(new Error(`Request timeout for share code: ${shareCode}`));
      }, 30000);

      this.pendingRequests.set(requestId, {
        resolve: (matches) => {
          clearTimeout(timer);
          resolve(matches);
        },
        reject: (err) => {
          clearTimeout(timer);
          reject(err);
        },
      });

      try {
        this.csgo.requestGame(shareCode);
      } catch (err) {
        clearTimeout(timer);
        this.pendingRequests.delete(requestId);
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
    maxMatches = 200
  ): Promise<NormalizedMatch[]> {
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
        if (!matches.length) break;

        const normalized = normalizeGcMatch(matches[0], currentCode, mySteam64Id);
        if (normalized) {
          results.push(normalized);
          count++;
        }

        const prevCode = (matches[0] as Record<string, unknown>).watchablematchinfo
          ? String((matches[0] as Record<string, unknown>).watchablematchinfo)
          : null;

        currentCode = prevCode && prevCode.startsWith("CSGO") ? prevCode : null;

        await sleep(2000);
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
