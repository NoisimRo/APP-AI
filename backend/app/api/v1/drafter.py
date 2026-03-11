"""Drafter API endpoint for generating legal complaints.

Uses RAG vector search to ground the complaint in actual CNSC jurisprudence.
"""

import time

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.db.session import get_session
from app.models.decision import ArgumentareCritica, DecizieCNSC
from app.services.embedding import EmbeddingService
from app.services.llm.factory import get_active_llm_provider, get_embedding_provider
from app.services.llm.streaming import create_sse_response
from app.services.rag import RAGService
from app.api.v1.scopes import get_scope_decision_ids

router = APIRouter()
logger = get_logger(__name__)


class DrafterRequest(BaseModel):
    """Request payload for complaint drafting."""

    facts: str = Field(..., min_length=1, max_length=200000)
    authority_args: str = Field(default="", max_length=200000)
    legal_grounds: str = Field(default="", max_length=50000)
    scope_id: str | None = Field(None, description="Optional scope ID for pre-filtering decisions")


class DrafterResponse(BaseModel):
    """Response payload for complaint drafting."""

    content: str
    decision_refs: list[str] = Field(default_factory=list)


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
        search_query = request.facts[:3000]
        query_vector = await embedding_service.embed_query(search_query)
        t_embed = time.monotonic()
        logger.info("timing_drafter_embed", duration_s=round(t_embed - t0, 2))

        stmt = (
            select(
                ArgumentareCritica,
                ArgumentareCritica.embedding.cosine_distance(query_vector).label("distance"),
            )
            .where(ArgumentareCritica.embedding.isnot(None))
            .order_by("distance")
            .limit(8)
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
            dec_ids = list({arg.decizie_id for arg, _ in relevant_chunks})
            dec_result = await session.execute(
                select(DecizieCNSC).where(DecizieCNSC.id.in_(dec_ids))
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
                if arg.argumente_contestator:
                    part += f"  Argumente contestator: {arg.argumente_contestator[:400]}\n"
                if arg.jurisprudenta_contestator:
                    part += f"  Jurisprudență contestator: {'; '.join(arg.jurisprudenta_contestator)}\n"
                if arg.argumentatie_cnsc:
                    part += f"  Argumentație CNSC: {arg.argumentatie_cnsc[:400]}\n"
                if arg.jurisprudenta_cnsc:
                    part += f"  Jurisprudență CNSC: {'; '.join(arg.jurisprudenta_cnsc)}\n"
                if arg.castigator_critica and arg.castigator_critica != "unknown":
                    part += f"  Câștigător: {arg.castigator_critica}\n"
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
                relevant_chunks, session, limit=6,
            )
            if leg_fragments:
                leg_contexts = rag_service._build_legislation_context(leg_fragments)
                legislation_context = "\n\n".join(leg_contexts)
                logger.info("drafter_legislation_linked", fragments=len(leg_fragments))
        except Exception as e:
            logger.warning("drafter_legislation_linking_failed", error=str(e))

    logger.info("timing_drafter_context_total", duration_s=round(time.monotonic() - t0, 2))

    # Build prompt with jurisprudence + legislation context
    jurisprudence_section = ""
    if jurisprudence_context:
        jurisprudence_section = f"""

=== JURISPRUDENȚĂ CNSC RELEVANTĂ (din baza de date) ===
{jurisprudence_context}
=== SFÂRȘIT JURISPRUDENȚĂ ===

IMPORTANT: Folosește activ jurisprudența CNSC de mai sus în argumentarea contestației.
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

Folosește textul exact al articolelor de lege de mai sus în argumentarea contestației.
Citează articolele cu formularea completă (ex: "conform art. 2 alin. (2) lit. e) din Legea nr. 98/2016")."""

    prompt = f"""Ești un avocat expert în achiziții publice din România. Redactează o contestație către CNSC (Consiliul Național de Soluționare a Contestațiilor).

Detalii faptice: {request.facts}

Argumente Autoritate Contractantă: {request.authority_args or 'Nu au fost furnizate.'}

Temei legal: {request.legal_grounds or 'Nu a fost specificat.'}
{jurisprudence_section}
{legislation_section}
Structura obligatorie a contestației:
1. **Părțile** - Identificarea contestatorului și a autorității contractante
2. **Situația de fapt** - Descrierea cronologică a evenimentelor
3. **Motivele contestației** - Dezvoltare amplă a argumentelor juridice, cu referire la legislația aplicabilă (Legea 98/2016, HG 395/2016, Legea 101/2016) și la jurisprudența CNSC disponibilă
4. **Solicitare de suspendare** a procedurii de atribuire
5. **Dispozitiv** - Solicitările concrete ale contestatorului

Redactează contestația în limba română, folosind limbaj juridic formal și profesionist.
Fiecare secțiune trebuie să fie clar delimitată cu titluri bold.
Include referințe la articole de lege relevante.

INSTRUCȚIUNI DE STIL:
- Scrie SINTETIC și CLAR — fiecare propoziție trebuie să aducă valoare juridică
- Evidențiază clar: criticile, argumentele, dovezile și jurisprudența
- NU dilua textul cu generalități, repetiții sau vorbărie goală
- Folosește paragrafe scurte, numerotare și titluri pentru structură clară
- Contestația poate avea până la 15 pagini — folosește spațiul pentru SUBSTANȚĂ, nu umplutură
- Fiecare critică trebuie să conțină: faptele relevante, norma legală încălcată, argumentația juridică și dovada
- Citează articole de lege cu text exact când este disponibil
- Preferă citate verbatim din jurisprudența CNSC furnizată"""

    return prompt, decision_refs


@router.post("/", response_model=DrafterResponse)
async def draft_complaint(
    request: DrafterRequest,
    session: AsyncSession = Depends(get_session),
) -> DrafterResponse:
    """Generate a legal complaint draft using LLM with RAG jurisprudence."""
    logger.info(
        "draft_complaint_request",
        facts_length=len(request.facts),
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
            decision_refs=decision_refs,
        )
        return DrafterResponse(content=response_text, decision_refs=decision_refs)

    except Exception as e:
        logger.error("draft_complaint_error", error=str(e))
        raise


@router.post("/stream")
async def draft_complaint_stream(
    request: DrafterRequest,
    session: AsyncSession = Depends(get_session),
):
    """Stream a legal complaint draft via SSE."""
    logger.info("draft_complaint_stream_request", facts_length=len(request.facts))

    # Resolve scope
    scope_ids = None
    if request.scope_id:
        scope_ids = await get_scope_decision_ids(request.scope_id, session)
        if scope_ids is None:
            raise HTTPException(status_code=404, detail="Scope not found")

    prompt, decision_refs = await _build_drafter_context(request, session, scope_decision_ids=scope_ids)
    llm = await get_active_llm_provider(session)

    return await create_sse_response(
        llm=llm,
        prompt=prompt,
        temperature=0.3,
        max_tokens=16384,
        metadata={"decision_refs": decision_refs},
    )
