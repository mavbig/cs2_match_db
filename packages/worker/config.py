from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    database_url: str = "postgresql+asyncpg://cs2match:changeme@postgres:5432/cs2match"
    redis_url: str = "redis://redis:6379/0"
    steam_api_key: str = ""
    faceit_api_key: str = ""
    faceit_nickname: str = ""
    leetify_api_key: str = ""
    my_steam64_id: str = ""
    api_internal_url: str = "http://api:8000"

    model_config = {"env_file": ".env", "extra": "ignore"}


settings = Settings()
