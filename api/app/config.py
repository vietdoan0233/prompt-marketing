"""Runtime configuration. All values come from environment variables (see .env.example)."""

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

API_ROOT = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=API_ROOT / ".env", env_file_encoding="utf-8", extra="ignore")

    database_url: str = f"sqlite:///{(API_ROOT / 'mergero_dev.db').as_posix()}"
    source_config_path: Path = API_ROOT / "config" / "sources.yaml"
    fixtures_dir: Path = API_ROOT / "tests" / "fixtures"  # only used by synthetic tests
    # Countries the database may contain. Estonia-only deployment: any other country fails closed.
    active_countries: str = "EE"
    # Local cache for official bulk files (git-ignored). Files are re-downloaded when the portal copy changes.
    ee_cache_dir: Path = API_ROOT / "data" / "ee_ariregister"
    backup_dir: Path = API_ROOT / "backups"

    # Company qualification: headcount is the primary viability proxy.
    min_employees_default: int = 20
    sub_scale_max_employees: int = 2

    # Freshness: facts observed longer ago than this are marked "old"; companies not verified are "stale".
    stale_after_days: int = 365
    aging_after_days: int = 180

    # Default raw snapshot / raw input retention if a source does not specify one.
    default_retention_days: int = 30

    # Live network connectors are off by default: fixture-backed connectors keep the demo credential-free.
    live_connectors_enabled: bool = False
    http_timeout_seconds: float = 15.0
    # Optional contact appended to the crawler User-Agent (e.g. a team mailbox). Empty by default.
    http_contact: str = ""
    cors_origins: str = "http://localhost:3000"
    default_actor: str = "reviewer@mergero.local"


def active_countries() -> set[str]:
    return {c.strip().upper() for c in get_settings().active_countries.split(",") if c.strip()}


@lru_cache
def get_settings() -> Settings:
    return Settings()
