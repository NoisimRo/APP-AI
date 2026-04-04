"""Red flags detection API endpoints."""

import asyncio
from typing import Optional

from fastapi import APIRouter, HTTPException, Depends, Request
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.core.deps import require_feature
from app.core.rate_limiter import require_rate_limit, increment_usage
from app.db.session import get_session
from app.models.decision import User
from app.services.llm.factory import get_active_llm_provider
from app.services.llm.streaming import create_sse_response
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
    clarification_proposal: str = ""


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
    http_request: Request,
    session: AsyncSession = Depends(get_session),
    rate_user: Optional[User] = Depends(require_rate_limit),
    _feature: Optional[User] = Depends(require_feature("redflags")),
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
                clarification_proposal=flag_data.get("clarification_proposal", ""),
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

        await increment_usage(rate_user, http_request)

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


class RedFlagsClarificationRequest(BaseModel):
    """Request for generating a clarification request from selected red flags."""

    selected_flags: list[RedFlagItem] = Field(
        ..., min_length=1, description="Selected grounded red flags"
    )
    document_names: list[str] = Field(
        default_factory=list, description="Names of analyzed documents"
    )


@router.post("/clarification/stream")
async def generate_redflags_clarification_stream(
    request: RedFlagsClarificationRequest,
    http_request: Request,
    session: AsyncSession = Depends(get_session),
    rate_user: Optional[User] = Depends(require_rate_limit),
    _feature: Optional[User] = Depends(require_feature("redflags")),
):
    """Generate a formal clarification request from selected red flags via SSE streaming.

    Takes already-grounded red flags (with legal references and CNSC decision refs)
    and generates a structured clarification document addressed to the contracting authority.
    """
    logger.info(
        "redflags_clarification_request",
        num_flags=len(request.selected_flags),
        document_names=request.document_names,
    )

    llm = await get_active_llm_provider(session)

    # Build context from selected flags
    flags_context_parts = []
    all_decision_refs = set()

    for idx, flag in enumerate(request.selected_flags, 1):
        part = f"--- Red Flag #{idx} (Severitate: {flag.severity}) ---\n"
        part += f"Clauza: «{flag.clause}»\n"
        part += f"Problema: {flag.issue}\n"

        if flag.legal_references:
            refs_text = "; ".join(
                f"{ref.citare} din {ref.act_normativ}"
                + (f" — {ref.text_extras}" if ref.text_extras else "")
                for ref in flag.legal_references
            )
            part += f"Baza legală: {refs_text}\n"

        if flag.decision_refs:
            part += f"Jurisprudență CNSC: Deciziile {', '.join(flag.decision_refs)}\n"
            all_decision_refs.update(flag.decision_refs)

        if flag.recommendation:
            part += f"Recomandare: {flag.recommendation}\n"

        flags_context_parts.append(part)

    doc_names_text = ""
    if request.document_names:
        doc_names_text = f"\nDocumente analizate: {', '.join(request.document_names)}\n"

    system_prompt = """Ești un expert în achiziții publice din România. Redactează o Solicitare de Clarificări formală către autoritatea contractantă.

Pentru FIECARE cerință problematică din lista de mai jos, folosește EXACT această structură:

1. „Având în vedere cerința din documentația de atribuire conform căreia «[citează clauza exactă, verbatim]»"
2. „Faptul că [descrie problema identificată], [menționează baza legală: art. X din Legea Y], [menționează jurisprudența CNSC dacă există: Decizia BOxxxx]"
3. „Vă solicităm să fiți de acord cu reformularea [cerinței/factorului/clauzei etc] după cum urmează: [propunere concretă de reformulare]"

REGULI STRICTE:
- Folosește EXCLUSIV referințele legale și deciziile CNSC furnizate. NU inventa alte articole sau decizii.
- Citează clauza EXACT cum apare în document (verbatim, între ghilimele).
- Propunerea de reformulare trebuie să fie concretă, nu generică.
- Tonul: formal, profesionist, politicos dar ferm.
- Limba: română.
- Adaugă un antet formal la început (Către: Autoritatea Contractantă, Ref: Solicitare de Clarificări / Modificare Documentație) și un paragraf de încheiere."""

    prompt = f"""Redactează o Solicitare de Clarificări formală bazată pe următoarele probleme identificate în documentația de achiziție:
{doc_names_text}
=== RED FLAGS SELECTATE ===
{"".join(flags_context_parts)}
=== SFÂRȘIT RED FLAGS ===

Compune documentul de clarificare cu structura indicată pentru fiecare red flag."""

    return await create_sse_response(
        llm=llm,
        prompt=prompt,
        system_prompt=system_prompt,
        temperature=0.3,
        max_tokens=8192,
        metadata={"decision_refs": list(all_decision_refs)},
        status_messages=["Se redactează Solicitarea de Clarificări..."],
    )
