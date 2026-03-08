"""LLM Settings API endpoints.

Allows viewing, updating, and testing LLM provider configuration.
API keys are encrypted before storage.
"""

import time

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.encryption import encrypt_value, decrypt_value
from app.core.logging import get_logger
from app.db.session import get_session
from app.models.decision import LLMSettings
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
}

# Default model per provider
DEFAULT_MODELS = {
    "gemini": "gemini-3.1-pro-preview",
    "anthropic": "claude-sonnet-4-6",
    "openai": "gpt-5.4-2026-03-05",
}


class ProviderInfo(BaseModel):
    """Info about a provider's configuration status."""
    configured: bool
    models: list[str]


class LLMSettingsResponse(BaseModel):
    """Response for GET /settings/llm."""
    active_provider: str
    active_model: str | None
    providers: dict[str, ProviderInfo]


class LLMSettingsUpdateRequest(BaseModel):
    """Request for PUT /settings/llm."""
    active_provider: str = Field(..., pattern=r"^(gemini|anthropic|openai)$")
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
    return False


async def _get_settings_row(session: AsyncSession) -> LLMSettings | None:
    """Get the single settings row from DB."""
    result = await session.execute(
        select(LLMSettings).where(LLMSettings.id == 1)
    )
    return result.scalar_one_or_none()


@router.get("/llm", response_model=LLMSettingsResponse)
async def get_llm_settings(
    session: AsyncSession = Depends(get_session),
) -> LLMSettingsResponse:
    """Return current LLM settings (without decrypted keys)."""
    settings_row = await _get_settings_row(session)

    active_provider = settings_row.active_provider if settings_row else "gemini"
    active_model = settings_row.active_model if settings_row else None

    providers = {}
    for provider_name, models in PROVIDER_MODELS.items():
        providers[provider_name] = ProviderInfo(
            configured=_is_provider_configured(provider_name, settings_row),
            models=models,
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
) -> LLMSettingsResponse:
    """Update LLM settings — provider, model, and/or API key."""
    logger.info(
        "llm_settings_update",
        provider=request.active_provider,
        model=request.active_model,
        has_key=bool(request.api_key),
    )

    # Validate model is in the provider's list (if specified)
    if request.active_model:
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

    await session.commit()
    await session.refresh(settings_row)

    # Clear provider cache so next request uses new settings
    clear_provider_cache()

    # Return updated settings
    providers = {}
    for provider_name, models in PROVIDER_MODELS.items():
        providers[provider_name] = ProviderInfo(
            configured=_is_provider_configured(provider_name, settings_row),
            models=models,
        )

    return LLMSettingsResponse(
        active_provider=settings_row.active_provider,
        active_model=settings_row.active_model,
        providers=providers,
    )


@router.post("/llm/test", response_model=LLMTestResponse)
async def test_llm_connection(
    session: AsyncSession = Depends(get_session),
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
