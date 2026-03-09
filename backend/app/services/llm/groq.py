"""Groq LLM Provider — free open-source models with blazing fast inference.

Uses OpenAI-compatible API with Groq's base_url.
Supports Llama 3.3 70B, GPT-OSS 120B, Qwen3, Llama 4 Scout, etc.
Free tier: https://console.groq.com/
"""

from typing import AsyncIterator

import openai

from app.core.config import get_settings
from app.core.logging import get_logger
from app.services.llm.base import LLMProvider

logger = get_logger(__name__)

GROQ_BASE_URL = "https://api.groq.com/openai/v1"

# Max input tokens per model on Groq free tier (on_demand).
# Values derived from actual TPM errors — leave ~2000 tokens for response.
# TPM = total tokens per minute (input + output combined).
MODEL_INPUT_LIMITS: dict[str, int] = {
    "llama-3.3-70b-versatile": 8000,       # TPM 12000
    "llama-3.1-8b-instant": 3500,           # TPM 6000
    "openai/gpt-oss-120b": 5000,            # TPM 8000
    "qwen/qwen3-32b": 3500,                 # TPM 6000
    "meta-llama/llama-4-scout-17b-16e-instruct": 5000,  # TPM ~8000
}
DEFAULT_INPUT_LIMIT = 3500  # Conservative default for unknown models

# Max output tokens per model (some models cap lower than 4096)
MODEL_MAX_OUTPUT_TOKENS: dict[str, int] = {
    "meta-llama/llama-4-scout-17b-16e-instruct": 8192,
}
DEFAULT_MAX_OUTPUT_TOKENS = 4096


class GroqProvider(LLMProvider):
    """Groq provider — free, fast inference on open-source models.

    Uses the OpenAI Python client with Groq's OpenAI-compatible API.
    Does NOT support embeddings — embeddings always use Gemini.
    """

    def __init__(
        self,
        model: str = "llama-3.3-70b-versatile",
        api_key: str | None = None,
    ):
        self._model_name = model

        key = api_key or get_settings().groq_api_key
        if not key:
            raise ValueError(
                "GROQ_API_KEY is required for GroqProvider. "
                "Set it as env var or configure in Setări LLM. "
                "Obțineți gratuit de la https://console.groq.com/"
            )

        self._client = openai.AsyncOpenAI(
            api_key=key,
            base_url=GROQ_BASE_URL,
        )

        logger.info("groq_provider_initialized", model=model)

    @property
    def provider_name(self) -> str:
        return "groq"

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
        """Truncate context documents to fit within model's token limit.

        Strategy: estimate total tokens, then progressively truncate
        context documents (largest first) until within budget.
        """
        max_input = self._get_max_input_tokens()
        overhead = self._estimate_tokens(prompt)
        if system_prompt:
            overhead += self._estimate_tokens(system_prompt)
        # XML tags, doc headers, etc.
        overhead += 100

        available = max_input - overhead
        if available <= 0:
            logger.warning(
                "groq_no_context_budget",
                model=self._model_name,
                max_input=max_input,
                overhead=overhead,
            )
            return []

        # Check if context already fits
        total_ctx_tokens = sum(self._estimate_tokens(c) for c in context)
        if total_ctx_tokens <= available:
            return context

        logger.info(
            "groq_truncating_context",
            model=self._model_name,
            original_tokens=total_ctx_tokens,
            budget=available,
            num_docs=len(context),
        )

        # Progressively truncate: first try cutting each doc proportionally
        ratio = available / total_ctx_tokens
        truncated = []
        for ctx in context:
            max_chars = int(len(ctx) * ratio)
            if max_chars < 200:
                continue  # Skip docs that would be too small to be useful
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

        # Truncate context to fit model's token limits
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

    def _get_max_output_tokens(self, requested: int) -> int:
        """Cap max_tokens to model's limit."""
        model_max = MODEL_MAX_OUTPUT_TOKENS.get(self._model_name, DEFAULT_MAX_OUTPUT_TOKENS)
        return min(requested, model_max)

    async def complete(
        self,
        prompt: str,
        context: list[str] | None = None,
        system_prompt: str | None = None,
        temperature: float = 0.1,
        max_tokens: int = 4096,
    ) -> str:
        """Generate a completion using Groq."""
        messages = self._build_messages(prompt, context, system_prompt)
        safe_max_tokens = self._get_max_output_tokens(max_tokens)

        try:
            response = await self._client.chat.completions.create(
                model=self._model_name,
                messages=messages,
                temperature=temperature,
                max_tokens=safe_max_tokens,
            )

            content = response.choices[0].message.content
            if content is None:
                raise ValueError(
                    "Groq a returnat un răspuns gol (content=None). "
                    "Verificați modelul și parametrii."
                )
            return content

        except Exception as e:
            logger.error("groq_completion_error", error=str(e))
            raise

    async def stream(
        self,
        prompt: str,
        context: list[str] | None = None,
        system_prompt: str | None = None,
        temperature: float = 0.1,
        max_tokens: int = 4096,
    ) -> AsyncIterator[str]:
        """Stream a completion using Groq."""
        messages = self._build_messages(prompt, context, system_prompt)
        safe_max_tokens = self._get_max_output_tokens(max_tokens)

        try:
            stream = await self._client.chat.completions.create(
                model=self._model_name,
                messages=messages,
                temperature=temperature,
                max_tokens=safe_max_tokens,
                stream=True,
            )

            async for chunk in stream:
                if not chunk.choices:
                    continue
                delta = chunk.choices[0].delta
                if delta.content:
                    yield delta.content

        except Exception as e:
            logger.error("groq_stream_error", error=str(e))
            raise

    async def embed(
        self,
        texts: list[str],
        task_type: str = "RETRIEVAL_DOCUMENT",
    ) -> list[list[float]]:
        """Not supported — embeddings always use Gemini."""
        raise NotImplementedError(
            "Groq nu suportă embeddings. "
            "Se folosește GeminiProvider pentru embeddings."
        )
