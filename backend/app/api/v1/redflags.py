"""Red flags detection API endpoints."""

import asyncio

from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.db.session import get_session
from app.services.llm.factory import get_active_llm_provider
from app.services.redflags_analyzer import RedFlagsAnalyzer

# Overall endpoint timeout (seconds) — generous for large documents
ENDPOINT_TIMEOUT = 300

router = APIRouter()
logger = get_logger(__name__)


class LegalReference(BaseModel):
    """A verified legal article reference from the legislation database."""

    citare: str  # "art. 2 alin. (2) lit. a) și b)"
    act_normativ: str  # "Legea 98/2016"
    text_extras: str = ""  # excerpt from the real article text


class RedFlagItem(BaseModel):
    """A detected and grounded red flag."""

    clause: str
    issue: str
    severity: str
    legal_references: list[LegalReference] = Field(default_factory=list)
    recommendation: str = ""
    decision_refs: list[str] = Field(default_factory=list)


class RedFlagsRequest(BaseModel):
    """Request for red flags analysis."""

    text: str = Field(..., min_length=10, description="Document text to analyze")
    use_jurisprudence: bool = Field(
        default=True,
        description="Whether to ground with real legislation and CNSC jurisprudence"
    )


class RedFlagsResponse(BaseModel):
    """Response from red flags analysis."""

    red_flags: list[RedFlagItem]
    total_count: int
    critical_count: int
    medium_count: int
    low_count: int
    grounded: bool  # True if legislation/jurisprudence was used


@router.post("/", response_model=RedFlagsResponse)
async def analyze_red_flags(
    request: RedFlagsRequest,
    session: AsyncSession = Depends(get_session)
) -> RedFlagsResponse:
    """Analyze procurement document for red flags.

    Uses a two-pass approach:
    1. Dynamic detection — LLM identifies problematic clauses
    2. Grounding — each clause is verified against real legislation
       (Legea 98/2016, HG 395/2016) and CNSC jurisprudence

    Each red flag includes:
    - Exact problematic clause from the document
    - Issue description
    - Severity (CRITICĂ, MEDIE, SCĂZUTĂ)
    - Verified legal references with article text
    - Related CNSC decisions (if found)
    - Recommendation based on real legislation
    """
    logger.info(
        "red_flags_analysis_request",
        text_length=len(request.text),
        use_jurisprudence=request.use_jurisprudence
    )

    try:
        llm = await get_active_llm_provider(session)
        analyzer = RedFlagsAnalyzer(llm_provider=llm)

        red_flags_data = await asyncio.wait_for(
            analyzer.analyze(
                document_text=request.text,
                session=session if request.use_jurisprudence else None,
                use_jurisprudence=request.use_jurisprudence,
            ),
            timeout=ENDPOINT_TIMEOUT,
        )

        # Convert to Pydantic models
        red_flags = []
        for flag_data in red_flags_data:
            # Handle legal_references conversion
            legal_refs = []
            for ref in flag_data.get("legal_references", []):
                if isinstance(ref, dict):
                    legal_refs.append(LegalReference(
                        citare=ref.get("citare", ""),
                        act_normativ=ref.get("act_normativ", ""),
                        text_extras=ref.get("text_extras", ""),
                    ))

            red_flags.append(RedFlagItem(
                clause=flag_data.get("clause", ""),
                issue=flag_data.get("issue", ""),
                severity=flag_data.get("severity", "MEDIE"),
                legal_references=legal_refs,
                recommendation=flag_data.get("recommendation", ""),
                decision_refs=flag_data.get("decision_refs", []),
            ))

        critical_count = sum(1 for rf in red_flags if rf.severity == "CRITICĂ")
        medium_count = sum(1 for rf in red_flags if rf.severity == "MEDIE")
        low_count = sum(1 for rf in red_flags if rf.severity == "SCĂZUTĂ")

        logger.info(
            "red_flags_analysis_complete",
            total=len(red_flags),
            critical=critical_count,
            medium=medium_count,
            low=low_count,
            grounded=request.use_jurisprudence,
        )

        return RedFlagsResponse(
            red_flags=red_flags,
            total_count=len(red_flags),
            critical_count=critical_count,
            medium_count=medium_count,
            low_count=low_count,
            grounded=request.use_jurisprudence and any(
                rf.legal_references or rf.decision_refs for rf in red_flags
            ),
        )

    except asyncio.TimeoutError:
        logger.error(
            "red_flags_analysis_timeout",
            text_length=len(request.text),
            timeout=ENDPOINT_TIMEOUT,
        )
        raise HTTPException(
            status_code=504,
            detail=(
                f"Analiza a depășit timpul limită ({ENDPOINT_TIMEOUT}s). "
                "Documentul este prea mare sau complex. Încercați cu o secțiune mai mică."
            ),
        )
    except Exception as e:
        logger.error("red_flags_analysis_error", error=str(e), exc_info=True)
        raise HTTPException(
            status_code=500,
            detail=f"Eroare la analiza red flags: {str(e)}"
        )
