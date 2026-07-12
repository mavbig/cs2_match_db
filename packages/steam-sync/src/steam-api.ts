export class ShareCodeChainEnd extends Error {
  constructor(message: string) {
    super(message);
    this.name = "ShareCodeChainEnd";
  }
}

export async function getNextMatchSharingCode(
  apiKey: string,
  steam64Id: string,
  authCode: string,
  knownCode: string
): Promise<string> {
  const params = new URLSearchParams({
    key: apiKey,
    steamid: steam64Id,
    steamidkey: authCode,
    knowncode: knownCode,
  });

  const resp = await fetch(
    `https://api.steampowered.com/ICSGOPlayers_730/GetNextMatchSharingCode/v1?${params}`
  );

  if (resp.status === 412 || resp.status === 404) {
    throw new ShareCodeChainEnd(`No newer share code after ${knownCode} (${resp.status})`);
  }

  const body = (await resp.json()) as { result?: { nextcode?: string }; nextcode?: string };
  const nextCode = body.result?.nextcode ?? body.nextcode;

  if (!resp.ok) {
    throw new Error(`GetNextMatchSharingCode failed (${resp.status}): ${JSON.stringify(body)}`);
  }

  if (!nextCode || !nextCode.startsWith("CSGO")) {
    throw new ShareCodeChainEnd(`No newer share code after ${knownCode}`);
  }

  return nextCode;
}
