"""OpenAI LLM Provider."""

from typing import AsyncIterator

import openai

from app.core.config import get_settings
from app.core.logging import get_logger
from app.services.llm.base import LLMProvider

logger = get_logger(__name__)


class OpenAIProvider(LLMProvider):
    """OpenAI provider (GPT-4o, GPT-4.1, o3, etc.).

    Supports text generation via OpenAI chat completions API.
    Does NOT support embeddings — embeddings always use Gemini.
    """

    def __init__(
        self,
        model: str = "gpt-4o",
        api_key: str | None = None,
    ):
        self._model_name = model

        key = api_key or get_settings().openai_api_key
        if not key:
            raise ValueError(
                "OPENAI_API_KEY is required for OpenAIProvider. "
                "Set it as env var or configure in Setări LLM."
            )

        self._client = openai.AsyncOpenAI(api_key=key)

        logger.info("openai_provider_initialized", model=model)

    @property
    def provider_name(self) -> str:
        return "openai"

    @property
    def model_name(self) -> str:
        return self._model_name

    def _build_messages(
        self,
        prompt: str,
        context: list[str] | None,
        system_prompt: str | None,
    ) -> list[dict]:
        """Build OpenAI messages array from prompt and context."""
        messages = []

        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})

        parts = []
        if context:
            parts.append("<context>")
            for i, ctx in enumerate(context, 1):
                parts.append(f"\n[Document {i}]\n{ctx}\n")
            parts.append("</context>\n")

        parts.append(prompt)

        messages.append({"role": "user", "content": "\n".join(parts)})
        return messages

    async def complete(
        self,
        prompt: str,
        context: list[str] | None = None,
        system_prompt: str | None = None,
        temperature: float = 0.1,
        max_tokens: int = 4096,
    ) -> str:
        """Generate a completion using OpenAI."""
        messages = self._build_messages(prompt, context, system_prompt)

        try:
            response = await self._client.chat.completions.create(
                model=self._model_name,
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens,
            )

            content = response.choices[0].message.content
            if content is None:
                raise ValueError(
                    "OpenAI a returnat un răspuns gol (content=None). "
                    "Verificați modelul și parametrii."
                )
            return content

        except Exception as e:
            logger.error("openai_completion_error", error=str(e))
            raise

    async def stream(
        self,
        prompt: str,
        context: list[str] | None = None,
        system_prompt: str | None = None,
        temperature: float = 0.1,
        max_tokens: int = 4096,
    ) -> AsyncIterator[str]:
        """Stream a completion using OpenAI."""
        messages = self._build_messages(prompt, context, system_prompt)

        try:
            stream = await self._client.chat.completions.create(
                model=self._model_name,
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens,
                stream=True,
            )

            async for chunk in stream:
                delta = chunk.choices[0].delta
                if delta.content:
                    yield delta.content

        except Exception as e:
            logger.error("openai_stream_error", error=str(e))
            raise

    async def embed(
        self,
        texts: list[str],
        task_type: str = "RETRIEVAL_DOCUMENT",
    ) -> list[list[float]]:
        """Not supported — embeddings always use Gemini."""
        raise NotImplementedError(
            "OpenAI embeddings are not used in this project. "
            "Use GeminiProvider for embeddings."
        )
