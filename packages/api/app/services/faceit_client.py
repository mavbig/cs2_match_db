import httpx

from app.config import settings


class FaceitClient:
    BASE = "https://open.faceit.com/data/v4"

    def __init__(self, api_key: str | None = None):
        self.api_key = api_key or settings.faceit_api_key

    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {self.api_key}"}

    async def get_player_by_nickname(self, nickname: str) -> dict | None:
        if not self.api_key:
            return None
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(
                f"{self.BASE}/players",
                params={"nickname": nickname},
                headers=self._headers(),
            )
            if resp.status_code == 404:
                return None
            resp.raise_for_status()
            return resp.json()

    async def get_player_by_steam_id(self, steam64_id: str) -> dict | None:
        if not self.api_key:
            return None
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(
                f"{self.BASE}/players",
                params={"game": "cs2", "game_player_id": steam64_id},
                headers=self._headers(),
            )
            if resp.status_code == 404:
                return None
            resp.raise_for_status()
            return resp.json()

    async def get_match_history(self, player_id: str, offset: int = 0, limit: int = 20) -> dict:
        if not self.api_key:
            return {"items": []}
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(
                f"{self.BASE}/players/{player_id}/history",
                params={"game": "cs2", "offset": offset, "limit": limit},
                headers=self._headers(),
            )
            resp.raise_for_status()
            return resp.json()

    async def get_all_match_history(self, player_id: str, *, max_items: int = 200) -> list[dict]:
        items: list[dict] = []
        offset = 0
        page_size = 100
        while len(items) < max_items:
            batch = await self.get_match_history(
                player_id,
                offset=offset,
                limit=min(page_size, max_items - len(items)),
            )
            page = batch.get("items") or []
            if not page:
                break
            items.extend(page)
            if len(page) < page_size:
                break
            offset += len(page)
        return items

    async def get_match(self, match_id: str) -> dict:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(f"{self.BASE}/matches/{match_id}", headers=self._headers())
            resp.raise_for_status()
            return resp.json()

    async def get_match_stats(self, match_id: str) -> dict:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(f"{self.BASE}/matches/{match_id}/stats", headers=self._headers())
            resp.raise_for_status()
            return resp.json()

    async def get_player_stats(self, player_id: str, game: str = "cs2") -> dict | None:
        if not self.api_key:
            return None
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(
                f"{self.BASE}/players/{player_id}/stats/{game}",
                headers=self._headers(),
            )
            if resp.status_code == 404:
                return None
            resp.raise_for_status()
            return resp.json()

    async def get_player_recent_match_stats(
        self,
        player_id: str,
        game: str = "cs2",
        *,
        offset: int = 0,
        limit: int = 20,
    ) -> dict | None:
        if not self.api_key:
            return None
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(
                f"{self.BASE}/players/{player_id}/games/{game}/stats",
                params={"offset": offset, "limit": limit},
                headers=self._headers(),
            )
            if resp.status_code == 404:
                return None
            resp.raise_for_status()
            return resp.json()

    async def get_all_player_recent_match_stats(
        self, player_id: str, *, game: str = "cs2", max_items: int = 100
    ) -> list[dict]:
        items: list[dict] = []
        offset = 0
        page_size = 100
        while len(items) < max_items:
            batch = await self.get_player_recent_match_stats(
                player_id,
                game,
                offset=offset,
                limit=min(page_size, max_items - len(items)),
            )
            if not batch:
                break
            page = batch.get("items") or []
            if not page:
                break
            items.extend(page)
            if len(page) < page_size:
                break
            offset += len(page)
        return items

    async def get_player_bans(self, player_id: str, limit: int = 20) -> dict | None:
        if not self.api_key:
            return None
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(
                f"{self.BASE}/players/{player_id}/bans",
                params={"limit": limit},
                headers=self._headers(),
            )
            if resp.status_code == 404:
                return None
            resp.raise_for_status()
            return resp.json()
