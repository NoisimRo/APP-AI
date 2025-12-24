"""Application configuration using Pydantic Settings."""

from functools import lru_cache
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
    )

    # Application
    app_name: str = "ExpertAP"
    environment: Literal["development", "staging", "production"] = "development"
    debug: bool = True
    log_level: str = "DEBUG"
    secret_key: str = "change-me-in-production"

    # Database
    database_url: str = "postgresql://expertap:expertap_dev@localhost:5432/expertap"

    # Redis
    redis_url: str = "redis://localhost:6379"

    # LLM Providers
    vertex_ai_project: str = ""
    vertex_ai_location: str = "europe-west1"
    gemini_api_key: str = ""
    openai_api_key: str = ""
    anthropic_api_key: str = ""

    # Embedding
    embedding_provider: Literal["vertex", "openai", "local"] = "vertex"
    embedding_model: str = "text-embedding-004"

    # Rate Limiting
    rate_limit_free_queries_per_day: int = 5
    rate_limit_authenticated_queries_per_day: int = 20

    # Feature Flags
    enable_legal_drafter: bool = True
    enable_red_flags_detector: bool = True
    enable_litigation_predictor: bool = False
    enable_trend_spotter: bool = False

    @property
    def is_production(self) -> bool:
        """Check if running in production."""
        return self.environment == "production"

    @property
    def async_database_url(self) -> str:
        """Get async database URL for SQLAlchemy."""
        return self.database_url.replace("postgresql://", "postgresql+asyncpg://")


@lru_cache
def get_settings() -> Settings:
    """Get cached settings instance."""
    return Settings()
