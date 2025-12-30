"""RAG Memo generation API endpoints."""

from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.db.session import get_session
from app.services.rag import RAGService

router = APIRouter()
logger = get_logger(__name__)


class RAGMemoRequest(BaseModel):
    """Request for RAG memo generation."""

    topic: str = Field(..., min_length=3, description="Topic for the legal memo")
    max_decisions: int = Field(
        default=5,
        ge=1,
        le=10,
        description="Maximum number of decisions to include"
    )


class RAGMemoResponse(BaseModel):
    """Response from RAG memo generation."""

    memo: str
    topic: str
    decisions_used: int
    confidence: float


@router.post("/", response_model=RAGMemoResponse)
async def generate_rag_memo(
    request: RAGMemoRequest,
    session: AsyncSession = Depends(get_session)
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

        # Build query from topic
        query = f"Generează un memo juridic despre: {request.topic}. Include jurisprudență CNSC relevantă, argumente cheie și recomandări."

        # Generate response using RAG
        response_text, citations, confidence, _ = await rag.generate_response(
            query=query,
            session=session,
            conversation_history=None,
            max_decisions=request.max_decisions
        )

        logger.info(
            "rag_memo_generated",
            topic=request.topic,
            decisions_used=len(citations),
            confidence=confidence
        )

        return RAGMemoResponse(
            memo=response_text,
            topic=request.topic,
            decisions_used=len(citations),
            confidence=confidence
        )

    except Exception as e:
        logger.error("rag_memo_error", error=str(e), exc_info=True)
        raise HTTPException(
            status_code=500,
            detail=f"Eroare la generarea memo-ului: {str(e)}"
        )
