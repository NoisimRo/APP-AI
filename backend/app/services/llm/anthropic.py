"""Anthropic Claude LLM Provider."""

import hashlib
from typing import AsyncIterator

import anthropic

from app.core.logging import get_logger
from app.services.llm.anthropic_models import (
    get_known_replacement,
    get_model_info,
    list_claude_models,
    model_version,
    resolve_replacement,
)
from app.services.llm.base import LLMProvider, ResourceExhaustedError

logger = get_logger(__name__)

# Fallback server-side la refuz (beta). Cu valoarea "default", un model care
# nu suportă fallback rulează cererea normal, deci e sigur de trimis pe orice
# model din generația 5+ — inclusiv pe modele viitoare, fără modificări de cod.
_FALLBACK_BETA = "server-side-fallback-2026-07-01"
_FALLBACK_MIN_GENERATION = 5

# Thinking-ul consumă din max_tokens; sub acest prag un răspuns poate ieși gol.
_MIN_MAX_TOKENS = 2048


def _supports_fallback(model: str) -> bool:
    return model_version(model)[0] >= _FALLBACK_MIN_GENERATION


def _extract_text(content: list) -> str:
    """Concatenează blocurile text (ignoră thinking/fallback blocks)."""
    return "".join(b.text for b in content if getattr(b, "type", None) == "text")


class AnthropicProvider(LLMProvider):
    """Anthropic Claude provider.

    Supports text generation via Claude models.
    Does NOT support embeddings — embeddings always use Gemini.
    """

    def __init__(
        self,
        model: str = "claude-opus-5-5",
        api_key: str | None = None,
    ):
        self._configured_model = model
        # Dacă modelul a fost deja detectat ca retras, folosește înlocuitorul.
        self._model_name = get_known_replacement(model) or model
        self._client = anthropic.AsyncAnthropic(
            api_key=api_key,  # Falls back to ANTHROPIC_API_KEY env var if None
        )
        self._cache_key = (
            hashlib.sha256(api_key.encode()).hexdigest()[:16] if api_key else "env"
        )

        logger.info("anthropic_provider_initialized", model=model)

    @property
    def provider_name(self) -> str:
        return "anthropic"

    @property
    def model_name(self) -> str:
        return self._model_name

    def _build_messages(
        self,
        prompt: str,
        context: list[str] | None,
    ) -> list[dict]:
        """Build Claude messages array from prompt and context."""
        parts = []

        if context:
            parts.append("<context>")
            for i, ctx in enumerate(context, 1):
                parts.append(f"\n[Document {i}]\n{ctx}\n")
            parts.append("</context>\n")

        parts.append(prompt)

        return [{"role": "user", "content": "\n".join(parts)}]

    def _build_kwargs(
        self,
        messages: list[dict],
        system_prompt: str | None,
        max_tokens: int,
    ) -> dict:
        """Parametrii cererii Messages API.

        `temperature` NU se trimite: SDK-ul anthropic 1.x+ l-a eliminat din
        semnătură (TypeError), iar modelele Claude 4.7+ îl resping cu 400.
        Parametrul rămâne în semnătura provider-ului doar pentru compatibilitate
        cu interfața `LLMProvider`.
        """
        max_tokens = max(max_tokens, _MIN_MAX_TOKENS)
        # Nu cere mai mult decât permite modelul (limită raportată de Models API).
        info = get_model_info(self._model_name)
        if info and info.output_tokens:
            max_tokens = min(max_tokens, info.output_tokens)
        kwargs: dict = {
            "model": self._model_name,
            "messages": messages,
            "max_tokens": max_tokens,
        }
        if system_prompt:
            kwargs["system"] = system_prompt
        if _supports_fallback(self._model_name):
            # La refuz, API-ul reia automat cererea pe un model de rezervă.
            kwargs["betas"] = [_FALLBACK_BETA]
            kwargs["fallbacks"] = "default"
        return kwargs

    async def _ensure_model_metadata(self) -> None:
        """Încarcă (cache 1h) limitele modelelor din Models API; eșecul nu blochează."""
        if get_model_info(self._model_name) is None:
            await list_claude_models(self._client, self._cache_key)

    async def _switch_to_replacement(self) -> bool:
        """La 404 (model retras), trece pe cel mai nou model din aceeași familie."""
        replacement = await resolve_replacement(
            self._model_name, self._client, self._cache_key
        )
        if not replacement or replacement == self._model_name:
            return False
        logger.warning(
            "anthropic_model_unavailable_switching",
            configured=self._configured_model,
            missing=self._model_name,
            replacement=replacement,
        )
        self._model_name = replacement
        return True

    def _stream_ctx(self, kwargs: dict):
        if _supports_fallback(self._model_name):
            return self._client.beta.messages.stream(**kwargs)
        return self._client.messages.stream(**kwargs)

    async def complete(
        self,
        prompt: str,
        context: list[str] | None = None,
        system_prompt: str | None = None,
        temperature: float = 0.1,
        max_tokens: int = 4096,
    ) -> str:
        """Generate a completion using Claude."""
        messages = self._build_messages(prompt, context)

        try:
            await self._ensure_model_metadata()
            # Streaming + get_final_message: SDK-ul refuză cererile non-streaming
            # cu max_tokens mare (>~21K), iar modelele noi acceptă până la 128K.
            try:
                kwargs = self._build_kwargs(messages, system_prompt, max_tokens)
                async with self._stream_ctx(kwargs) as stream:
                    response = await stream.get_final_message()
            except anthropic.NotFoundError:
                if not await self._switch_to_replacement():
                    raise
                kwargs = self._build_kwargs(messages, system_prompt, max_tokens)
                async with self._stream_ctx(kwargs) as stream:
                    response = await stream.get_final_message()

            if response.stop_reason == "refusal":
                raise RuntimeError(
                    "Modelul Claude a refuzat cererea (filtru de siguranță)."
                )

            return _extract_text(response.content)

        except anthropic.RateLimitError as e:
            raise ResourceExhaustedError("anthropic", str(e)) from e
        except Exception as e:
            logger.error("anthropic_completion_error", error=str(e))
            raise

    async def stream(
        self,
        prompt: str,
        context: list[str] | None = None,
        system_prompt: str | None = None,
        temperature: float = 0.1,
        max_tokens: int = 4096,
    ) -> AsyncIterator[str]:
        """Stream a completion using Claude."""
        messages = self._build_messages(prompt, context)

        try:
            await self._ensure_model_metadata()
            for attempt in range(2):
                kwargs = self._build_kwargs(messages, system_prompt, max_tokens)
                try:
                    async with self._stream_ctx(kwargs) as stream:
                        async for text in stream.text_stream:
                            yield text
                    return
                except anthropic.NotFoundError:
                    # 404 vine la deschiderea stream-ului, înainte de orice text.
                    if attempt or not await self._switch_to_replacement():
                        raise

        except anthropic.RateLimitError as e:
            raise ResourceExhaustedError("anthropic", str(e)) from e
        except Exception as e:
            logger.error("anthropic_stream_error", error=str(e))
            raise

    async def embed(
        self,
        texts: list[str],
        task_type: str = "RETRIEVAL_DOCUMENT",
    ) -> list[list[float]]:
        """Not supported — embeddings always use Gemini."""
        raise NotImplementedError(
            "Anthropic does not provide an embedding API. "
            "Use GeminiProvider for embeddings."
        )
