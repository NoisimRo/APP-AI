"""Drafter API endpoint for generating legal complaints.

Uses RAG hybrid search (vector + trigram + RRF + query expansion) to ground
the complaint in actual CNSC jurisprudence and real legislation.
"""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.db.session import get_session
from app.models.decision import DecizieCNSC
from app.services.rag import RAGService
from app.services.llm.factory import get_active_llm_provider
from app.services.llm.streaming import create_sse_response
from sqlalchemy import select

router = APIRouter()
logger = get_logger(__name__)


class DrafterRequest(BaseModel):
    """Request payload for complaint drafting."""

    facts: str = Field(..., min_length=1, max_length=200000)
    authority_args: str = Field(default="", max_length=200000)
    legal_grounds: str = Field(default="", max_length=50000)


class DrafterResponse(BaseModel):
    """Response payload for complaint drafting."""

    content: str
    decision_refs: list[str] = Field(default_factory=list)


async def _build_drafter_context(
    request: DrafterRequest,
    session: AsyncSession,
    llm_provider=None,
) -> tuple[str, list[str]]:
    """Build drafter prompt using RAG hybrid search for jurisprudence + legislation.

    Returns:
        Tuple of (prompt, decision_refs).
    """
    rag = RAGService(llm_provider=llm_provider)

    jurisprudence_context = ""
    legislation_context = ""
    decision_refs: list[str] = []

    try:
        search_query = request.facts[:3000]

        # Hybrid search: vector + trigram + RRF + query expansion
        matched_chunks = await rag.hybrid_search(
            query=search_query,
            session=session,
            limit=8,
            expand=True,
        )

        # Filter by relevance (distance < 0.5 → similarity > 0.5)
        relevant_chunks = [(arg, dist) for arg, dist in matched_chunks if dist < 0.7]

        if relevant_chunks:
            # Load parent decisions
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
                    part += f"  Argumente contestator: {arg.argumente_contestator[:500]}\n"
                if arg.jurisprudenta_contestator:
                    part += f"  Jurisprudență contestator: {'; '.join(arg.jurisprudenta_contestator)}\n"
                if arg.argumentatie_cnsc:
                    part += f"  Argumentație CNSC: {arg.argumentatie_cnsc[:500]}\n"
                if arg.jurisprudenta_cnsc:
                    part += f"  Jurisprudență CNSC: {'; '.join(arg.jurisprudenta_cnsc)}\n"
                if arg.elemente_retinute_cnsc:
                    part += f"  Elemente reținute CNSC: {arg.elemente_retinute_cnsc[:400]}\n"
                if arg.castigator_critica and arg.castigator_critica != "unknown":
                    part += f"  Câștigător: {arg.castigator_critica}\n"
                context_parts.append(part)

            jurisprudence_context = "\n---\n".join(context_parts)

            # Extract legislation references from matched chunks
            leg_fragments = await rag.extract_legislation_from_chunks(
                relevant_chunks, session, max_total=6,
            )
            if leg_fragments:
                leg_parts = []
                for frag, act_name in leg_fragments:
                    body = frag.articol_complet or frag.text_fragment
                    leg_parts.append(f"--- {act_name}, {frag.citare} ---\n{body}")
                legislation_context = "\n\n".join(leg_parts)

            logger.info(
                "drafter_jurisprudence_found",
                decisions=len(decisions),
                chunks=len(relevant_chunks),
                legislation_fragments=len(leg_fragments) if leg_fragments else 0,
            )

    except Exception as e:
        logger.warning("drafter_jurisprudence_search_failed", error=str(e))

    # Build prompt with jurisprudence + legislation context
    context_sections = ""
    if jurisprudence_context:
        context_sections += f"""

=== JURISPRUDENȚĂ CNSC RELEVANTĂ (din baza de date) ===
{jurisprudence_context}
=== SFÂRȘIT JURISPRUDENȚĂ ===

IMPORTANT: Folosește activ jurisprudența CNSC de mai sus în argumentarea contestației.
Citează deciziile specifice (ex: "Conform deciziei {decision_refs[0] if decision_refs else 'BO...'}...").
Poți cita DOAR deciziile furnizate mai sus. NU inventa alte numere de decizii."""
    else:
        context_sections += """

Notă: Nu s-a găsit jurisprudență CNSC specifică în baza de date. NU cita și NU inventa numere de decizii CNSC."""

    if legislation_context:
        context_sections += f"""

=== LEGISLAȚIE APLICABILĂ (din baza de date) ===
{legislation_context}
=== SFÂRȘIT LEGISLAȚIE ===

Folosește articolele de lege de mai sus cu citare exactă în argumentare."""

    prompt = f"""Ești un avocat expert în achiziții publice din România. Redactează o contestație către CNSC (Consiliul Național de Soluționare a Contestațiilor).

Detalii faptice: {request.facts}

Argumente Autoritate Contractantă: {request.authority_args or 'Nu au fost furnizate.'}

Temei legal: {request.legal_grounds or 'Nu a fost specificat.'}
{context_sections}

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

    llm = await get_active_llm_provider(session)
    prompt, decision_refs = await _build_drafter_context(request, session, llm_provider=llm)

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

    llm = await get_active_llm_provider(session)
    prompt, decision_refs = await _build_drafter_context(request, session, llm_provider=llm)

    return await create_sse_response(
        llm=llm,
        prompt=prompt,
        temperature=0.3,
        max_tokens=16384,
        metadata={"decision_refs": decision_refs},
    )
