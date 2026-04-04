"""Multi-document analysis API endpoint."""

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
from app.services.multi_document_analyzer import MultiDocumentAnalyzer

router = APIRouter()
logger = get_logger(__name__)

ENDPOINT_TIMEOUT = 600  # 10 min — multi-doc is heavy


@router.post("/analyze")
async def analyze_multi_document(
    http_request: Request,
    files: list[UploadFile] = File(...),
    use_jurisprudence: bool = Form(True),
    session: AsyncSession = Depends(get_session),
    rate_user: Optional[User] = Depends(require_rate_limit),
    _feature: Optional[User] = Depends(require_feature("multi_document")),
):
    """Analyze multiple procurement documents for red flags and cross-document issues.

    Upload 2-5 files (.pdf, .docx, .txt, .md) for unified analysis.
    """
    if not is_db_available():
        raise HTTPException(status_code=503, detail="Baza de date indisponibilă")

    if len(files) < 2:
        raise HTTPException(status_code=400, detail="Minim 2 documente necesare.")
    if len(files) > 5:
        raise HTTPException(status_code=400, detail="Maximum 5 documente permise.")

    # Read all files
    documents = []
    for f in files:
        content = await f.read()
        documents.append({
            "filename": f.filename or "unknown",
            "content": content,
            "mime_type": f.content_type or "",
        })

    logger.info("multi_doc_request", doc_count=len(documents))

    try:
        llm = await get_active_llm_provider(session)
        analyzer = MultiDocumentAnalyzer(llm_provider=llm)

        result = await asyncio.wait_for(
            analyzer.analyze(
                session=session,
                documents=documents,
                use_jurisprudence=use_jurisprudence,
            ),
            timeout=ENDPOINT_TIMEOUT,
        )

        await increment_usage(rate_user, http_request)
        return result

    except asyncio.TimeoutError:
        raise HTTPException(
            status_code=504,
            detail="Timeout la analiza multi-document. Reîncearcă cu mai puține documente.",
        )
    except Exception as e:
        logger.error("multi_doc_error", error=str(e), exc_info=True)
        raise HTTPException(status_code=500, detail=f"Eroare: {str(e)}")
