"""Chat API endpoints."""

from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.db.session import get_session
from app.services.rag import RAGService

router = APIRouter()
logger = get_logger(__name__)


class ChatMessage(BaseModel):
    """A chat message."""

    role: str = Field(..., description="Message role: 'user' or 'assistant'")
    content: str = Field(..., description="Message content")


class ChatRequest(BaseModel):
    """Chat request payload."""

    message: str = Field(..., min_length=1, max_length=10000)
    conversation_id: str | None = Field(None, description="Optional conversation ID")
    history: list[ChatMessage] = Field(default_factory=list)


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
        # Initialize RAG service
        rag = RAGService()

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
            max_decisions=5
        )

        # Generate or reuse conversation ID
        conversation_id = request.conversation_id or f"conv-{hash(request.message)}"

        logger.info(
            "chat_response_generated",
            conversation_id=conversation_id,
            citations_count=len(citations),
            confidence=confidence
        )

        return ChatResponse(
            message=response_text,
            conversation_id=conversation_id,
            citations=citations,
            confidence=confidence,
            suggested_questions=suggested,
        )

    except Exception as e:
        logger.error("chat_error", error=str(e), exc_info=True)
        raise HTTPException(
            status_code=500,
            detail=f"Eroare la procesarea cererii: {str(e)}"
        )


@router.get("/conversations/{conversation_id}")
async def get_conversation(conversation_id: str):
    """Get conversation history by ID."""
    # TODO: Implement conversation retrieval
    raise HTTPException(status_code=501, detail="Not implemented yet")
