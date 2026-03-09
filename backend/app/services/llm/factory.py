"""LLM Provider Factory.

Provides functions to get LLM providers:
- get_llm_provider() — create a provider by type (simple, no DB)
- get_active_llm_provider() — read settings from DB, return configured provider
- get_embedding_provider() — always returns GeminiProvider (embeddings stay on Gemini)
- clear_provider_cache() — invalidate cached providers after settings change
"""

from typing import Literal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.logging import get_logger
from app.services.llm.base import LLMProvider

logger = get_logger(__name__)

ProviderType = Literal["gemini", "anthropic", "openai", "groq", "openrouter", "vertex", "ollama"]

_provider_cache: dict[str, LLMProvider] = {}


def clear_provider_cache() -> None:
    """Clear the provider cache (call after settings change)."""
    _provider_cache.clear()
    logger.info("provider_cache_cleared")


def get_llm_provider(
    provider_type: ProviderType | None = None,
    api_key: str | None = None,
    **kwargs,
) -> LLMProvider:
    """Get an LLM provider instance.

    Args:
        provider_type: The type of provider to use. If None, uses the
            first available provider based on configured API keys.
        api_key: Optional API key to use (overrides env var).
        **kwargs: Additional arguments to pass to the provider constructor.

    Returns:
        An LLM provider instance.
    """
    settings = get_settings()

    # Determine provider type if not specified
    if provider_type is None:
        provider_type = _detect_available_provider(settings)

    # When api_key is provided, skip cache (per-request key from DB)
    if api_key:
        provider = _create_provider(provider_type, api_key=api_key, **kwargs)
        logger.info(
            "llm_provider_created",
            provider=provider.provider_name,
            model=provider.model_name,
            cached=False,
        )
        return provider

    # Check cache
    cache_key = f"{provider_type}:{kwargs.get('model', 'default')}"
    if cache_key in _provider_cache:
        return _provider_cache[cache_key]

    # Create provider
    provider = _create_provider(provider_type, **kwargs)
    _provider_cache[cache_key] = provider

    logger.info(
        "llm_provider_created",
        provider=provider.provider_name,
        model=provider.model_name,
        cached=True,
    )

    return provider


async def get_active_llm_provider(session: AsyncSession) -> LLMProvider:
    """Get the active LLM provider based on DB settings.

    Reads LLMSettings from the database, decrypts the relevant API key,
    and returns the configured provider. Falls back to env var-based
    detection if no DB settings exist.

    Args:
        session: Database session for reading settings.

    Returns:
        The active LLM provider.
    """
    from app.core.encryption import decrypt_value
    from app.models.decision import LLMSettings

    try:
        result = await session.execute(
            select(LLMSettings).where(LLMSettings.id == 1)
        )
        settings_row = result.scalar_one_or_none()
    except Exception as e:
        logger.warning("llm_settings_read_failed", error=str(e))
        settings_row = None

    if not settings_row:
        # No DB settings — fall back to env var detection
        return get_llm_provider()

    provider_type = settings_row.active_provider
    model = settings_row.active_model

    # Decrypt the relevant API key
    api_key = None
    if provider_type == "gemini" and settings_row.gemini_api_key_enc:
        api_key = decrypt_value(settings_row.gemini_api_key_enc)
    elif provider_type == "anthropic" and settings_row.anthropic_api_key_enc:
        api_key = decrypt_value(settings_row.anthropic_api_key_enc)
    elif provider_type == "openai" and settings_row.openai_api_key_enc:
        api_key = decrypt_value(settings_row.openai_api_key_enc)
    elif provider_type == "groq" and settings_row.groq_api_key_enc:
        api_key = decrypt_value(settings_row.groq_api_key_enc)
    elif provider_type == "openrouter" and settings_row.openrouter_api_key_enc:
        api_key = decrypt_value(settings_row.openrouter_api_key_enc)

    kwargs = {}
    if model:
        kwargs["model"] = model

    # If no DB key, fall back to env var (api_key=None lets provider use env)
    return get_llm_provider(
        provider_type=provider_type,
        api_key=api_key if api_key else None,
        **kwargs,
    )


def get_embedding_provider(api_key: str | None = None) -> LLMProvider:
    """Get the embedding provider — always Gemini.

    Embeddings stay on Gemini regardless of active chat provider,
    because switching embedding models would require re-generating
    all DB vectors.

    Args:
        api_key: Optional Gemini API key (overrides env var).

    Returns:
        GeminiProvider configured for embeddings.
    """
    cache_key = "embedding:gemini"
    if not api_key and cache_key in _provider_cache:
        return _provider_cache[cache_key]

    from app.services.llm.gemini import GeminiProvider

    provider = GeminiProvider(api_key=api_key) if api_key else GeminiProvider()
    if not api_key:
        _provider_cache[cache_key] = provider
    return provider


def _detect_available_provider(settings) -> ProviderType:
    """Detect which provider is available based on configuration."""
    if settings.gemini_api_key:
        return "gemini"
    if settings.anthropic_api_key:
        return "anthropic"
    if settings.openai_api_key:
        return "openai"
    if settings.groq_api_key:
        return "groq"
    if settings.openrouter_api_key:
        return "openrouter"
    if settings.vertex_ai_project:
        return "vertex"

    raise ValueError(
        "No LLM provider configured. Set at least one of: "
        "GEMINI_API_KEY, ANTHROPIC_API_KEY, OPENAI_API_KEY, GROQ_API_KEY, OPENROUTER_API_KEY, or VERTEX_AI_PROJECT"
    )


def _create_provider(
    provider_type: ProviderType,
    api_key: str | None = None,
    **kwargs,
) -> LLMProvider:
    """Create a provider instance."""
    if provider_type == "gemini":
        from app.services.llm.gemini import GeminiProvider

        return GeminiProvider(api_key=api_key, **kwargs)

    if provider_type == "anthropic":
        from app.services.llm.anthropic import AnthropicProvider

        return AnthropicProvider(api_key=api_key, **kwargs)

    if provider_type == "openai":
        from app.services.llm.openai import OpenAIProvider

        return OpenAIProvider(api_key=api_key, **kwargs)

    if provider_type == "groq":
        from app.services.llm.groq import GroqProvider

        return GroqProvider(api_key=api_key, **kwargs)

    if provider_type == "openrouter":
        from app.services.llm.openrouter import OpenRouterProvider

        return OpenRouterProvider(api_key=api_key, **kwargs)

    if provider_type == "vertex":
        raise NotImplementedError("Vertex AI provider not yet implemented")

    if provider_type == "ollama":
        raise NotImplementedError("Ollama provider not yet implemented")

    raise ValueError(f"Unknown provider type: {provider_type}")
