"""Chat API endpoints."""

from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.db.session import get_session
from app.models.decision import SearchScope
from app.services.rag import RAGService
from app.services.llm.factory import get_active_llm_provider
from app.services.llm.streaming import create_sse_response

router = APIRouter()
logger = get_logger(__name__)


class ChatMessage(BaseModel):
    """A chat message."""

    role: str = Field(..., description="Message role: 'user' or 'assistant'")
    content: str = Field(..., description="Message content")


class ChatFilters(BaseModel):
    """Filtre opționale pentru restricționarea căutării RAG."""

    scope_id: str | None = None
    ruling: str | None = None
    tip_contestatie: str | None = None
    year: str | None = None
    coduri_critici: list[str] | None = None
    cpv_codes: list[str] | None = None


class ChatRequest(BaseModel):
    """Chat request payload."""

    message: str = Field(..., min_length=1, max_length=100000)
    conversation_id: str | None = Field(None, description="Optional conversation ID")
    history: list[ChatMessage] = Field(default_factory=list)
    rerank: bool = Field(False, description="Enable LLM-based reranking")
    filters: ChatFilters | None = Field(None, description="Optional search scope filters")


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


async def _resolve_filters(filters: ChatFilters | None, session: AsyncSession) -> dict | None:
    """Resolve ChatFilters to a dict for RAG, loading scope filters if scope_id is set."""
    if not filters:
        return None

    result = {}

    # If scope_id is set, load filters from DB
    if filters.scope_id:
        stmt = select(SearchScope).where(SearchScope.id == filters.scope_id)
        scope_result = await session.execute(stmt)
        scope = scope_result.scalar_one_or_none()
        if scope and scope.filters:
            result.update(scope.filters)

    # Overlay explicit filters (override scope)
    if filters.ruling:
        result["ruling"] = filters.ruling
    if filters.tip_contestatie:
        result["tip_contestatie"] = filters.tip_contestatie
    if filters.year:
        result["year"] = filters.year
    if filters.coduri_critici:
        result["coduri_critici"] = filters.coduri_critici
    if filters.cpv_codes:
        result["cpv_codes"] = filters.cpv_codes

    return result if result else None


@router.post("/", response_model=ChatResponse)
async def chat(
    request: ChatRequest,
    session: AsyncSession = Depends(get_session)
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

        # Resolve filters (scope_id → dict)
        rag_filters = await _resolve_filters(request.filters, session)

        # Convert chat history to format expected by RAG
        history = [
            {"role": msg.role, "content": msg.content}
            for msg in request.history
        ] if request.history else None

        # Generate response using RAG pipeline
        response_text, citations, confidence, suggested = await rag.generate_response(
            query=request.message,
            session=session,
            conversation_history=history,
            max_decisions=5,
            rerank=request.rerank,
            filters=rag_filters,
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
    session: AsyncSession = Depends(get_session)
):
    """Stream chat response via SSE."""
    logger.info("chat_stream_request", message_length=len(request.message))

    try:
        llm = await get_active_llm_provider(session)
        rag = RAGService(llm_provider=llm)

        # Resolve filters (scope_id → dict)
        rag_filters = await _resolve_filters(request.filters, session)

        history = [
            {"role": msg.role, "content": msg.content}
            for msg in request.history
        ] if request.history else None

        # Run RAG search to get context and citations
        query = request.message
        contexts, system_prompt, citations, confidence, suggested = await rag.prepare_context(
            query=query, session=session, conversation_history=history, max_decisions=5,
            rerank=request.rerank, filters=rag_filters,
        )

        if contexts is None:
            # No relevant results — return a non-streaming error message
            raise HTTPException(
                status_code=404,
                detail="Nu am găsit informații relevante. Reformulează întrebarea.",
            )

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
            },
        )

    except Exception as e:
        logger.error("chat_stream_error", error=str(e), exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/conversations/{conversation_id}")
async def get_conversation(conversation_id: str):
    """Get conversation history by ID."""
    # TODO: Implement conversation retrieval
    raise HTTPException(status_code=501, detail="Not implemented yet")
