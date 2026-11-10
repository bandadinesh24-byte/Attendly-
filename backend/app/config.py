from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parents[2]

class Settings(BaseSettings):
    database_url: str = "sqlite:///./attendance.db"
    secret_key: str = "development-only-change-me"
    access_token_minutes: int = 720
    alert_threshold: int = 30
    campus_latitude: float = 37.4275
    campus_longitude: float = -122.1697
    qr_ttl_seconds: int = 120
    app_timezone: str = "UTC"
    # Resolve .env from the repository root, independent of whether Uvicorn or
    # an admin utility is launched from the repository or backend directory.
    model_config = SettingsConfigDict(env_file=PROJECT_ROOT / ".env", extra="ignore")

settings = Settings()

def utc_now_naive() -> datetime:
    """Return UTC for the legacy timezone-naive database columns."""
    return datetime.now(timezone.utc).replace(tzinfo=None)

def app_today():
    return datetime.now(ZoneInfo(settings.app_timezone)).date()
