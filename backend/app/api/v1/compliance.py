"""Compliance checking API endpoints."""

import asyncio
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request, UploadFile, File, Form
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.core.rate_limiter import require_rate_limit, increment_usage
from app.core.deps import require_feature
from app.db.session import get_session, is_db_available
from app.models.decision import User
from app.services.llm.factory import get_active_llm_provider
from app.services.compliance_checker import ComplianceChecker
from app.services.document_processor import DocumentProcessor

router = APIRouter()
logger = get_logger(__name__)

ENDPOINT_TIMEOUT = 300


@router.post("/check")
async def check_compliance(
    http_request: Request,
    text: Optional[str] = Form(None),
    file: Optional[UploadFile] = File(None),
    tip_procedura: Optional[str] = Form(None),
    tip_document: Optional[str] = Form(None),
    session: AsyncSession = Depends(get_session),
    rate_user: Optional[User] = Depends(require_rate_limit),
    _feature: Optional[User] = Depends(require_feature("compliance")),
):
    """Check procurement document compliance against legal requirements.

    Accepts either text directly or a file upload (.pdf, .docx, .txt, .md).
    """
    if not is_db_available():
        raise HTTPException(status_code=503, detail="Baza de date indisponibilă")

    # Extract text from file or use provided text
    doc_text = text
    if file and not doc_text:
        processor = DocumentProcessor()
        content = await file.read()
        filename = file.filename or "document.txt"
        doc_text = processor.extract_text_from_file(content, filename)

    if not doc_text or len(doc_text.strip()) < 50:
        raise HTTPException(
            status_code=400,
            detail="Textul documentului este prea scurt (minim 50 caractere).",
        )

    logger.info("compliance_request", text_length=len(doc_text), tip_procedura=tip_procedura)

    try:
        llm = await get_active_llm_provider(session)
        checker = ComplianceChecker(llm_provider=llm)

        result = await asyncio.wait_for(
            checker.check_compliance(
                session=session,
                document_text=doc_text,
                tip_procedura=tip_procedura,
                tip_document=tip_document,
            ),
            timeout=ENDPOINT_TIMEOUT,
        )

        await increment_usage(rate_user, http_request)
        return result

    except asyncio.TimeoutError:
        raise HTTPException(
            status_code=504,
            detail="Timeout la verificarea conformității. Reîncearcă cu un document mai scurt.",
        )
    except Exception as e:
        logger.error("compliance_error", error=str(e), exc_info=True)
        raise HTTPException(status_code=500, detail=f"Eroare: {str(e)}")
