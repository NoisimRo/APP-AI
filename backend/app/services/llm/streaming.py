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
    strip_preamble: bool = False,
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
        strip_preamble: If True, discard any text before the first ## heading.

    Returns:
        FastAPI StreamingResponse with text/event-stream content type.
    """

    async def event_generator():
        import time
        t_stream_start = time.monotonic()
        first_token_logged = False

        try:
            # Buffer to strip preamble text before first ## heading
            preamble_buffer = ""
            preamble_passed = not strip_preamble

            async for chunk in llm.stream(
                prompt=prompt,
                context=context,
                system_prompt=system_prompt,
                temperature=temperature,
                max_tokens=max_tokens,
            ):
                if not first_token_logged:
                    logger.info("timing_llm_first_token",
                                duration_s=round(time.monotonic() - t_stream_start, 2))
                    first_token_logged = True

                if not preamble_passed:
                    preamble_buffer += chunk
                    # Check if we've reached the first ## heading
                    heading_pos = preamble_buffer.find("## ")
                    if heading_pos >= 0:
                        # Discard everything before the heading, emit from ## onwards
                        remaining = preamble_buffer[heading_pos:]
                        preamble_passed = True
                        if remaining:
                            yield f"data: {json.dumps({'text': remaining})}\n\n"
                    elif len(preamble_buffer) > 500:
                        # Safety: if no heading found after 500 chars, flush everything
                        preamble_passed = True
                        yield f"data: {json.dumps({'text': preamble_buffer})}\n\n"
                    continue

                yield f"data: {json.dumps({'text': chunk})}\n\n"

            logger.info("timing_llm_stream_total",
                         duration_s=round(time.monotonic() - t_stream_start, 2))

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
