import logging
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.services.match_service import get_setting, set_setting

logger = logging.getLogger(__name__)

LEETIFY_SESSION_ENV_KEY = "LEETIFY_SESSION_TOKEN"


def _read_text_file(path: Path) -> str | None:
    try:
        if not path.is_file():
            return None
        value = path.read_text(encoding="utf-8").strip()
        return value or None
    except OSError as exc:
        logger.warning("Could not read %s: %s", path, exc)
        return None


def _write_text_file(path: Path, value: str) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(value.strip() + "\n", encoding="utf-8")
    except OSError as exc:
        logger.warning("Could not write %s: %s", path, exc)


def _update_dotenv(path: Path, key: str, value: str | None) -> None:
    if not path.parent.exists():
        return
    try:
        lines = path.read_text(encoding="utf-8").splitlines() if path.is_file() else []
    except OSError as exc:
        logger.warning("Could not read %s: %s", path, exc)
        return

    new_lines: list[str] = []
    found = False
    for line in lines:
        if line.startswith(f"{key}="):
            found = True
            if value:
                new_lines.append(f"{key}={value}")
            continue
        new_lines.append(line)

    if value and not found:
        if new_lines and new_lines[-1].strip():
            new_lines.append("")
        new_lines.append(f"# Leetify browser session (Bearer JWT from games/history request)")
        new_lines.append(f"{key}={value}")

    try:
        path.write_text("\n".join(new_lines).rstrip() + "\n", encoding="utf-8")
    except OSError as exc:
        logger.warning("Could not write %s: %s", path, exc)


async def get_leetify_session_token(db: AsyncSession) -> str:
    db_value = await get_setting(db, "leetify_session_token")
    if db_value:
        return db_value

    if settings.leetify_session_token_file:
        file_value = _read_text_file(Path(settings.leetify_session_token_file))
        if file_value:
            return file_value

    return settings.leetify_session_token or ""


async def save_leetify_session_token(db: AsyncSession, value: str | None) -> None:
    cleaned = value.strip() if value else None
    await set_setting(db, "leetify_session_token", cleaned or None)

    if cleaned:
        if settings.leetify_session_token_file:
            _write_text_file(Path(settings.leetify_session_token_file), cleaned)
        if settings.secrets_env_file:
            _update_dotenv(Path(settings.secrets_env_file), LEETIFY_SESSION_ENV_KEY, cleaned)
        logger.info("Leetify session token saved (database + disk)")
    else:
        if settings.leetify_session_token_file:
            token_path = Path(settings.leetify_session_token_file)
            if token_path.is_file():
                try:
                    token_path.unlink()
                except OSError as exc:
                    logger.warning("Could not delete %s: %s", token_path, exc)
        if settings.secrets_env_file:
            _update_dotenv(Path(settings.secrets_env_file), LEETIFY_SESSION_ENV_KEY, None)
