"""OpenRouter LLM Provider — access 400+ models through one API.

Uses OpenAI-compatible API with OpenRouter's base_url.
Free models available (suffix :free): Llama 4, DeepSeek R1/V3, Qwen3, Gemma 3, etc.
Free tier: https://openrouter.ai/ (no credit card required)
"""

from typing import AsyncIterator

import openai

from app.core.config import get_settings
from app.core.logging import get_logger
from app.services.llm.base import LLMProvider

logger = get_logger(__name__)

OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"

# Max input tokens per model on OpenRouter.
# Free models (:free suffix) have tighter limits; paid versions are generous.
# These are conservative budgets (leaving room for max_tokens response).
MODEL_INPUT_LIMITS: dict[str, int] = {
    # Free models — conservative limits (free tier rate-limited)
    "deepseek/deepseek-chat-v3-0324:free": 28000,
    "deepseek/deepseek-r1-0528:free": 28000,
    "meta-llama/llama-4-maverick:free": 28000,
    "meta-llama/llama-4-scout:free": 28000,
    "meta-llama/llama-3.3-70b-instruct:free": 28000,
    "qwen/qwen3-235b-a22b:free": 28000,
    "google/gemma-3-27b-it:free": 12000,
    "mistralai/mistral-small-3.1-24b-instruct:free": 12000,
    "nvidia/llama-3.1-nemotron-ultra-253b-v1:free": 28000,
    "openrouter/free": 12000,  # Router — unknown model, be conservative
}
DEFAULT_INPUT_LIMIT = 28000  # Most OpenRouter models have large contexts


class OpenRouterProvider(LLMProvider):
    """OpenRouter provider — unified API for 400+ models (including free ones).

    Uses the OpenAI Python client with OpenRouter's OpenAI-compatible API.
    Does NOT support embeddings — embeddings always use Gemini.
    """

    def __init__(
        self,
        model: str = "deepseek/deepseek-chat-v3-0324:free",
        api_key: str | None = None,
    ):
        self._model_name = model

        key = api_key or get_settings().openrouter_api_key
        if not key:
            raise ValueError(
                "OPENROUTER_API_KEY is required for OpenRouterProvider. "
                "Set it as env var or configure in Setări LLM. "
                "Obțineți gratuit de la https://openrouter.ai/"
            )

        self._client = openai.AsyncOpenAI(
            api_key=key,
            base_url=OPENROUTER_BASE_URL,
        )

        logger.info("openrouter_provider_initialized", model=model)

    @property
    def provider_name(self) -> str:
        return "openrouter"

    @property
    def model_name(self) -> str:
        return self._model_name

    def _estimate_tokens(self, text: str) -> int:
        """Estimate token count (~4 chars per token for Romanian/mixed text)."""
        return len(text) // 4

    def _get_max_input_tokens(self) -> int:
        """Get max input token budget for the current model."""
        return MODEL_INPUT_LIMITS.get(self._model_name, DEFAULT_INPUT_LIMIT)

    def _truncate_context_to_budget(
        self,
        context: list[str],
        system_prompt: str | None,
        prompt: str,
    ) -> list[str]:
        """Truncate context documents to fit within model's token limit."""
        max_input = self._get_max_input_tokens()
        overhead = self._estimate_tokens(prompt)
        if system_prompt:
            overhead += self._estimate_tokens(system_prompt)
        overhead += 100  # XML tags, doc headers

        available = max_input - overhead
        if available <= 0:
            logger.warning(
                "openrouter_no_context_budget",
                model=self._model_name,
                max_input=max_input,
                overhead=overhead,
            )
            return []

        total_ctx_tokens = sum(self._estimate_tokens(c) for c in context)
        if total_ctx_tokens <= available:
            return context

        logger.info(
            "openrouter_truncating_context",
            model=self._model_name,
            original_tokens=total_ctx_tokens,
            budget=available,
            num_docs=len(context),
        )

        ratio = available / total_ctx_tokens
        truncated = []
        for ctx in context:
            max_chars = int(len(ctx) * ratio)
            if max_chars < 200:
                continue
            if len(ctx) > max_chars:
                truncated.append(ctx[:max_chars] + "\n[... trunchiat pentru limita modelului]")
            else:
                truncated.append(ctx)

        return truncated

    def _build_messages(
        self,
        prompt: str,
        context: list[str] | None,
        system_prompt: str | None,
    ) -> list[dict]:
        """Build OpenAI-compatible messages array with token-aware truncation."""
        messages = []

        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})

        if context:
            context = self._truncate_context_to_budget(context, system_prompt, prompt)

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
        """Generate a completion using OpenRouter."""
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
                    "OpenRouter a returnat un răspuns gol (content=None). "
                    "Verificați modelul și parametrii."
                )
            return content

        except Exception as e:
            logger.error("openrouter_completion_error", error=str(e))
            raise

    async def stream(
        self,
        prompt: str,
        context: list[str] | None = None,
        system_prompt: str | None = None,
        temperature: float = 0.1,
        max_tokens: int = 4096,
    ) -> AsyncIterator[str]:
        """Stream a completion using OpenRouter."""
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
            logger.error("openrouter_stream_error", error=str(e))
            raise

    async def embed(
        self,
        texts: list[str],
        task_type: str = "RETRIEVAL_DOCUMENT",
    ) -> list[list[float]]:
        """Not supported — embeddings always use Gemini."""
        raise NotImplementedError(
            "OpenRouter nu suportă embeddings. "
            "Se folosește GeminiProvider pentru embeddings."
        )
