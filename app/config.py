"""Application configuration loaded from environment variables.

A single ``Settings`` instance is reused everywhere via :func:`get_settings`.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Annotated

from pydantic import AnyHttpUrl, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Strongly typed settings."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # Core
    app_env: str = "development"
    app_name: str = "talk-confident"
    log_level: str = "INFO"

    # Database
    database_url: str = Field(
        default="postgresql+asyncpg://talkconfident:talkconfident@localhost:5432/talkconfident",
    )

    @field_validator("database_url")
    @classmethod
    def _normalise_database_url(cls, v: str) -> str:
        """Coerce Render/Heroku-style URLs to the async SQLAlchemy dialect.

        Managed hosts (Render, Heroku, Fly, etc.) hand out URLs like
        ``postgres://user:pass@host/db``. SQLAlchemy 2.x rejects the bare
        ``postgres`` scheme, and we always want the asyncpg driver here.
        """
        if v.startswith("postgres://"):
            return v.replace("postgres://", "postgresql+asyncpg://", 1)
        if v.startswith("postgresql://") and "+asyncpg" not in v:
            return v.replace("postgresql://", "postgresql+asyncpg://", 1)
        return v

    # Auth
    jwt_secret: str = Field(default="change-me", min_length=8)
    jwt_algorithm: str = "HS256"
    jwt_expires_minutes: int = 60 * 24 * 30  # 30 days

    google_oauth_client_id: str = Field(default="")
    google_oauth_additional_audiences: str = ""

    # Gemini
    gemini_api_key: str = Field(default="")
    # Default to Gemini 3.1 Flash-Lite — the cheapest Gemini 3 tier that still
    # ships with native audio understanding, structured output, and context
    # caching. See https://ai.google.dev/gemini-api/docs/models/gemini-3.1-flash-lite
    gemini_model: str = "gemini-3.1-flash-lite"
    # Gemini 3 replaced ``thinking_budget`` with ``thinking_level``. Allowed
    # values: ``minimal`` | ``low`` | ``medium`` | ``high``. We default to
    # ``high`` for the deepest reasoning on every call.
    # https://ai.google.dev/gemini-api/docs/gemini-3
    gemini_thinking_level: str = "high"
    gemini_cache_ttl_seconds: int = 3600

    # Context budget
    context_token_threshold: int = 80_000

    # Uploads
    max_audio_bytes: int = 50 * 1024 * 1024
    inline_audio_threshold_bytes: int = 20 * 1024 * 1024

    # Self-poll keepalive — keeps Render's free-tier instance from spinning
    # down. ``self_ping_url`` may be left empty; the loop then falls back to
    # ``RENDER_EXTERNAL_URL`` (Render injects this automatically). If neither
    # is set, the keepalive task is skipped entirely.
    self_ping_url: str = ""
    self_ping_interval_seconds: int = 600  # 10 minutes
    self_ping_path: str = "/healthz"

    # CORS
    cors_origins: str = ""

    @field_validator("cors_origins")
    @classmethod
    def _normalise_cors(cls, v: str) -> str:
        return v.strip()

    @property
    def cors_origin_list(self) -> list[str]:
        if not self.cors_origins:
            return []
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def google_audiences(self) -> list[str]:
        """All Google client IDs we accept as ID-token audiences."""
        audiences = [self.google_oauth_client_id] if self.google_oauth_client_id else []
        extra = (self.google_oauth_additional_audiences or "").strip()
        if extra:
            audiences.extend(a.strip() for a in extra.split(",") if a.strip())
        return audiences


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return a cached ``Settings`` instance."""
    return Settings()


SettingsDep = Annotated[Settings, "Settings"]
