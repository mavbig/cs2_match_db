import httpx

from app.config import settings


class SteamClient:
    BASE = "https://api.steampowered.com"

    def __init__(self, api_key: str | None = None):
        self.api_key = api_key or settings.steam_api_key

    async def get_player_summaries(self, steam64_ids: list[str]) -> list[dict]:
        if not self.api_key or not steam64_ids:
            return []
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(
                f"{self.BASE}/ISteamUser/GetPlayerSummaries/v0002/",
                params={"key": self.api_key, "steamids": ",".join(steam64_ids)},
            )
            resp.raise_for_status()
            return resp.json().get("response", {}).get("players", [])

    async def resolve_vanity_url(self, vanity: str) -> str | None:
        if not self.api_key:
            return None
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(
                f"{self.BASE}/ISteamUser/ResolveVanityURL/v0001/",
                params={"key": self.api_key, "vanityurl": vanity},
            )
            resp.raise_for_status()
            data = resp.json().get("response", {})
            if data.get("success") == 1:
                return data.get("steamid")
            return None
