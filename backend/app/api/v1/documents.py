"""Document processing API endpoints."""

import asyncio
from typing import Optional

from fastapi import APIRouter, HTTPException, UploadFile, File, Form, Depends, Request
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.core.rate_limiter import require_rate_limit, increment_usage
from app.db.session import get_session
from app.models.decision import User
from app.services.document_processor import DocumentProcessor
from app.services.entity_extractor import EntityExtractor
from app.services.llm.factory import get_active_llm_provider

router = APIRouter()
logger = get_logger(__name__)


class DocumentAnalyzeRequest(BaseModel):
    """Request for document analysis with base64 content."""

    filename: str = Field(..., description="Original filename")
    content: str = Field(..., description="Base64-encoded file content")
    mime_type: str | None = Field(None, description="MIME type of the file")


class TextStats(BaseModel):
    """Text statistics."""

    characters: int
    words: int
    lines: int
    paragraphs: int


class DocumentAnalyzeResponse(BaseModel):
    """Response from document analysis."""

    filename: str
    text: str
    stats: TextStats
    success: bool = True


@router.post("/analyze", response_model=DocumentAnalyzeResponse)
async def analyze_document(request: DocumentAnalyzeRequest) -> DocumentAnalyzeResponse:
    """
    Analyze document and extract text.

    Supports:
    - PDF files (.pdf)
    - Text files (.txt)
    - Markdown files (.md)

    The file should be base64-encoded in the request.
    """
    logger.info(
        "document_analyze_request",
        filename=request.filename,
        mime_type=request.mime_type,
        content_length=len(request.content)
    )

    try:
        # Initialize document processor
        processor = DocumentProcessor()

        # Extract text from base64
        extracted_text = processor.extract_text_from_base64(
            base64_content=request.content,
            filename=request.filename,
            mime_type=request.mime_type
        )

        # Clean text
        cleaned_text = processor.clean_text(extracted_text)

        # Get statistics
        stats = processor.get_text_stats(cleaned_text)

        logger.info(
            "document_analyzed",
            filename=request.filename,
            stats=stats
        )

        return DocumentAnalyzeResponse(
            filename=request.filename,
            text=cleaned_text,
            stats=TextStats(**stats),
            success=True
        )

    except ValueError as e:
        logger.error("document_analysis_error", error=str(e), filename=request.filename)
        raise HTTPException(
            status_code=400,
            detail=str(e)
        )
    except Exception as e:
        logger.error("document_analysis_unexpected_error", error=str(e), exc_info=True)
        raise HTTPException(
            status_code=500,
            detail=f"Eroare la procesarea documentului: {str(e)}"
        )


@router.post("/upload", response_model=DocumentAnalyzeResponse)
async def upload_document(file: UploadFile = File(...)) -> DocumentAnalyzeResponse:
    """
    Upload and analyze document via multipart form.

    Alternative endpoint that accepts direct file upload instead of base64.
    """
    logger.info(
        "document_upload_request",
        filename=file.filename,
        content_type=file.content_type
    )

    try:
        # Read file content
        file_content = await file.read()

        # Initialize document processor
        processor = DocumentProcessor()

        # Extract text
        extracted_text = processor.extract_text_from_file(
            file_content=file_content,
            filename=file.filename or "document",
            mime_type=file.content_type
        )

        # Clean text
        cleaned_text = processor.clean_text(extracted_text)

        # Get statistics
        stats = processor.get_text_stats(cleaned_text)

        logger.info(
            "document_uploaded_analyzed",
            filename=file.filename,
            stats=stats
        )

        return DocumentAnalyzeResponse(
            filename=file.filename or "document",
            text=cleaned_text,
            stats=TextStats(**stats),
            success=True
        )

    except ValueError as e:
        logger.error("document_upload_error", error=str(e), filename=file.filename)
        raise HTTPException(
            status_code=400,
            detail=str(e)
        )
    except Exception as e:
        logger.error("document_upload_unexpected_error", error=str(e), exc_info=True)
        raise HTTPException(
            status_code=500,
            detail=f"Eroare la procesarea documentului: {str(e)}"
        )


@router.post("/extract-entities")
async def extract_entities(
    http_request: Request,
    text: Optional[str] = Form(None),
    file: Optional[UploadFile] = File(None),
    session: AsyncSession = Depends(get_session),
    rate_user: Optional[User] = Depends(require_rate_limit),
):
    """Extract structured metadata (CPV, procedure type, value, etc.) from a document.

    Accepts either text directly or a file upload. Returns JSON with extracted entities.
    Used by frontend to auto-populate forms in Drafter, Strategy, Red Flags, etc.
    """
    doc_text = text
    if file and not doc_text:
        processor = DocumentProcessor()
        content = await file.read()
        filename = file.filename or ""
        if filename.lower().endswith(".pdf"):
            doc_text = processor.extract_text_from_pdf(content)
        elif filename.lower().endswith((".docx", ".doc")):
            doc_text = processor.extract_text_from_docx(content)
        else:
            doc_text = processor.extract_text_from_txt(content)

    if not doc_text or len(doc_text.strip()) < 30:
        raise HTTPException(status_code=400, detail="Text prea scurt pentru extragere.")

    try:
        llm = await get_active_llm_provider(session)
        extractor = EntityExtractor(llm_provider=llm)
        entities = await asyncio.wait_for(
            extractor.extract_entities(doc_text),
            timeout=120,
        )
        await increment_usage(rate_user, http_request)
        return entities
    except asyncio.TimeoutError:
        raise HTTPException(status_code=504, detail="Timeout la extragerea entităților.")
    except Exception as e:
        logger.error("entity_extraction_error", error=str(e), exc_info=True)
        raise HTTPException(status_code=500, detail=f"Eroare: {str(e)}")
