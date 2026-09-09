"""Application configuration using Pydantic Settings."""

from functools import lru_cache
from typing import Literal, Optional

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Placeholder shipped in .env.example — must never reach production.
INSECURE_SECRET_KEYS = {
    "change-me-in-production",
    "your-secret-key-change-in-production",
}
MIN_SECRET_KEY_LENGTH = 32


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
    )

    # Application
    app_name: str = "ExpertAP"
    environment: Literal["development", "staging", "production", "test"] = "development"
    debug: bool = False
    log_level: str = "INFO"
    secret_key: str = "change-me-in-production"

    # CORS — comma-separated list of allowed browser origins. The SPA is
    # served from the same origin as the API, so only dev servers need this.
    cors_origins: str = "http://localhost:3000,http://localhost:5173,http://127.0.0.1:3000"

    # Largest request body accepted (multipart uploads, base64 payloads).
    max_request_body_bytes: int = 60 * 1024 * 1024
    max_upload_bytes: int = 25 * 1024 * 1024

    # Database - Optional for demo/test mode
    database_url: Optional[str] = None
    skip_db: bool = False  # Set to True to run without database

    # Redis
    redis_url: str = "redis://localhost:6379"

    # LLM Providers
    vertex_ai_project: str = ""
    vertex_ai_location: str = "europe-west1"
    gemini_api_key: str = ""
    openai_api_key: str = ""
    anthropic_api_key: str = ""
    groq_api_key: str = ""
    openrouter_api_key: str = ""

    # Embedding
    embedding_provider: Literal["vertex", "openai", "local"] = "vertex"
    embedding_model: str = "text-embedding-004"

    # JWT Authentication
    access_token_expire_minutes: int = 30
    refresh_token_expire_days: int = 7
    jwt_algorithm: str = "HS256"

    # Rate Limiting
    rate_limit_free_queries_per_day: int = 5
    rate_limit_authenticated_queries_per_day: int = 20

    # Feature Flags
    enable_legal_drafter: bool = True
    enable_red_flags_detector: bool = True
    enable_litigation_predictor: bool = False
    enable_trend_spotter: bool = False

    @model_validator(mode="after")
    def _reject_insecure_secret_in_production(self) -> "Settings":
        """Refuse to start in production with a forgeable JWT signing key.

        Every access/refresh token is signed with ``secret_key``; with the
        placeholder value anyone can mint an admin token. Failing fast beats
        running exposed.
        """
        if self.environment == "production" and not self.secret_key_is_secure:
            raise ValueError(
                "SECRET_KEY is unset/placeholder or shorter than "
                f"{MIN_SECRET_KEY_LENGTH} characters. Set a random secret "
                "(e.g. `openssl rand -hex 32`) before running in production."
            )
        return self

    @property
    def secret_key_is_secure(self) -> bool:
        """True if the JWT signing key is not a placeholder and is long enough."""
        return (
            self.secret_key not in INSECURE_SECRET_KEYS
            and len(self.secret_key) >= MIN_SECRET_KEY_LENGTH
        )

    @property
    def cors_origin_list(self) -> list[str]:
        """Parsed ``cors_origins`` (empty entries dropped)."""
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def is_production(self) -> bool:
        """Check if running in production."""
        return self.environment == "production"

    @property
    def has_database(self) -> bool:
        """Check if database is configured."""
        return bool(self.database_url) and not self.skip_db

    @property
    def async_database_url(self) -> Optional[str]:
        """Get async database URL for SQLAlchemy."""
        if not self.database_url:
            return None

        url = self.database_url

        # Handle different database types
        if url.startswith("postgresql://"):
            return url.replace("postgresql://", "postgresql+asyncpg://")
        elif url.startswith("sqlite:"):
            # SQLite async requires aiosqlite
            return url.replace("sqlite:", "sqlite+aiosqlite:")
        else:
            return url


@lru_cache
def get_settings() -> Settings:
    """Get cached settings instance."""
    return Settings()
