"""Anthropic Claude LLM Provider."""

from typing import AsyncIterator

import anthropic

from app.core.logging import get_logger
from app.services.llm.base import LLMProvider, ResourceExhaustedError

logger = get_logger(__name__)

# Modele care rulează mereu cu adaptive thinking (nu acceptă temperature și nici
# dezactivarea thinking-ului) și care suportă fallback server-side la refuz.
_FALLBACK_MODEL_PREFIXES = (
    "claude-fable-5",
    "claude-opus-5",
    "claude-sonnet-5-5",
)
_FALLBACK_BETA = "server-side-fallback-2026-07-01"

# Thinking-ul consumă din max_tokens; sub acest prag un răspuns poate ieși gol.
_MIN_MAX_TOKENS = 2048


def _supports_fallback(model: str) -> bool:
    return model.startswith(_FALLBACK_MODEL_PREFIXES)


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
        self._model_name = model
        self._client = anthropic.AsyncAnthropic(
            api_key=api_key,  # Falls back to ANTHROPIC_API_KEY env var if None
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
        kwargs: dict = {
            "model": self._model_name,
            "messages": messages,
            "max_tokens": max(max_tokens, _MIN_MAX_TOKENS),
        }
        if system_prompt:
            kwargs["system"] = system_prompt
        if _supports_fallback(self._model_name):
            # La refuz, API-ul reia automat cererea pe un model de rezervă.
            kwargs["betas"] = [_FALLBACK_BETA]
            kwargs["fallbacks"] = "default"
        return kwargs

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
            kwargs = self._build_kwargs(messages, system_prompt, max_tokens)

            # Streaming + get_final_message: SDK-ul refuză cererile non-streaming
            # cu max_tokens mare (>~21K), iar modelele noi acceptă până la 128K.
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
            kwargs = self._build_kwargs(messages, system_prompt, max_tokens)

            async with self._stream_ctx(kwargs) as stream:
                async for text in stream.text_stream:
                    yield text

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
