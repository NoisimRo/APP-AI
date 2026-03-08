"""Anthropic Claude LLM Provider."""

from typing import AsyncIterator

import anthropic

from app.core.logging import get_logger
from app.services.llm.base import LLMProvider

logger = get_logger(__name__)


class AnthropicProvider(LLMProvider):
    """Anthropic Claude provider.

    Supports text generation via Claude models.
    Does NOT support embeddings — embeddings always use Gemini.
    """

    def __init__(
        self,
        model: str = "claude-sonnet-4-6",
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
            kwargs = {
                "model": self._model_name,
                "messages": messages,
                "max_tokens": max_tokens,
                "temperature": temperature,
            }
            if system_prompt:
                kwargs["system"] = system_prompt

            response = await self._client.messages.create(**kwargs)

            return response.content[0].text

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
            kwargs = {
                "model": self._model_name,
                "messages": messages,
                "max_tokens": max_tokens,
                "temperature": temperature,
            }
            if system_prompt:
                kwargs["system"] = system_prompt

            async with self._client.messages.stream(**kwargs) as stream:
                async for text in stream.text_stream:
                    yield text

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
