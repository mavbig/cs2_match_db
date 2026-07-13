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
    steam_auth_code: str = ""
    steam_oldest_share_code: str = ""

    model_config = {"env_file": ".env", "extra": "ignore"}


settings = Settings()
