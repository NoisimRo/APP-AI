"""Chat API endpoints."""

from typing import Optional

from fastapi import APIRouter, HTTPException, Depends, Request
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.core.rate_limiter import require_rate_limit, increment_usage
from app.db.session import get_session
from app.models.decision import User
from app.services.rag import RAGService
from app.services.llm.factory import get_active_llm_provider
from app.services.llm.streaming import create_sse_response
from app.api.v1.scopes import get_scope_decision_ids

router = APIRouter()
logger = get_logger(__name__)


class ChatMessage(BaseModel):
    """A chat message."""

    role: str = Field(..., description="Message role: 'user' or 'assistant'")
    content: str = Field(..., description="Message content")


class ChatRequest(BaseModel):
    """Chat request payload."""

    message: str = Field(..., min_length=1, max_length=100000)
    conversation_id: str | None = Field(None, description="Optional conversation ID")
    history: list[ChatMessage] = Field(default_factory=list)
    scope_id: str | None = Field(None, description="Optional scope ID for pre-filtering decisions")
    rerank: bool = Field(False, description="Enable LLM reranking of retrieved chunks")
    expansion: bool = Field(False, description="Enable LLM query expansion for better retrieval")


class Citation(BaseModel):
    """A citation from a CNSC decision."""

    decision_id: str
    text: str
    verified: bool = True


class ChatResponse(BaseModel):
    """Chat response payload."""

    message: str
    conversation_id: str
    citations: list[Citation] = Field(default_factory=list)
    confidence: float = Field(ge=0.0, le=1.0)
    suggested_questions: list[str] = Field(default_factory=list)


@router.post("/", response_model=ChatResponse)
async def chat(
    request: ChatRequest,
    http_request: Request,
    session: AsyncSession = Depends(get_session),
    rate_user: Optional[User] = Depends(require_rate_limit),
) -> ChatResponse:
    """
    Chat with ExpertAP.

    Send a message and receive an AI-generated response grounded in CNSC decisions.
    All citations are verified against the database.
    """
    logger.info(
        "chat_request",
        message_length=len(request.message),
        has_history=len(request.history) > 0,
    )

    try:
        # Initialize RAG service with active provider
        llm = await get_active_llm_provider(session)
        rag = RAGService(llm_provider=llm)

        # Convert chat history to format expected by RAG
        history = [
            {"role": msg.role, "content": msg.content}
            for msg in request.history
        ] if request.history else None

        # Resolve scope to decision IDs if provided
        scope_ids = None
        if request.scope_id:
            scope_ids = await get_scope_decision_ids(request.scope_id, session)
            if scope_ids is None:
                raise HTTPException(status_code=404, detail="Scope not found")
            logger.info("chat_scope_applied", scope_id=request.scope_id,
                        decision_count=len(scope_ids))

        # Generate response using RAG pipeline
        response_text, citations, confidence, suggested = await rag.generate_response(
            query=request.message,
            session=session,
            conversation_history=history,
            max_decisions=5,
            scope_decision_ids=scope_ids,
            enable_rerank=request.rerank,
            enable_expansion=request.expansion,
        )

        # Generate or reuse conversation ID
        conversation_id = request.conversation_id or f"conv-{hash(request.message)}"

        logger.info(
            "chat_response_generated",
            conversation_id=conversation_id,
            citations_count=len(citations),
            confidence=confidence
        )

        # Convert Citation objects to dicts for Pydantic validation
        # RAG service returns Citation objects from rag.py, but ChatResponse
        # expects Citation objects from chat.py (different classes)
        citations_dicts = [
            {"decision_id": c.decision_id, "text": c.text, "verified": c.verified}
            for c in citations
        ]

        increment_usage(rate_user, http_request)

        return ChatResponse(
            message=response_text,
            conversation_id=conversation_id,
            citations=citations_dicts,
            confidence=confidence,
            suggested_questions=suggested,
        )

    except Exception as e:
        logger.error("chat_error", error=str(e), exc_info=True)
        raise HTTPException(
            status_code=500,
            detail=f"Eroare la procesarea cererii: {str(e)}"
        )


@router.post("/stream")
async def chat_stream(
    request: ChatRequest,
    http_request: Request,
    session: AsyncSession = Depends(get_session),
    rate_user: Optional[User] = Depends(require_rate_limit),
):
    """Stream chat response via SSE."""
    logger.info("chat_stream_request", message_length=len(request.message))

    try:
        llm = await get_active_llm_provider(session)
        rag = RAGService(llm_provider=llm)

        history = [
            {"role": msg.role, "content": msg.content}
            for msg in request.history
        ] if request.history else None

        # Resolve scope to decision IDs if provided
        scope_ids = None
        if request.scope_id:
            scope_ids = await get_scope_decision_ids(request.scope_id, session)
            if scope_ids is None:
                raise HTTPException(status_code=404, detail="Scope not found")

        # Run RAG search to get context and citations
        import time
        t_start = time.monotonic()

        query = request.message
        contexts, system_prompt, citations, confidence, suggested = await rag.prepare_context(
            query=query, session=session, conversation_history=history, max_decisions=5,
            scope_decision_ids=scope_ids,
            enable_rerank=request.rerank,
            enable_expansion=request.expansion,
        )

        search_duration_s = round(time.monotonic() - t_start, 2)
        logger.info("chat_stream_search_completed", duration_s=search_duration_s)

        if contexts is None:
            # No relevant results — return a non-streaming error message
            raise HTTPException(
                status_code=404,
                detail="Nu am găsit informații relevante. Reformulează întrebarea.",
            )

        # Build status messages for user feedback
        status_msgs = []
        n_sources = len(citations)
        if n_sources:
            status_msgs.append(f"Am identificat {n_sources} decizii CNSC relevante")

        increment_usage(rate_user, http_request)

        # Stream the LLM response
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
                "suggested_questions": suggested,
                "conversation_id": request.conversation_id or f"conv-{hash(request.message)}",
                "search_duration_s": search_duration_s,
            },
            status_messages=status_msgs,
        )

    except Exception as e:
        logger.error("chat_stream_error", error=str(e), exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/conversations/{conversation_id}")
async def get_conversation(conversation_id: str):
    """Get conversation history by ID."""
    # TODO: Implement conversation retrieval
    raise HTTPException(status_code=501, detail="Not implemented yet")
