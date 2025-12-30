"""Red flags detection API endpoints."""

from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.db.session import get_session
from app.services.redflags_analyzer import RedFlagsAnalyzer

router = APIRouter()
logger = get_logger(__name__)


class RedFlagItem(BaseModel):
    """A detected red flag."""

    category: str
    severity: str
    clause: str
    issue: str
    legal_reference: str
    recommendation: str
    decision_refs: list[str] = Field(default_factory=list)


class RedFlagsRequest(BaseModel):
    """Request for red flags analysis."""

    text: str = Field(..., min_length=10, description="Document text to analyze")
    use_jurisprudence: bool = Field(
        default=True,
        description="Whether to include CNSC jurisprudence in analysis"
    )


class RedFlagsResponse(BaseModel):
    """Response from red flags analysis."""

    red_flags: list[RedFlagItem]
    total_count: int
    critical_count: int
    medium_count: int
    low_count: int
    used_jurisprudence: bool


@router.post("/", response_model=RedFlagsResponse)
async def analyze_red_flags(
    request: RedFlagsRequest,
    session: AsyncSession = Depends(get_session)
) -> RedFlagsResponse:
    """
    Analyze procurement document for red flags.

    Detects potentially illegal or restrictive clauses in procurement
    documentation (specifications, tender documents, etc.).

    Categories detected:
    - Excessive similar experience requirements
    - Disproportionate turnover requirements
    - Restrictive certifications
    - Excessive dedicated personnel
    - Discriminatory clauses
    - Unrealistic deadlines
    - Restrictive technical criteria

    Each red flag includes:
    - Category and severity
    - Exact problematic clause
    - Issue description
    - Legal reference (Legea 98/2016, HG 395/2016)
    - Recommendation for modification
    - Related CNSC decisions (if use_jurisprudence=true)
    """
    logger.info(
        "red_flags_analysis_request",
        text_length=len(request.text),
        use_jurisprudence=request.use_jurisprudence
    )

    try:
        # Initialize analyzer
        analyzer = RedFlagsAnalyzer()

        # Analyze document
        red_flags_data = await analyzer.analyze(
            document_text=request.text,
            session=session if request.use_jurisprudence else None,
            use_jurisprudence=request.use_jurisprudence
        )

        # Convert to Pydantic models
        red_flags = [RedFlagItem(**flag) for flag in red_flags_data]

        # Count by severity
        critical_count = sum(1 for rf in red_flags if rf.severity == "CRITICĂ")
        medium_count = sum(1 for rf in red_flags if rf.severity == "MEDIE")
        low_count = sum(1 for rf in red_flags if rf.severity == "SCĂZUTĂ")

        logger.info(
            "red_flags_analysis_complete",
            total=len(red_flags),
            critical=critical_count,
            medium=medium_count,
            low=low_count
        )

        return RedFlagsResponse(
            red_flags=red_flags,
            total_count=len(red_flags),
            critical_count=critical_count,
            medium_count=medium_count,
            low_count=low_count,
            used_jurisprudence=request.use_jurisprudence and len(red_flags_data) > 0
        )

    except Exception as e:
        logger.error("red_flags_analysis_error", error=str(e), exc_info=True)
        raise HTTPException(
            status_code=500,
            detail=f"Eroare la analiza red flags: {str(e)}"
        )
