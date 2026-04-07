"""RAG Memo generation API endpoints."""

import asyncio
import time
from typing import Optional

from fastapi import APIRouter, HTTPException, Depends, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import defer

from app.core.logging import get_logger
from app.core.rate_limiter import require_rate_limit, increment_usage
from app.db.session import get_session
from app.models.decision import User, DecizieCNSC
from app.services.rag import RAGService
from app.services.llm.streaming import create_sse_response
from app.api.v1.scopes import get_scope_decision_ids

# Overall endpoint timeout (seconds) — generous for large topics + RAG search
ENDPOINT_TIMEOUT = 300
# Threshold above which we use multi-chunk RAG instead of single embedding
MULTI_CHUNK_THRESHOLD = 5000

router = APIRouter()
logger = get_logger(__name__)


class RAGMemoRequest(BaseModel):
    """Request for RAG memo generation."""

    topic: str = Field(..., min_length=3, max_length=100000, description="Topic for the legal memo")
    max_decisions: int = Field(
        default=5,
        ge=1,
        le=10,
        description="Maximum number of decisions to include"
    )
    scope_id: str | None = Field(None, description="Optional scope ID for pre-filtering decisions")


class RAGMemoResponse(BaseModel):
    """Response from RAG memo generation."""

    memo: str
    topic: str
    decisions_used: int
    confidence: float


@router.post("/", response_model=RAGMemoResponse)
async def generate_rag_memo(
    request: RAGMemoRequest,
    http_request: Request,
    session: AsyncSession = Depends(get_session),
    rate_user: Optional[User] = Depends(require_rate_limit),
) -> RAGMemoResponse:
    """
    Generate legal memo based on CNSC jurisprudence.

    Searches the database for relevant decisions on the given topic
    and generates a structured legal memo with citations.

    The memo includes:
    - Overview of relevant jurisprudence
    - Key arguments and principles
    - Specific CNSC decisions and outcomes
    - Recommendations based on precedents
    """
    logger.info("rag_memo_request", topic=request.topic, max_decisions=request.max_decisions)

    try:
        # Initialize RAG service
        rag = RAGService()

        # Resolve scope
        scope_ids = None
        if request.scope_id:
            scope_ids = await get_scope_decision_ids(request.scope_id, session)
            if scope_ids is None:
                raise HTTPException(status_code=404, detail="Scope not found")

        # Build query from topic
        query = f"Generează un memo juridic despre: {request.topic}. Include jurisprudență CNSC relevantă, argumente cheie și recomandări."

        # Generate response using RAG (with timeout guard)
        t0 = time.monotonic()
        response_text, citations, confidence, _ = await asyncio.wait_for(
            rag.generate_response(
                query=query,
                session=session,
                conversation_history=None,
                max_decisions=request.max_decisions,
                scope_decision_ids=scope_ids,
            ),
            timeout=ENDPOINT_TIMEOUT,
        )
        logger.info("timing_ragmemo_total", duration_s=round(time.monotonic() - t0, 2))

        logger.info(
            "rag_memo_generated",
            topic=request.topic,
            decisions_used=len(citations),
            confidence=confidence
        )

        await increment_usage(rate_user, http_request)

        return RAGMemoResponse(
            memo=response_text,
            topic=request.topic,
            decisions_used=len(citations),
            confidence=confidence
        )

    except asyncio.TimeoutError:
        logger.error(
            "rag_memo_timeout",
            topic_length=len(request.topic),
            timeout=ENDPOINT_TIMEOUT,
        )
        raise HTTPException(
            status_code=504,
            detail=(
                f"Generarea memo-ului a depășit timpul limită ({ENDPOINT_TIMEOUT}s). "
                "Subiectul este prea mare sau complex. Încercați cu un subiect mai concis."
            ),
        )
    except Exception as e:
        logger.error("rag_memo_error", error=str(e), exc_info=True)
        raise HTTPException(
            status_code=500,
            detail=f"Eroare la generarea memo-ului: {str(e)}"
        )


@router.post("/stream")
async def generate_rag_memo_stream(
    request: RAGMemoRequest,
    http_request: Request,
    session: AsyncSession = Depends(get_session),
    rate_user: Optional[User] = Depends(require_rate_limit),
):
    """Stream a legal memo via SSE."""
    logger.info("rag_memo_stream_request", topic=request.topic)

    rag = RAGService()

    # Resolve scope
    scope_ids = None
    if request.scope_id:
        scope_ids = await get_scope_decision_ids(request.scope_id, session)
        if scope_ids is None:
            raise HTTPException(status_code=404, detail="Scope not found")

    query = f"Generează un memo juridic despre: {request.topic}. Include jurisprudență CNSC relevantă, argumente cheie și recomandări."

    t0 = time.monotonic()
    use_multi_chunk = len(request.topic) > MULTI_CHUNK_THRESHOLD

    try:
        if use_multi_chunk:
            # Multi-chunk RAG: split large topic into chunks for better coverage
            logger.info("ragmemo_using_multi_chunk", topic_len=len(request.topic))
            relevant_chunks, leg_fragments = await asyncio.wait_for(
                rag.multi_chunk_search(
                    documents=[request.topic],
                    session=session,
                    max_decisions=request.max_decisions,
                    max_legislation=8,
                    scope_decision_ids=scope_ids,
                ),
                timeout=ENDPOINT_TIMEOUT,
            )
            # Build context from multi-chunk results
            contexts, system_prompt, citations, confidence = await rag._build_context_from_chunks(
                query=query,
                relevant_chunks=relevant_chunks,
                legislation_fragments=leg_fragments,
                session=session,
            )
        else:
            contexts, system_prompt, citations, confidence, _ = await asyncio.wait_for(
                rag.prepare_context(
                    query=query, session=session, conversation_history=None, max_decisions=request.max_decisions,
                    scope_decision_ids=scope_ids,
                ),
                timeout=ENDPOINT_TIMEOUT,
            )
    except asyncio.TimeoutError:
        logger.error(
            "rag_memo_stream_timeout",
            topic_length=len(request.topic),
            timeout=ENDPOINT_TIMEOUT,
            multi_chunk=use_multi_chunk,
        )
        raise HTTPException(
            status_code=504,
            detail=(
                f"Căutarea RAG a depășit timpul limită ({ENDPOINT_TIMEOUT}s). "
                "Subiectul este prea mare sau complex. Încercați cu un subiect mai concis."
            ),
        )
    search_duration_s = round(time.monotonic() - t0, 2)
    logger.info("timing_ragmemo_stream_search", duration_s=search_duration_s, multi_chunk=use_multi_chunk)

    if contexts is None:
        raise HTTPException(
            status_code=404,
            detail="Nu am găsit jurisprudență relevantă pentru acest subiect.",
        )

    status_msgs = []
    n_sources = len(citations)
    if n_sources:
        status_msgs.append(f"Am identificat {n_sources} decizii CNSC relevante")

    await increment_usage(rate_user, http_request)

    return await create_sse_response(
        llm=rag.llm,
        prompt=query,
        context=contexts,
        system_prompt=system_prompt,
        temperature=0.1,
        max_tokens=12288,
        metadata={
            "citations": [{"decision_id": c.decision_id, "text": c.text, "verified": c.verified} for c in citations],
            "confidence": confidence,
            "decisions_used": len(citations),
            "search_duration_s": search_duration_s,
        },
        status_messages=status_msgs,
    )
