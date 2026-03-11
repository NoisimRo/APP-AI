"""Base LLM Provider interface."""

from abc import ABC, abstractmethod
from typing import AsyncIterator


class ResourceExhaustedError(Exception):
    """Raised when an API quota or rate limit is exhausted.

    This signals that the process should stop retrying and halt,
    as further requests will also fail until quota resets.
    """

    def __init__(self, provider: str, message: str, retry_after: float | None = None):
        self.provider = provider
        self.retry_after = retry_after
        super().__init__(f"[{provider}] Resource exhausted: {message}")


class LLMProvider(ABC):
    """Abstract base class for LLM providers.

    All LLM providers must implement this interface to ensure
    consistent behavior across different providers (Vertex AI,
    OpenAI, Anthropic, Ollama, etc.).
    """

    @abstractmethod
    async def complete(
        self,
        prompt: str,
        context: list[str] | None = None,
        system_prompt: str | None = None,
        temperature: float = 0.1,
        max_tokens: int = 4096,
    ) -> str:
        """Generate a completion for the given prompt.

        Args:
            prompt: The user's query or request.
            context: Optional list of context strings (e.g., retrieved documents).
            system_prompt: Optional system instruction.
            temperature: Controls randomness (0.0 = deterministic, 1.0 = creative).
            max_tokens: Maximum tokens in the response.

        Returns:
            The generated text response.
        """
        pass

    @abstractmethod
    async def stream(
        self,
        prompt: str,
        context: list[str] | None = None,
        system_prompt: str | None = None,
        temperature: float = 0.1,
        max_tokens: int = 4096,
    ) -> AsyncIterator[str]:
        """Stream a completion for the given prompt.

        Args:
            prompt: The user's query or request.
            context: Optional list of context strings.
            system_prompt: Optional system instruction.
            temperature: Controls randomness.
            max_tokens: Maximum tokens in the response.

        Yields:
            Text chunks as they are generated.
        """
        pass

    @abstractmethod
    async def embed(
        self,
        texts: list[str],
        task_type: str = "retrieval_document",
    ) -> list[list[float]]:
        """Generate embeddings for the given texts.

        Args:
            texts: List of strings to embed.
            task_type: Embedding task type. Use "retrieval_document" for stored
                documents and "retrieval_query" for search queries.

        Returns:
            List of embedding vectors (one per input text).
        """
        pass

    @property
    @abstractmethod
    def provider_name(self) -> str:
        """Return the name of this provider."""
        pass

    @property
    @abstractmethod
    def model_name(self) -> str:
        """Return the name of the model being used."""
        pass
