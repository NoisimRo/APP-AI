"""Google Gemini LLM Provider (via Google AI Studio)."""

from typing import AsyncIterator

from google import genai
from google.genai import types

from app.core.config import get_settings
from app.core.logging import get_logger
from app.services.llm.base import LLMProvider, ResourceExhaustedError

logger = get_logger(__name__)


class GeminiProvider(LLMProvider):
    """Google Gemini provider using Google AI Studio API.

    This provider is suitable for development and smaller deployments.
    For production on GCP, consider using VertexAIProvider.
    """

    # Thinking models share max_output_tokens between thinking + visible output.
    # We need a minimum floor so short requests (50-500 tokens) don't get truncated.
    THINKING_MODEL_PREFIXES = ("gemini-2.5", "gemini-3")
    THINKING_MIN_OUTPUT_TOKENS = 4096

    def __init__(
        self,
        model: str = "gemini-3.1-pro-preview",
        embedding_model: str = "gemini-embedding-001",
        api_key: str | None = None,
    ):
        self._model_name = model
        self._embedding_model = embedding_model
        self._is_thinking_model = any(
            model.startswith(p) for p in self.THINKING_MODEL_PREFIXES
        )

        key = api_key or get_settings().gemini_api_key
        if not key:
            raise ValueError("GEMINI_API_KEY is required for GeminiProvider")

        self._client = genai.Client(api_key=key)

        logger.info(
            "gemini_provider_initialized",
            model=model,
            embedding_model=embedding_model,
            is_thinking_model=self._is_thinking_model,
        )

    @property
    def provider_name(self) -> str:
        return "gemini"

    @property
    def model_name(self) -> str:
        return self._model_name

    def _safe_max_tokens(self, max_tokens: int) -> int:
        """Ensure max_tokens is high enough for thinking models.

        Thinking models (gemini-2.5+, gemini-3+) use max_output_tokens for BOTH
        internal reasoning and visible output. A request for 50-500 tokens would
        leave almost nothing for visible text after thinking consumes most of it.
        """
        if self._is_thinking_model and max_tokens < self.THINKING_MIN_OUTPUT_TOKENS:
            logger.debug(
                "gemini_thinking_tokens_adjusted",
                requested=max_tokens,
                adjusted=self.THINKING_MIN_OUTPUT_TOKENS,
            )
            return self.THINKING_MIN_OUTPUT_TOKENS
        return max_tokens

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
        safe_tokens = self._safe_max_tokens(max_tokens)

        try:
            response = await self._client.aio.models.generate_content(
                model=self._model_name,
                contents=full_prompt,
                config=types.GenerateContentConfig(
                    temperature=temperature,
                    max_output_tokens=safe_tokens,
                ),
            )

            # Handle empty/blocked responses (safety filters, empty candidates)
            if not response.candidates:
                block_reason = getattr(response, "prompt_feedback", None)
                raise ValueError(
                    f"Gemini a returnat un răspuns gol (fără candidați). "
                    f"Motiv posibil: filtru de siguranță. Feedback: {block_reason}"
                )

            # Try response.text first, fall back to extracting from parts
            # (thinking models like gemini-2.5-pro may have text=None
            #  because response contains thought parts alongside text parts)
            text = response.text
            if text is None:
                # Extract text from candidate parts directly
                candidate = response.candidates[0]
                if candidate.content and candidate.content.parts:
                    text_parts = []
                    for part in candidate.content.parts:
                        # Skip thought parts, only collect text parts
                        if hasattr(part, "thought") and part.thought:
                            continue
                        if hasattr(part, "text") and part.text:
                            text_parts.append(part.text)
                    if text_parts:
                        text = "".join(text_parts)

            if text is None:
                raise ValueError(
                    "Gemini a returnat un răspuns gol (text=None). "
                    "Verificați modelul și parametrii."
                )
            return text

        except Exception as e:
            self._check_resource_exhausted(e)
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
        safe_tokens = self._safe_max_tokens(max_tokens)

        try:
            async for chunk in await self._client.aio.models.generate_content_stream(
                model=self._model_name,
                contents=full_prompt,
                config=types.GenerateContentConfig(
                    temperature=temperature,
                    max_output_tokens=safe_tokens,
                ),
            ):
                if chunk.text:
                    yield chunk.text

        except Exception as e:
            self._check_resource_exhausted(e)
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
            self._check_resource_exhausted(e)
            logger.error("gemini_embed_error", error=str(e))
            raise

    def _check_resource_exhausted(self, error: Exception) -> None:
        """Raise ResourceExhaustedError if the error is a quota/rate-limit issue."""
        err_str = str(error).lower()
        if any(kw in err_str for kw in [
            "resource_exhausted", "resource exhausted",
            "quota", "rate limit", "429", "too many requests",
            "rate_limit", "ratelimit",
        ]):
            raise ResourceExhaustedError("gemini", str(error)) from error

    # Gemini context window limits (chars, ~4 chars/token)
    # gemini-2.5-pro: 1M tokens, gemini-3.1-pro: 1M tokens
    # Reserve 20% for output + system prompt overhead
    MAX_CONTEXT_CHARS = 800_000  # ~200K tokens — safe for 1M token models

    def _build_prompt(
        self,
        prompt: str,
        context: list[str] | None,
        system_prompt: str | None,
    ) -> str:
        """Build the full prompt with context and system instructions.

        Applies proportional truncation if total context exceeds budget.
        """
        parts = []

        if system_prompt:
            parts.append(f"<system>\n{system_prompt}\n</system>\n")

        # Calculate budget for context (total budget minus system prompt and query)
        overhead = len(system_prompt or "") + len(prompt) + 200  # tags etc.
        context_budget = self.MAX_CONTEXT_CHARS - overhead

        if context and context_budget > 0:
            total_ctx_chars = sum(len(c) for c in context)
            if total_ctx_chars > context_budget:
                # Proportional truncation: each context doc gets its fair share
                ratio = context_budget / total_ctx_chars
                logger.info(
                    "gemini_context_truncated",
                    original_chars=total_ctx_chars,
                    budget_chars=context_budget,
                    ratio=round(ratio, 2),
                    num_contexts=len(context),
                )
                truncated = [c[:int(len(c) * ratio)] for c in context]
                context = truncated

            parts.append("<context>")
            for i, ctx in enumerate(context, 1):
                parts.append(f"\n[Document {i}]\n{ctx}\n")
            parts.append("</context>\n")
        elif context:
            # Budget exhausted by system prompt + query, skip context
            logger.warning("gemini_context_skipped_budget_exhausted")

        parts.append(f"<query>\n{prompt}\n</query>")

        return "\n".join(parts)
