"""Google Gemini LLM Provider (via Google AI Studio)."""

from typing import AsyncIterator

import google.generativeai as genai

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
        model: str = "gemini-3-flash-preview",
        embedding_model: str = "text-embedding-004",
    ):
        self._model_name = model
        self._embedding_model = embedding_model

        settings = get_settings()
        if not settings.gemini_api_key:
            raise ValueError("GEMINI_API_KEY is required for GeminiProvider")

        genai.configure(api_key=settings.gemini_api_key)
        self._model = genai.GenerativeModel(model)

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
        # Build the full prompt with context
        full_prompt = self._build_prompt(prompt, context, system_prompt)

        try:
            response = await self._model.generate_content_async(
                full_prompt,
                generation_config=genai.types.GenerationConfig(
                    temperature=temperature,
                    max_output_tokens=max_tokens,
                ),
            )
            return response.text

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
            response = await self._model.generate_content_async(
                full_prompt,
                generation_config=genai.types.GenerationConfig(
                    temperature=temperature,
                    max_output_tokens=max_tokens,
                ),
                stream=True,
            )

            async for chunk in response:
                if chunk.text:
                    yield chunk.text

        except Exception as e:
            logger.error("gemini_stream_error", error=str(e))
            raise

    async def embed(self, texts: list[str]) -> list[list[float]]:
        """Generate embeddings using Gemini embedding model."""
        try:
            embeddings = []
            for text in texts:
                result = genai.embed_content(
                    model=f"models/{self._embedding_model}",
                    content=text,
                    task_type="retrieval_document",
                )
                embeddings.append(result["embedding"])

            return embeddings

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
