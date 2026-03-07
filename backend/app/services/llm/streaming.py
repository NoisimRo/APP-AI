"""SSE streaming utilities for LLM responses."""

import json

from fastapi.responses import StreamingResponse

from app.core.logging import get_logger
from app.services.llm.base import LLMProvider

logger = get_logger(__name__)


async def create_sse_response(
    llm: LLMProvider,
    prompt: str,
    context: list[str] | None = None,
    system_prompt: str | None = None,
    temperature: float = 0.1,
    max_tokens: int = 8192,
    metadata: dict | None = None,
) -> StreamingResponse:
    """Create an SSE StreamingResponse from an LLM stream.

    Args:
        llm: The LLM provider to stream from.
        prompt: The prompt to send.
        context: Optional context list.
        system_prompt: Optional system prompt.
        temperature: Generation temperature.
        max_tokens: Maximum output tokens.
        metadata: Optional metadata to send as final event (e.g. citations, decision_refs).

    Returns:
        FastAPI StreamingResponse with text/event-stream content type.
    """

    async def event_generator():
        try:
            async for chunk in llm.stream(
                prompt=prompt,
                context=context,
                system_prompt=system_prompt,
                temperature=temperature,
                max_tokens=max_tokens,
            ):
                yield f"data: {json.dumps({'text': chunk})}\n\n"

            # Send final event with metadata
            done_payload = {"done": True}
            if metadata:
                done_payload.update(metadata)
            yield f"data: {json.dumps(done_payload)}\n\n"

        except Exception as e:
            logger.error("sse_stream_error", error=str(e))
            yield f"data: {json.dumps({'error': str(e)})}\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
