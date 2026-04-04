"""Drafter API endpoint for generating legal complaints.

Uses RAG vector search to ground the complaint in actual CNSC jurisprudence.
Supports two document types: contestație (to CNSC) and plângere (to court).
"""

import time
from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.core.deps import require_feature
from app.core.rate_limiter import require_rate_limit, increment_usage
from app.db.session import get_session
from app.models.decision import ArgumentareCritica, DecizieCNSC, User
from app.services.embedding import EmbeddingService
from app.services.llm.factory import get_active_llm_provider, get_embedding_provider
from app.services.llm.streaming import create_sse_response
from app.services.rag import RAGService
from app.api.v1.scopes import get_scope_decision_ids

router = APIRouter()
logger = get_logger(__name__)


# =============================================================================
# DOCUMENT TYPE DEFINITIONS
# =============================================================================

DOCUMENT_TYPES = {
    "contestatie": {
        "name": "Contestație la CNSC",
        "description": "Contestație formulată către Consiliul Național de Soluționare a Contestațiilor",
        "destinatar": "CNSC",
    },
    "plangere": {
        "name": "Plângere la Curtea de Apel",
        "description": "Plângere împotriva deciziei CNSC, formulată la Curtea de Apel competentă",
        "destinatar": "Curtea de Apel",
    },
}


# =============================================================================
# REQUEST / RESPONSE MODELS
# =============================================================================

class DrafterRequest(BaseModel):
    """Request payload for complaint drafting."""

    facts: str = Field(..., min_length=1, max_length=200000)
    authority_args: str = Field(default="", max_length=200000)
    legal_grounds: str = Field(default="", max_length=50000)
    scope_id: str | None = Field(None, description="Optional scope ID for pre-filtering decisions")
    doc_type: Literal["contestatie", "plangere"] = Field(
        default="contestatie",
        description="Document type: contestatie (CNSC) or plangere (court appeal)",
    )
    # New fields for richer context
    remedii_solicitate: str = Field(
        default="",
        max_length=50000,
        description="Specific remedies requested (e.g., anulare act, reevaluare oferte)",
    )
    detalii_procedura: str = Field(
        default="",
        max_length=100000,
        description="Procedure details: procurement type, estimated value, award criterion",
    )
    numar_decizie_cnsc: str = Field(
        default="",
        max_length=200,
        description="For plângere: CNSC decision number being appealed",
    )


class DrafterResponse(BaseModel):
    """Response payload for complaint drafting."""

    content: str
    decision_refs: list[str] = Field(default_factory=list)
    doc_type: str = "contestatie"


# =============================================================================
# PROMPT BUILDERS PER DOCUMENT TYPE
# =============================================================================

def _build_contestatie_prompt(
    request: DrafterRequest,
    jurisprudence_section: str,
    legislation_section: str,
) -> str:
    """Build prompt for contestație (complaint to CNSC)."""

    procedure_info = ""
    if request.detalii_procedura:
        procedure_info = f"\nDetalii procedură de achiziție:\n{request.detalii_procedura}\n"

    remedies_info = ""
    if request.remedii_solicitate:
        remedies_info = f"\nRemedii solicitate de contestator:\n{request.remedii_solicitate}\n"

    return f"""Ești un avocat expert în achiziții publice din România cu experiență vastă în litigii CNSC.
Redactează o CONTESTAȚIE completă către CNSC (Consiliul Național de Soluționare a Contestațiilor).

=== INFORMAȚII PRIMITE DE LA CLIENT ===

Situația de fapt:
{request.facts}
{procedure_info}
Argumentele Autorității Contractante (dacă sunt cunoscute):
{request.authority_args or 'Nu au fost furnizate.'}

Temei legal indicat de client:
{request.legal_grounds or 'Nu a fost specificat — identifică tu temeiul legal aplicabil.'}
{remedies_info}
{jurisprudence_section}
{legislation_section}

=== STRUCTURA OBLIGATORIE A CONTESTAȚIEI ===

Contestația TREBUIE să conțină următoarele secțiuni, fiecare clar delimitată:

1. **ANTET ȘI ADRESARE**
   - Către: Consiliul Național de Soluționare a Contestațiilor
   - Contestator: [de completat de client]
   - Autoritate contractantă: [extrage din fapte sau marchează ca "[AC]"]
   - Procedura de atribuire: [referința procedurii, dacă e menționată]

2. **OBIECTUL CONTESTAȚIEI**
   - Actul atacat (comunicare rezultat, decizie de respingere, clauze documentație, etc.)
   - Data comunicării / publicării actului contestat
   - Temeiul legal al contestației (art. 8-10 din Legea 101/2016)

3. **SITUAȚIA DE FAPT**
   - Descrierea cronologică detaliată a evenimentelor
   - Fapte relevante cu date exacte
   - Contextul procedurii de achiziție publică

4. **CRITICILE CONTESTATORULUI** (secțiunea centrală — dezvoltă fiecare critică separat)
   Pentru FIECARE critică:
   a) Identificarea actului/clauzei contestate
   b) Norma legală încălcată (cu text exact din lege)
   c) Argumentația juridică detaliată
   d) Jurisprudența CNSC relevantă (cu citate verbatim din decizii)
   e) Dovezile și documentele justificative

5. **SOLICITARE DE MĂSURI PROVIZORII / SUSPENDARE**
   - Solicitarea suspendării procedurii (art. 26 din Legea 101/2016)
   - Motivarea urgenței și a prejudiciului iminent

6. **DISPOZITIV — SOLICITĂRI CONCRETE**
   - Enumerarea clară a tuturor solicitărilor
   - Formulare juridică precisă ("Solicităm admiterea contestației și...")
   - Remediile cerute: anulare act, reevaluare, refacere documentație, etc.

7. **SEMNĂTURĂ ȘI ANEXE**
   - Spațiu pentru semnătură contestator
   - Lista documentelor anexate

=== INSTRUCȚIUNI DE REDACTARE ===

SUBSTANȚĂ:
- Fiecare propoziție trebuie să aducă valoare juridică — ZERO umplutură
- Dezvoltă criticile cu argumente concrete, nu generalități
- Citează textul exact al articolelor de lege când este disponibil
- Folosește citate verbatim din jurisprudența CNSC furnizată
- Fiecare critică = fapte + normă + argumentație + dovadă + jurisprudență

STIL:
- Limbaj juridic formal, profesionist, ferm dar respectuos
- Paragrafe scurte și clare, cu numerotare sistematică
- Contestația poate avea 10-20 pagini — folosește spațiul pentru SUBSTANȚĂ
- Evidențiază normele legale cu bold sau formatare distinctivă
- Folosește conectori logici clari ("în consecință", "prin urmare", "cu atât mai mult cu cât")

RESTRICȚII:
- Citează DOAR deciziile CNSC furnizate în secțiunea de jurisprudență
- NU inventa numere de decizii sau referințe legislative inexistente
- NU include articole de lege al căror text nu îl cunoști exact"""


def _build_plangere_prompt(
    request: DrafterRequest,
    jurisprudence_section: str,
    legislation_section: str,
) -> str:
    """Build prompt for plângere (appeal to court against CNSC decision)."""

    procedure_info = ""
    if request.detalii_procedura:
        procedure_info = f"\nDetalii procedură de achiziție:\n{request.detalii_procedura}\n"

    cnsc_decision_ref = ""
    if request.numar_decizie_cnsc:
        cnsc_decision_ref = f"\nDecizia CNSC atacată: {request.numar_decizie_cnsc}\n"

    remedies_info = ""
    if request.remedii_solicitate:
        remedies_info = f"\nRemedii solicitate:\n{request.remedii_solicitate}\n"

    return f"""Ești un avocat expert în achiziții publice din România cu experiență vastă în litigii de achiziții publice.
Redactează o PLÂNGERE la Curtea de Apel competentă împotriva unei decizii CNSC.

=== INFORMAȚII PRIMITE DE LA CLIENT ===

Situația de fapt și motivele plângerii:
{request.facts}
{cnsc_decision_ref}{procedure_info}
Argumentele avute în vedere de CNSC / Autoritatea Contractantă:
{request.authority_args or 'Nu au fost furnizate.'}

Temei legal indicat de client:
{request.legal_grounds or 'Nu a fost specificat — identifică tu temeiul legal aplicabil.'}
{remedies_info}
{jurisprudence_section}
{legislation_section}

=== STRUCTURA OBLIGATORIE A PLÂNGERII ===

1. **ANTET ȘI ADRESARE**
   - Către: Curtea de Apel [competentă]
   - Petent (fost contestator): [de completat]
   - Intimat: CNSC + Autoritatea Contractantă
   - Decizia CNSC atacată: [nr. și data deciziei]

2. **OBIECTUL PLÂNGERII**
   - Decizia CNSC atacată (număr, dată, conținut pe scurt)
   - Temeiul legal: art. 29-36 din Legea nr. 101/2016
   - Termenul de depunere (10 zile de la comunicarea deciziei CNSC)

3. **SITUAȚIA DE FAPT**
   - Istoricul procedurii de achiziție
   - Contestația formulată la CNSC și obiectul acesteia
   - Soluția CNSC și motivarea acesteia (pe scurt)
   - Motivele pentru care decizia CNSC este nelegală/netemeinică

4. **MOTIVELE PLÂNGERII** (secțiunea centrală)
   Pentru FIECARE motiv de nelegalitate/netemeinicie:
   a) Ce a reținut CNSC (citare din decizie)
   b) De ce argumentarea CNSC este greșită
   c) Norma legală interpretată/aplicată incorect
   d) Argumentația juridică corectă (cu referință la lege și jurisprudență)
   e) Jurisprudență relevantă (CNSC și instanțe)

5. **ÎN DREPT**
   - Art. 29-36 din Legea 101/2016
   - Articole din Legea 98/2016, HG 395/2016 relevante
   - Jurisprudența aplicabilă

6. **SOLICITARE DE SUSPENDARE A EXECUTĂRII**
   - Suspendarea executării deciziei CNSC (art. 34 din Legea 101/2016)
   - Motivarea urgenței

7. **DISPOZITIV**
   - Admiterea plângerii
   - Modificarea/desființarea deciziei CNSC
   - Solicitări concrete (admiterea contestației inițiale, reevaluare, etc.)

8. **SEMNĂTURĂ ȘI ANEXE**
   - Anexarea obligatorie a deciziei CNSC atacate
   - Dovada comunicării deciziei CNSC
   - Documentele justificative

=== INSTRUCȚIUNI DE REDACTARE ===

SUBSTANȚĂ:
- Plângerea trebuie să demonstreze CONCRET de ce decizia CNSC este greșită
- Atacă fiecare argument al CNSC în parte, nu te limita la generalități
- Citează textul exact al articolelor de lege
- Folosește jurisprudența furnizată pentru a susține argumentele
- Ton ferm dar respectuos față de instanță și CNSC

STIL:
- Limbaj juridic formal, adecvat instanțelor judecătorești
- Structură clară cu numerotare și subtitluri
- Poate avea 10-20 pagini

RESTRICȚII:
- Citează DOAR deciziile furnizate — NU inventa referințe
- NU include texte de lege al căror conținut nu îl cunoști exact"""


# =============================================================================
# RAG CONTEXT BUILDER
# =============================================================================

async def _build_drafter_context(
    request: DrafterRequest,
    session: AsyncSession,
    scope_decision_ids: list[str] | None = None,
) -> tuple[str, list[str]]:
    """Build drafter prompt and search for relevant jurisprudence.

    Returns:
        Tuple of (prompt, decision_refs).
    """
    t0 = time.monotonic()
    embedding_service = EmbeddingService(llm_provider=get_embedding_provider())

    jurisprudence_context = ""
    decision_refs: list[str] = []
    relevant_chunks: list[tuple] = []

    try:
        # Use facts + legal grounds for better search relevance
        search_text = request.facts[:2500]
        if request.legal_grounds:
            search_text += " " + request.legal_grounds[:500]
        query_vector = await embedding_service.embed_query(search_text)
        t_embed = time.monotonic()
        logger.info("timing_drafter_embed", duration_s=round(t_embed - t0, 2))

        stmt = (
            select(
                ArgumentareCritica,
                ArgumentareCritica.embedding.cosine_distance(query_vector).label("distance"),
            )
            .where(ArgumentareCritica.embedding.isnot(None))
            .order_by("distance")
            .limit(12)
        )

        # Scope pre-filter
        if scope_decision_ids is not None:
            stmt = stmt.where(ArgumentareCritica.decizie_id.in_(scope_decision_ids))

        result = await session.execute(stmt)
        rows = result.all()

        relevant_chunks = [
            (row.ArgumentareCritica, row.distance)
            for row in rows
            if row.distance < 0.5
        ]

        if relevant_chunks:
            from sqlalchemy.orm import defer
            dec_ids = list({arg.decizie_id for arg, _ in relevant_chunks})
            dec_result = await session.execute(
                select(DecizieCNSC).options(defer(DecizieCNSC.text_integral)).where(DecizieCNSC.id.in_(dec_ids))
            )
            decisions = {d.id: d for d in dec_result.scalars().all()}
            decision_refs = [d.external_id for d in decisions.values()]

            context_parts = []
            for arg, dist in relevant_chunks:
                dec = decisions.get(arg.decizie_id)
                if not dec:
                    continue
                similarity = 1.0 - dist
                part = f"Decizia {dec.external_id} (relevanță: {similarity:.2f}):\n"
                part += f"  Soluție: {dec.solutie_contestatie or 'N/A'}\n"
                part += f"  Critică: {arg.cod_critica}\n"
                if arg.castigator_critica and arg.castigator_critica != "unknown":
                    part += f"  Câștigător critică: {arg.castigator_critica}\n"
                if arg.argumente_contestator:
                    part += f"  Argumente contestator: {arg.argumente_contestator[:600]}\n"
                if arg.jurisprudenta_contestator:
                    part += f"  Jurisprudență invocată de contestator: {'; '.join(arg.jurisprudenta_contestator)}\n"
                if arg.argumente_ac:
                    part += f"  Argumente AC: {arg.argumente_ac[:400]}\n"
                if arg.elemente_retinute_cnsc:
                    part += f"  Elemente reținute de CNSC: {arg.elemente_retinute_cnsc[:500]}\n"
                if arg.argumentatie_cnsc:
                    part += f"  Argumentație CNSC: {arg.argumentatie_cnsc[:600]}\n"
                if arg.jurisprudenta_cnsc:
                    part += f"  Jurisprudență citată de CNSC: {'; '.join(arg.jurisprudenta_cnsc)}\n"
                context_parts.append(part)

            jurisprudence_context = "\n---\n".join(context_parts)

            logger.info(
                "drafter_jurisprudence_found",
                decisions=len(decisions),
                chunks=len(relevant_chunks),
                top_similarity=1.0 - relevant_chunks[0][1],
            )

    except Exception as e:
        logger.warning("drafter_jurisprudence_search_failed", error=str(e))

    t_search = time.monotonic()
    logger.info("timing_drafter_search", duration_s=round(t_search - t0, 2))

    # Legislation Linking: find actual legal text referenced by matched chunks
    legislation_context = ""
    if relevant_chunks:
        try:
            rag_service = RAGService()
            leg_fragments = await rag_service._find_legislation_for_chunks(
                relevant_chunks, session, limit=8,
            )
            if leg_fragments:
                leg_contexts = rag_service._build_legislation_context(leg_fragments)
                legislation_context = "\n\n".join(leg_contexts)
                logger.info("drafter_legislation_linked", fragments=len(leg_fragments))
        except Exception as e:
            logger.warning("drafter_legislation_linking_failed", error=str(e))

    logger.info("timing_drafter_context_total", duration_s=round(time.monotonic() - t0, 2))

    # Build jurisprudence and legislation sections for prompt
    jurisprudence_section = ""
    if jurisprudence_context:
        jurisprudence_section = f"""
=== JURISPRUDENȚĂ CNSC RELEVANTĂ (din baza de date) ===
{jurisprudence_context}
=== SFÂRȘIT JURISPRUDENȚĂ ===

IMPORTANT: Folosește activ jurisprudența CNSC de mai sus în argumentare.
Citează deciziile specifice (ex: "Conform deciziei {decision_refs[0] if decision_refs else 'BO...'}...").
Poți cita DOAR deciziile furnizate mai sus. NU inventa alte numere de decizii."""
    else:
        jurisprudence_section = """
Notă: Nu s-a găsit jurisprudență CNSC specifică în baza de date. NU cita și NU inventa numere de decizii CNSC."""

    legislation_section = ""
    if legislation_context:
        legislation_section = f"""
=== LEGISLAȚIE RELEVANTĂ (din baza de date) ===
{legislation_context}
=== SFÂRȘIT LEGISLAȚIE ===

Folosește textul exact al articolelor de lege de mai sus în argumentare.
Citează articolele cu formularea completă (ex: "conform art. 2 alin. (2) lit. e) din Legea nr. 98/2016")."""

    # Select prompt builder based on document type
    if request.doc_type == "plangere":
        prompt = _build_plangere_prompt(request, jurisprudence_section, legislation_section)
    else:
        prompt = _build_contestatie_prompt(request, jurisprudence_section, legislation_section)

    return prompt, decision_refs


# =============================================================================
# API ENDPOINTS
# =============================================================================

@router.get("/types")
async def get_document_types():
    """Get available document types for the drafter."""
    return DOCUMENT_TYPES


@router.post("/", response_model=DrafterResponse)
async def draft_complaint(
    request: DrafterRequest,
    http_request: Request,
    session: AsyncSession = Depends(get_session),
    rate_user: Optional[User] = Depends(require_rate_limit),
    _feature: Optional[User] = Depends(require_feature("drafter")),
) -> DrafterResponse:
    """Generate a legal complaint draft using LLM with RAG jurisprudence."""
    logger.info(
        "draft_complaint_request",
        facts_length=len(request.facts),
        doc_type=request.doc_type,
        has_authority_args=bool(request.authority_args),
        has_legal_grounds=bool(request.legal_grounds),
    )

    # Resolve scope
    scope_ids = None
    if request.scope_id:
        scope_ids = await get_scope_decision_ids(request.scope_id, session)
        if scope_ids is None:
            raise HTTPException(status_code=404, detail="Scope not found")

    prompt, decision_refs = await _build_drafter_context(request, session, scope_decision_ids=scope_ids)
    llm = await get_active_llm_provider(session)

    try:
        response_text = await llm.complete(
            prompt=prompt,
            temperature=0.3,
            max_tokens=16384,
        )

        logger.info(
            "draft_complaint_generated",
            length=len(response_text),
            doc_type=request.doc_type,
            decision_refs=decision_refs,
        )
        await increment_usage(rate_user, http_request)
        return DrafterResponse(
            content=response_text,
            decision_refs=decision_refs,
            doc_type=request.doc_type,
        )

    except Exception as e:
        logger.error("draft_complaint_error", error=str(e))
        raise


@router.post("/stream")
async def draft_complaint_stream(
    request: DrafterRequest,
    http_request: Request,
    session: AsyncSession = Depends(get_session),
    rate_user: Optional[User] = Depends(require_rate_limit),
    _feature: Optional[User] = Depends(require_feature("drafter")),
):
    """Stream a legal complaint draft via SSE."""
    logger.info(
        "draft_complaint_stream_request",
        facts_length=len(request.facts),
        doc_type=request.doc_type,
    )
    await increment_usage(rate_user, http_request)

    # Resolve scope
    scope_ids = None
    if request.scope_id:
        scope_ids = await get_scope_decision_ids(request.scope_id, session)
        if scope_ids is None:
            raise HTTPException(status_code=404, detail="Scope not found")

    prompt, decision_refs = await _build_drafter_context(request, session, scope_decision_ids=scope_ids)
    llm = await get_active_llm_provider(session)

    doc_label = "contestația" if request.doc_type == "contestatie" else "plângerea"
    status_msgs = []
    if decision_refs:
        status_msgs.append(f"Am găsit {len(decision_refs)} decizii CNSC relevante")
    status_msgs.append(f"Se redactează {doc_label}...")

    return await create_sse_response(
        llm=llm,
        prompt=prompt,
        temperature=0.3,
        max_tokens=16384,
        metadata={"decision_refs": decision_refs, "doc_type": request.doc_type},
        status_messages=status_msgs,
    )
