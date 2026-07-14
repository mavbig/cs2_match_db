from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    database_url: str = "postgresql+asyncpg://cs2match:changeme@localhost:5432/cs2match"
    redis_url: str = "redis://localhost:6379/0"
    api_secret_key: str = "change-me"
    api_sync_token: str = "change-me"
    my_steam64_id: str = ""
    steam_api_key: str = ""
    faceit_api_key: str = ""
    faceit_nickname: str = ""
    leetify_api_key: str = ""
    leetify_session_token: str = ""
    # Host paths mounted in Docker for persistent secrets (see docker-compose.yml).
    leetify_session_token_file: str = "/run/secrets/leetify_session_token"
    secrets_env_file: str = "/config/.env"
    leetify_request_delay_ms: int = 500
    leetify_history_window_days: int = 180
    leetify_history_max_requests: int = 50
    leetify_history_months: int = 36  # legacy alias: used as years-back cap (months / 12)
    steam_auth_code: str = ""
    steam_oldest_share_code: str = ""
    csstats_cookie: str = ""
    csstats_request_delay_ms: int = 1500

    model_config = {"env_file": ".env", "extra": "ignore"}


settings = Settings()
