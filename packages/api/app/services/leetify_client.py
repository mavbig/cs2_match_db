import httpx

from app.config import settings


class LeetifyClient:
    BASE = "https://api-public.cs-prod.leetify.com"

    def __init__(self, api_key: str | None = None):
        self.api_key = api_key or settings.leetify_api_key

    def _headers(self) -> dict:
        headers = {}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        return headers

    async def get_profile(self, steam64_id: str) -> dict | None:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(
                f"{self.BASE}/v3/profile",
                params={"steamId": steam64_id},
                headers=self._headers(),
            )
            if resp.status_code == 404:
                return None
            if resp.status_code == 401:
                return None
            resp.raise_for_status()
            return resp.json()

    async def get_profile_matches(self, steam64_id: str) -> dict | None:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(
                f"{self.BASE}/v3/profile/matches",
                params={"steamId": steam64_id},
                headers=self._headers(),
            )
            if resp.status_code in (404, 401):
                return None
            resp.raise_for_status()
            return resp.json()

    async def get_match_by_source(self, data_source: str, data_source_id: str) -> dict | None:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(
                f"{self.BASE}/v2/matches/{data_source}/{data_source_id}",
                headers=self._headers(),
            )
            if resp.status_code in (404, 401):
                return None
            resp.raise_for_status()
            return resp.json()
