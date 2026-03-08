"""Google Gemini LLM Provider (via Google AI Studio)."""

from typing import AsyncIterator

from google import genai
from google.genai import types

from app.core.config import get_settings
from app.core.logging import get_logger
from app.services.llm.base import LLMProvider

logger = get_logger(__name__)


class GeminiProvider(LLMProvider):
    """Google Gemini provider using Google AI Studio API.

    This provider is suitable for development and smaller deployments.
    For production on GCP, consider using VertexAIProvider.
    """

    def __init__(
        self,
        model: str = "gemini-3.1-pro-preview",
        embedding_model: str = "gemini-embedding-001",
        api_key: str | None = None,
    ):
        self._model_name = model
        self._embedding_model = embedding_model

        key = api_key or get_settings().gemini_api_key
        if not key:
            raise ValueError("GEMINI_API_KEY is required for GeminiProvider")

        self._client = genai.Client(api_key=key)

        logger.info(
            "gemini_provider_initialized",
            model=model,
            embedding_model=embedding_model,
        )

    @property
    def provider_name(self) -> str:
        return "gemini"

    @property
    def model_name(self) -> str:
        return self._model_name

    async def complete(
        self,
        prompt: str,
        context: list[str] | None = None,
        system_prompt: str | None = None,
        temperature: float = 0.1,
        max_tokens: int = 4096,
    ) -> str:
        """Generate a completion using Gemini."""
        full_prompt = self._build_prompt(prompt, context, system_prompt)

        try:
            response = await self._client.aio.models.generate_content(
                model=self._model_name,
                contents=full_prompt,
                config=types.GenerateContentConfig(
                    temperature=temperature,
                    max_output_tokens=max_tokens,
                ),
            )

            # Handle empty/blocked responses (safety filters, empty candidates)
            if not response.candidates:
                block_reason = getattr(response, "prompt_feedback", None)
                raise ValueError(
                    f"Gemini a returnat un răspuns gol (fără candidați). "
                    f"Motiv posibil: filtru de siguranță. Feedback: {block_reason}"
                )

            text = response.text
            if text is None:
                raise ValueError(
                    "Gemini a returnat un răspuns gol (text=None). "
                    "Verificați modelul și parametrii."
                )
            return text

        except Exception as e:
            logger.error("gemini_completion_error", error=str(e))
            raise

    async def stream(
        self,
        prompt: str,
        context: list[str] | None = None,
        system_prompt: str | None = None,
        temperature: float = 0.1,
        max_tokens: int = 4096,
    ) -> AsyncIterator[str]:
        """Stream a completion using Gemini."""
        full_prompt = self._build_prompt(prompt, context, system_prompt)

        try:
            async for chunk in await self._client.aio.models.generate_content_stream(
                model=self._model_name,
                contents=full_prompt,
                config=types.GenerateContentConfig(
                    temperature=temperature,
                    max_output_tokens=max_tokens,
                ),
            ):
                if chunk.text:
                    yield chunk.text

        except Exception as e:
            logger.error("gemini_stream_error", error=str(e))
            raise

    async def embed(
        self,
        texts: list[str],
        task_type: str = "RETRIEVAL_DOCUMENT",
    ) -> list[list[float]]:
        """Generate embeddings using Gemini embedding model.

        Returns 2000-dimensional vectors (capped from 3072 native output
        to fit pgvector HNSW index limit of 2000 dimensions).
        """
        try:
            result = await self._client.aio.models.embed_content(
                model=self._embedding_model,
                contents=texts,
                config=types.EmbedContentConfig(
                    task_type=task_type,
                    output_dimensionality=2000,
                ),
            )
            return [e.values for e in result.embeddings]

        except Exception as e:
            logger.error("gemini_embed_error", error=str(e))
            raise

    def _build_prompt(
        self,
        prompt: str,
        context: list[str] | None,
        system_prompt: str | None,
    ) -> str:
        """Build the full prompt with context and system instructions."""
        parts = []

        if system_prompt:
            parts.append(f"<system>\n{system_prompt}\n</system>\n")

        if context:
            parts.append("<context>")
            for i, ctx in enumerate(context, 1):
                parts.append(f"\n[Document {i}]\n{ctx}\n")
            parts.append("</context>\n")

        parts.append(f"<query>\n{prompt}\n</query>")

        return "\n".join(parts)
