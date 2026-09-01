"""Environment & config loading."""

from functools import lru_cache

from pydantic import ValidationError
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings loaded from environment / .env file."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    RAZORPAY_KEY_ID: str
    RAZORPAY_KEY_SECRET: str
    WEBHOOK_SECRET: str
    WEBHOOK_SECRET_PREVIOUS: str | None = None

    DATABASE_URL: str
    REDIS_URL: str
    GEMINI_API_KEY: str

    APP_ENV: str = "development"
    APP_PORT: int = 8000
    LOG_LEVEL: str = "INFO"
    TIMEZONE: str = "Asia/Kolkata"


@lru_cache
def get_settings() -> Settings:
    """Build Settings once, failing fast with a clear message on missing vars."""
    try:
        return Settings()
    except ValidationError as exc:
        missing = [str(err["loc"][0]) for err in exc.errors() if err["type"] == "missing"]
        if missing:
            raise RuntimeError(
                "Missing required environment variables: " + ", ".join(sorted(missing))
            ) from exc
        raise RuntimeError(f"Invalid configuration: {exc}") from exc