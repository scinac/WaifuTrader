"""Application settings, read from environment variables prefixed with TT_."""
from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="TT_", env_file=".env", extra="ignore")

    # Database. Production: postgresql+asyncpg://user:pass@db:5432/tt
    database_url: str = "sqlite+aiosqlite:///./tt.db"
    # Create tables on startup (dev/tests). Production uses `alembic upgrade head`.
    auto_create_schema: bool = False

    # Fernet key for encrypting TOTP secrets and broker credentials.
    # Generate with: python -m tt.cli gen-master-key
    master_key: str = Field(..., min_length=40)

    session_ttl_hours: int = 12
    cli_token_ttl_days: int = 90
    cookie_secure: bool = True
    invite_ttl_hours: int = 72

    # Login lockout (FSD acceptance: 5 failures lock for 15 min)
    max_failed_logins: int = 5
    lockout_minutes: int = 15

    # Market data
    marketdata_enabled: bool = True
    tracked_symbols: list[str] = ["kraken:BTC/EUR", "kraken:ETH/EUR"]
    stale_after_seconds_crypto: int = 10
    candle_publish_interval_ms: int = 250
    backfill_on_start_minutes: int = 720  # Kraken OHLC returns at most 720 bars


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
