"""LLM Settings API endpoints.

Allows viewing, updating, and testing LLM provider configuration.
API keys are encrypted before storage.
OpenRouter models are fetched dynamically from their API.
"""

import time

import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.deps import require_role
from app.core.encryption import encrypt_value, decrypt_value
from app.core.logging import get_logger
from app.db.session import get_session
from app.models.decision import LLMSettings, User
from app.services.llm.factory import (
    clear_provider_cache,
    get_llm_provider,
)

router = APIRouter()
logger = get_logger(__name__)

# Hardcoded model lists per provider
PROVIDER_MODELS = {
    "gemini": [
        "gemini-3.1-pro-preview",
        "gemini-3.1-pro-preview-customtools",
        "gemini-3-flash-preview",
        "gemini-3.1-flash-lite-preview",
        "gemini-2.5-flash",
        "gemini-2.5-pro",
        "gemini-2.5-flash-lite",
    ],
    "anthropic": [
        "claude-opus-4-6",
        "claude-sonnet-4-6",
        "claude-opus-4-5",
        "claude-sonnet-4-5",
    ],
    "openai": [
        "gpt-5.4-2026-03-05",
        "gpt-5-2025-08-07",
        "gpt-5-mini-2025-08-07",
        "gpt-4.1",
        "gpt-4.1-mini",
        "gpt-4.1-nano",
        "gpt-4o",
        "gpt-4o-mini",
        "o3",
        "o3-mini",
        "o4-mini",
    ],
    "groq": [
        "llama-3.3-70b-versatile",
        "llama-3.1-8b-instant",
        "openai/gpt-oss-120b",
        "qwen/qwen3-32b",
        "meta-llama/llama-4-scout-17b-16e-instruct",
    ],
    "openrouter": [
        "openrouter/free",  # Auto-router — always kept first
    ],
}

# Curated fallback list of well-known OpenRouter free models.
# Used when the OpenRouter /models API is unreachable or returns nothing usable,
# so the dropdown is never empty. Kept small and stable on purpose.
OPENROUTER_FALLBACK_MODELS = [
    "openrouter/free",
    "deepseek/deepseek-r1:free",
    "deepseek/deepseek-chat-v3-0324:free",
    "meta-llama/llama-4-maverick:free",
    "meta-llama/llama-4-scout:free",
    "meta-llama/llama-3.3-70b-instruct:free",
    "qwen/qwen3-235b-a22b:free",
    "qwen/qwen-2.5-72b-instruct:free",
    "google/gemma-3-27b-it:free",
    "mistralai/mistral-small-3.2-24b-instruct:free",
]

# Default model per provider
DEFAULT_MODELS = {
    "gemini": "gemini-3.1-pro-preview",
    "anthropic": "claude-sonnet-4-6",
    "openai": "gpt-5.4-2026-03-05",
    "groq": "llama-3.3-70b-versatile",
    "openrouter": "openrouter/free",
}

# Token limits per model: (max_input_tokens, max_output_tokens)
MODEL_TOKEN_LIMITS: dict[str, tuple[int, int]] = {
    # Gemini
    "gemini-3.1-pro-preview": (1_048_576, 65_536),
    "gemini-3.1-pro-preview-customtools": (1_048_576, 65_536),
    "gemini-3-flash-preview": (1_048_576, 65_536),
    "gemini-3.1-flash-lite-preview": (1_048_576, 65_536),
    "gemini-2.5-flash": (1_048_576, 65_536),
    "gemini-2.5-pro": (1_048_576, 65_536),
    "gemini-2.5-flash-lite": (1_048_576, 65_536),
    # Anthropic
    "claude-opus-4-6": (200_000, 32_000),
    "claude-sonnet-4-6": (200_000, 16_000),
    "claude-opus-4-5": (200_000, 32_000),
    "claude-sonnet-4-5": (200_000, 16_000),
    # OpenAI
    "gpt-5.4-2026-03-05": (200_000, 100_000),
    "gpt-5-2025-08-07": (128_000, 32_768),
    "gpt-5-mini-2025-08-07": (128_000, 16_384),
    "gpt-4.1": (1_047_576, 32_768),
    "gpt-4.1-mini": (1_047_576, 32_768),
    "gpt-4.1-nano": (1_047_576, 32_768),
    "gpt-4o": (128_000, 16_384),
    "gpt-4o-mini": (128_000, 16_384),
    "o3": (200_000, 100_000),
    "o3-mini": (200_000, 100_000),
    "o4-mini": (200_000, 100_000),
    # Groq (free tier TPM limits — much lower than model max)
    "llama-3.3-70b-versatile": (8_000, 4_096),
    "llama-3.1-8b-instant": (3_500, 4_096),
    "openai/gpt-oss-120b": (5_000, 4_096),
    "qwen/qwen3-32b": (3_500, 4_096),
    "meta-llama/llama-4-scout-17b-16e-instruct": (5_000, 8_192),
    # OpenRouter
    "openrouter/free": (10_000, 4_096),
}

# Cache for dynamically fetched OpenRouter models
_openrouter_models_cache: list[str] | None = None
_openrouter_limits_cache: dict[str, tuple[int, int]] = {}
_openrouter_cache_time: float = 0
OPENROUTER_CACHE_TTL = 3600  # 1 hour
OPENROUTER_MODELS_URL = "https://openrouter.ai/api/v1/models"

# Fallback token budget for a free model whose context_length we cannot read.
OPENROUTER_DEFAULT_LIMITS = (12_000, 4_096)


def _price_is_zero(value: object) -> bool:
    """True if an OpenRouter pricing entry means "no cost".

    OpenRouter returns prices as strings, but the exact formatting is not
    contractual — "0", "0.0", "0E-9" and 0 (numeric) have all been observed.
    Comparing against the literal string "0" silently drops every free model
    the moment the formatting changes, so parse numerically instead.
    A missing field is treated as "not free" (conservative).
    """
    if value is None:
        return False
    try:
        return float(value) == 0.0
    except (TypeError, ValueError):
        return False


async def _fetch_openrouter_free_models() -> list[str]:
    """Fetch available free models from OpenRouter API. Cached for 1 hour.

    Populates ``_openrouter_limits_cache`` with the real context window and
    max output tokens reported by OpenRouter for each model, so the UI can
    show token budgets for dynamically discovered models.
    """
    global _openrouter_models_cache, _openrouter_cache_time

    now = time.monotonic()
    if _openrouter_models_cache is not None and (now - _openrouter_cache_time) < OPENROUTER_CACHE_TTL:
        return _openrouter_models_cache

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(
                OPENROUTER_MODELS_URL,
                headers={"Accept": "application/json"},
            )
            resp.raise_for_status()
            data = resp.json()

        entries = data.get("data") or []
        if not isinstance(entries, list):
            raise ValueError(f"unexpected payload shape: {type(entries).__name__}")

        free_models: list[str] = []
        limits: dict[str, tuple[int, int]] = {}

        for model in entries:
            if not isinstance(model, dict):
                continue
            model_id = model.get("id") or ""
            if not model_id:
                continue

            pricing = model.get("pricing") or {}
            # Two independent signals — OpenRouter marks free models with a
            # ":free" id suffix and with zero prompt/completion pricing.
            # Accept either, so a change to one does not empty the list.
            is_free_by_suffix = model_id.endswith(":free")
            is_free_by_price = _price_is_zero(pricing.get("prompt")) and _price_is_zero(
                pricing.get("completion")
            )
            if not (is_free_by_suffix or is_free_by_price):
                continue

            free_models.append(model_id)

            context_length = model.get("context_length")
            top_provider = model.get("top_provider") or {}
            max_completion = top_provider.get("max_completion_tokens")
            try:
                max_in = int(context_length) if context_length else OPENROUTER_DEFAULT_LIMITS[0]
            except (TypeError, ValueError):
                max_in = OPENROUTER_DEFAULT_LIMITS[0]
            try:
                max_out = int(max_completion) if max_completion else OPENROUTER_DEFAULT_LIMITS[1]
            except (TypeError, ValueError):
                max_out = OPENROUTER_DEFAULT_LIMITS[1]
            limits[model_id] = (max_in, max_out)

        if not free_models:
            raise ValueError(f"no free models found in {len(entries)} entries")

        # Sort: prioritize well-known providers
        priority_prefixes = ["deepseek/", "meta-llama/", "qwen/", "google/", "mistralai/", "nvidia/"]
        def sort_key(m: str) -> tuple:
            for i, prefix in enumerate(priority_prefixes):
                if m.startswith(prefix):
                    return (i, m)
            return (len(priority_prefixes), m)

        free_models.sort(key=sort_key)

        # Always include openrouter/free at the start
        result = ["openrouter/free"] + [m for m in free_models if m != "openrouter/free"]

        _openrouter_models_cache = result
        _openrouter_limits_cache.clear()
        _openrouter_limits_cache.update(limits)
        _openrouter_cache_time = now
        logger.info("openrouter_models_fetched", count=len(result), total_seen=len(entries))
        return result

    except httpx.HTTPStatusError as e:
        logger.warning(
            "openrouter_models_fetch_failed",
            error=str(e),
            status_code=e.response.status_code,
            body=e.response.text[:300],
        )
    except Exception as e:
        logger.warning(
            "openrouter_models_fetch_failed",
            error=str(e),
            error_type=type(e).__name__,
        )

    # Serve the last good list if we have one, otherwise the curated fallback
    # so the dropdown is never reduced to a single entry.
    if _openrouter_models_cache:
        return _openrouter_models_cache
    return list(OPENROUTER_FALLBACK_MODELS)


class ModelInfo(BaseModel):
    """Info about a single model."""
    id: str
    input_tokens: int
    output_tokens: int


class ProviderInfo(BaseModel):
    """Info about a provider's configuration status."""
    configured: bool
    models: list[ModelInfo]


class LLMSettingsResponse(BaseModel):
    """Response for GET /settings/llm."""
    active_provider: str
    active_model: str | None
    providers: dict[str, ProviderInfo]


class LLMSettingsUpdateRequest(BaseModel):
    """Request for PUT /settings/llm."""
    active_provider: str = Field(..., pattern=r"^(gemini|anthropic|openai|groq|openrouter)$")
    active_model: str | None = None
    api_key: str | None = Field(None, description="API key for the provider (will be encrypted)")


class LLMTestResponse(BaseModel):
    """Response for POST /settings/llm/test."""
    success: bool
    provider: str
    model: str
    response_time_ms: int
    error: str | None = None


def _is_provider_configured(provider: str, settings_row: LLMSettings | None) -> bool:
    """Check if a provider has an API key (DB or env var)."""
    app_settings = get_settings()

    if provider == "gemini":
        has_env = bool(app_settings.gemini_api_key)
        has_db = bool(settings_row and settings_row.gemini_api_key_enc)
        return has_env or has_db
    elif provider == "anthropic":
        has_env = bool(app_settings.anthropic_api_key)
        has_db = bool(settings_row and settings_row.anthropic_api_key_enc)
        return has_env or has_db
    elif provider == "openai":
        has_env = bool(app_settings.openai_api_key)
        has_db = bool(settings_row and settings_row.openai_api_key_enc)
        return has_env or has_db
    elif provider == "groq":
        has_env = bool(app_settings.groq_api_key)
        has_db = bool(settings_row and settings_row.groq_api_key_enc)
        return has_env or has_db
    elif provider == "openrouter":
        has_env = bool(app_settings.openrouter_api_key)
        has_db = bool(settings_row and settings_row.openrouter_api_key_enc)
        return has_env or has_db
    return False


def _build_model_list(model_ids: list[str]) -> list[ModelInfo]:
    """Build sorted ModelInfo list from model IDs (largest input first).

    Token limits come from the static ``MODEL_TOKEN_LIMITS`` table first, then
    from the limits OpenRouter reported for dynamically discovered models.
    """
    models = []
    for mid in model_ids:
        limits = MODEL_TOKEN_LIMITS.get(mid) or _openrouter_limits_cache.get(mid, (0, 0))
        models.append(ModelInfo(id=mid, input_tokens=limits[0], output_tokens=limits[1]))
    models.sort(key=lambda m: m.input_tokens, reverse=True)
    return models


async def _get_settings_row(session: AsyncSession) -> LLMSettings | None:
    """Get the single settings row from DB."""
    result = await session.execute(
        select(LLMSettings).where(LLMSettings.id == 1)
    )
    return result.scalar_one_or_none()


@router.get("/llm", response_model=LLMSettingsResponse)
async def get_llm_settings(
    session: AsyncSession = Depends(get_session),
    _admin: User = Depends(require_role("admin")),
) -> LLMSettingsResponse:
    """Return current LLM settings (without decrypted keys)."""
    settings_row = await _get_settings_row(session)

    active_provider = settings_row.active_provider if settings_row else "gemini"
    active_model = settings_row.active_model if settings_row else None

    # Fetch dynamic OpenRouter model list
    openrouter_models = await _fetch_openrouter_free_models()

    providers = {}
    for provider_name, model_ids in PROVIDER_MODELS.items():
        raw_ids = openrouter_models if provider_name == "openrouter" else model_ids
        providers[provider_name] = ProviderInfo(
            configured=_is_provider_configured(provider_name, settings_row),
            models=_build_model_list(raw_ids),
        )

    return LLMSettingsResponse(
        active_provider=active_provider,
        active_model=active_model,
        providers=providers,
    )


@router.put("/llm", response_model=LLMSettingsResponse)
async def update_llm_settings(
    request: LLMSettingsUpdateRequest,
    session: AsyncSession = Depends(get_session),
    _admin: User = Depends(require_role("admin")),
) -> LLMSettingsResponse:
    """Update LLM settings — provider, model, and/or API key."""
    logger.info(
        "llm_settings_update",
        provider=request.active_provider,
        model=request.active_model,
        has_key=bool(request.api_key),
    )

    # Validate model is in the provider's list (skip for OpenRouter — models are dynamic)
    if request.active_model and request.active_provider != "openrouter":
        valid_models = PROVIDER_MODELS.get(request.active_provider, [])
        if valid_models and request.active_model not in valid_models:
            raise HTTPException(
                status_code=400,
                detail=f"Model '{request.active_model}' nu este disponibil pentru {request.active_provider}. "
                f"Modele valide: {', '.join(valid_models)}",
            )

    settings_row = await _get_settings_row(session)

    if not settings_row:
        # Create the settings row if it doesn't exist
        settings_row = LLMSettings(id=1)
        session.add(settings_row)

    settings_row.active_provider = request.active_provider
    settings_row.active_model = request.active_model

    # Encrypt and store API key if provided
    if request.api_key:
        encrypted = encrypt_value(request.api_key)
        if request.active_provider == "gemini":
            settings_row.gemini_api_key_enc = encrypted
        elif request.active_provider == "anthropic":
            settings_row.anthropic_api_key_enc = encrypted
        elif request.active_provider == "openai":
            settings_row.openai_api_key_enc = encrypted
        elif request.active_provider == "groq":
            settings_row.groq_api_key_enc = encrypted
        elif request.active_provider == "openrouter":
            settings_row.openrouter_api_key_enc = encrypted

    await session.commit()
    await session.refresh(settings_row)

    # Clear provider cache so next request uses new settings
    clear_provider_cache()

    # Return updated settings (served from the 1h cache when warm)
    openrouter_models = await _fetch_openrouter_free_models()
    providers = {}
    for provider_name, model_ids in PROVIDER_MODELS.items():
        raw_ids = openrouter_models if provider_name == "openrouter" else model_ids
        providers[provider_name] = ProviderInfo(
            configured=_is_provider_configured(provider_name, settings_row),
            models=_build_model_list(raw_ids),
        )

    return LLMSettingsResponse(
        active_provider=settings_row.active_provider,
        active_model=settings_row.active_model,
        providers=providers,
    )


@router.post("/llm/test", response_model=LLMTestResponse)
async def test_llm_connection(
    session: AsyncSession = Depends(get_session),
    _admin: User = Depends(require_role("admin")),
) -> LLMTestResponse:
    """Test the currently configured LLM provider with a simple completion."""
    settings_row = await _get_settings_row(session)

    provider_type = settings_row.active_provider if settings_row else "gemini"
    model = settings_row.active_model if settings_row else None

    # Get the API key
    api_key = None
    if settings_row:
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

    try:
        llm = get_llm_provider(
            provider_type=provider_type,
            api_key=api_key if api_key else None,
            **kwargs,
        )

        start = time.monotonic()
        response = await llm.complete(
            prompt="Răspunde cu un singur cuvânt: care este capitala României?",
            temperature=0.1,
            max_tokens=100,
        )
        elapsed_ms = int((time.monotonic() - start) * 1000)

        logger.info(
            "llm_test_success",
            provider=provider_type,
            model=llm.model_name,
            response_time_ms=elapsed_ms,
            response=response[:50],
        )

        return LLMTestResponse(
            success=True,
            provider=provider_type,
            model=llm.model_name,
            response_time_ms=elapsed_ms,
        )

    except Exception as e:
        error_msg = str(e)

        # Provide friendlier error messages for common issues
        if "credit balance is too low" in error_msg:
            error_msg = (
                "Balanța de credite Anthropic este insuficientă. "
                "Verificați la console.anthropic.com/settings/billing că aveți credite active "
                "și că cheia API aparține organizației corecte."
            )
        elif "invalid x-api-key" in error_msg.lower() or "invalid api key" in error_msg.lower():
            error_msg = "Cheia API este invalidă. Verificați că ați copiat cheia corect."
        elif "insufficient_quota" in error_msg or "exceeded your current quota" in error_msg:
            error_msg = (
                "Cota OpenAI depășită. Verificați la platform.openai.com/usage "
                "că aveți credite disponibile."
            )
        elif "NoneType" in error_msg and "subscriptable" in error_msg:
            error_msg = (
                "Gemini a returnat un răspuns gol. Încercați alt model sau verificați cheia API."
            )

        logger.error("llm_test_failed", provider=provider_type, error=str(e))
        return LLMTestResponse(
            success=False,
            provider=provider_type,
            model=model or DEFAULT_MODELS.get(provider_type, "unknown"),
            response_time_ms=0,
            error=error_msg,
        )
