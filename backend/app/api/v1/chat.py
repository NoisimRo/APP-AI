"""Chat API endpoints."""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.core.logging import get_logger

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
async def chat(request: ChatRequest) -> ChatResponse:
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

    # TODO: Implement RAG pipeline
    # 1. Generate embedding for the query
    # 2. Search for relevant documents
    # 3. Build context from retrieved documents
    # 4. Generate response with LLM
    # 5. Verify citations
    # 6. Return response

    # Placeholder response
    return ChatResponse(
        message=(
            "Aceasta este o versiune demo. Sistemul RAG va fi implementat în curând. "
            "Întrebarea ta a fost: " + request.message
        ),
        conversation_id=request.conversation_id or "demo-conversation",
        citations=[],
        confidence=0.0,
        suggested_questions=[
            "Care sunt criteriile de calificare acceptabile?",
            "Ce spune CNSC despre experiența similară?",
            "Cum se interpretează art. 210 din Legea 98/2016?",
        ],
    )


@router.get("/conversations/{conversation_id}")
async def get_conversation(conversation_id: str):
    """Get conversation history by ID."""
    # TODO: Implement conversation retrieval
    raise HTTPException(status_code=501, detail="Not implemented yet")
