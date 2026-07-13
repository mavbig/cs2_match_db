from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field


class MatchPlayerIn(BaseModel):
    steam64_id: str
    name: str | None = None
    team: str | None = None
    kills: int | None = None
    deaths: int | None = None
    assists: int | None = None
    mvps: int | None = None
    headshot_pct: float | None = None
    score: int | None = None
    ping: int | None = None
    is_me: bool = False


class MatchSyncStatusOut(BaseModel):
    steam_synced: bool = False
    steam_synced_at: datetime | None = None
    leetify_synced: bool = False
    leetify_synced_at: datetime | None = None


class PlayerSyncResultOut(BaseModel):
    player_id: UUID
    sources: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)


class MatchIngestIn(BaseModel):
    source: str = "steam_gc"
    source_match_id: str
    map: str | None = None
    mode: str | None = None
    played_at: datetime | None = None
    score_team_a: int | None = None
    score_team_b: int | None = None
    duration_seconds: int | None = None
    share_code: str | None = None
    raw_payload: dict | None = None
    players: list[MatchPlayerIn] = Field(default_factory=list)


class MatchIngestBatchIn(BaseModel):
    matches: list[MatchIngestIn]


class MatchPlayerOut(BaseModel):
    player_id: UUID
    steam64_id: str
    name: str | None
    team: str | None
    kills: int | None
    deaths: int | None
    assists: int | None
    mvps: int | None
    headshot_pct: float | None
    score: int | None
    ping: int | None
    is_me: bool
    times_played_with_me: int | None = None

    model_config = {"from_attributes": True}


class MatchOut(BaseModel):
    id: UUID
    source: str
    source_match_id: str
    map: str | None
    mode: str | None
    played_at: datetime | None
    score_team_a: int | None
    score_team_b: int | None
    duration_seconds: int | None
    share_code: str | None
    demo_url: str | None = None
    sync_status: MatchSyncStatusOut = Field(default_factory=MatchSyncStatusOut)
    players: list[MatchPlayerOut] = Field(default_factory=list)

    model_config = {"from_attributes": True}


class MatchSummaryOut(BaseModel):
    id: UUID
    source: str
    source_match_id: str
    map: str | None
    mode: str | None
    played_at: datetime | None
    score_team_a: int | None
    score_team_b: int | None
    player_count: int = 0

    model_config = {"from_attributes": True}


class PlayerMatchOut(MatchSummaryOut):
    kills: int | None = None
    deaths: int | None = None
    assists: int | None = None
    mvps: int | None = None
    headshot_pct: float | None = None
    score: int | None = None


class PlayerOut(BaseModel):
    id: UUID
    steam64_id: str
    current_name: str | None
    avatar_url: str | None
    profile_url: str | None
    first_seen_at: datetime
    last_seen_at: datetime

    model_config = {"from_attributes": True}


class PlayerDetailOut(PlayerOut):
    name_history: list[str] = Field(default_factory=list)
    platform_accounts: list[dict] = Field(default_factory=list)
    latest_stats: dict = Field(default_factory=dict)
    match_count: int = 0
    times_played_with_me: int | None = None


class PlayedWithOut(BaseModel):
    player: PlayerOut
    times_together: int
    first_together: datetime | None
    last_together: datetime | None
    shared_matches: list[MatchSummaryOut] = Field(default_factory=list)


class TeammatesListOut(BaseModel):
    teammates: list[PlayedWithOut] = Field(default_factory=list)
    has_more: bool = False


class SearchResultOut(BaseModel):
    players: list[PlayerOut] = Field(default_factory=list)


class SyncJobOut(BaseModel):
    id: UUID
    job_type: str
    status: str
    started_at: datetime | None
    finished_at: datetime | None
    matches_imported: int
    error_message: str | None

    model_config = {"from_attributes": True}


class SyncStatusOut(BaseModel):
    last_steam_sync: datetime | None = None
    last_faceit_sync: datetime | None = None
    total_matches: int = 0
    total_players: int = 0
    pending_jobs: int = 0
    steam_configured: bool = False
    faceit_configured: bool = False


class SettingsUpdateIn(BaseModel):
    my_steam64_id: str | None = None
    steam_auth_code: str | None = None
    steam_oldest_share_code: str | None = None
    steam_api_key: str | None = None
    faceit_api_key: str | None = None
    faceit_nickname: str | None = None
    leetify_api_key: str | None = None
    leetify_session_token: str | None = None


class SettingsOut(BaseModel):
    my_steam64_id: str | None = None
    steam_auth_code_set: bool = False
    steam_oldest_share_code_set: bool = False
    steam_api_key_set: bool = False
    faceit_api_key_set: bool = False
    faceit_nickname: str | None = None
    leetify_api_key_set: bool = False
    leetify_session_token_set: bool = False
    onboarding_complete: bool = False


class ShareCodeImportIn(BaseModel):
    share_code: str


class PlayerLookupIn(BaseModel):
    steam_url_or_id: str


class DashboardOut(BaseModel):
    recent_matches: list[MatchSummaryOut] = Field(default_factory=list)
    top_teammates: list[PlayedWithOut] = Field(default_factory=list)
    top_teammates_has_more: bool = False
    sync_status: SyncStatusOut
