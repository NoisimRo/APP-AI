"""LLM Provider Factory."""

from typing import Literal

from app.core.config import get_settings
from app.core.logging import get_logger
from app.services.llm.base import LLMProvider

logger = get_logger(__name__)

ProviderType = Literal["gemini", "openai", "anthropic", "vertex", "ollama"]

_provider_cache: dict[str, LLMProvider] = {}


def get_llm_provider(
    provider_type: ProviderType | None = None,
    **kwargs,
) -> LLMProvider:
    """Get an LLM provider instance.

    Args:
        provider_type: The type of provider to use. If None, uses the
            first available provider based on configured API keys.
        **kwargs: Additional arguments to pass to the provider constructor.

    Returns:
        An LLM provider instance.

    Raises:
        ValueError: If no provider is available or the requested provider
            is not configured.
    """
    settings = get_settings()

    # Determine provider type if not specified
    if provider_type is None:
        provider_type = _detect_available_provider(settings)

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
    )

    return provider


def _detect_available_provider(settings) -> ProviderType:
    """Detect which provider is available based on configuration."""
    if settings.gemini_api_key:
        return "gemini"
    if settings.openai_api_key:
        return "openai"
    if settings.anthropic_api_key:
        return "anthropic"
    if settings.vertex_ai_project:
        return "vertex"

    raise ValueError(
        "No LLM provider configured. Set at least one of: "
        "GEMINI_API_KEY, OPENAI_API_KEY, ANTHROPIC_API_KEY, or VERTEX_AI_PROJECT"
    )


def _create_provider(provider_type: ProviderType, **kwargs) -> LLMProvider:
    """Create a provider instance."""
    if provider_type == "gemini":
        from app.services.llm.gemini import GeminiProvider

        return GeminiProvider(**kwargs)

    if provider_type == "openai":
        # TODO: Implement OpenAI provider
        raise NotImplementedError("OpenAI provider not yet implemented")

    if provider_type == "anthropic":
        # TODO: Implement Anthropic provider
        raise NotImplementedError("Anthropic provider not yet implemented")

    if provider_type == "vertex":
        # TODO: Implement Vertex AI provider
        raise NotImplementedError("Vertex AI provider not yet implemented")

    if provider_type == "ollama":
        # TODO: Implement Ollama provider
        raise NotImplementedError("Ollama provider not yet implemented")

    raise ValueError(f"Unknown provider type: {provider_type}")
