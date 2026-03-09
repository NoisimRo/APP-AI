"""Clarification request generation API endpoint.

Uses RAG hybrid search (vector + trigram + RRF + query expansion) to ground
clarifications in actual CNSC jurisprudence and real legislation.
"""

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.db.session import get_session
from app.models.decision import DecizieCNSC
from app.services.rag import RAGService
from app.services.llm.factory import get_active_llm_provider

router = APIRouter()
logger = get_logger(__name__)


class ClarificationRequest(BaseModel):
    """Request payload for clarification generation."""

    clause: str = Field(..., min_length=1, max_length=200000)


class ClarificationResponse(BaseModel):
    """Response payload for clarification generation."""

    content: str
    decision_refs: list[str] = Field(default_factory=list)


@router.post("/", response_model=ClarificationResponse)
async def generate_clarification(
    request: ClarificationRequest,
    session: AsyncSession = Depends(get_session),
) -> ClarificationResponse:
    """Generate a formal clarification request with RAG jurisprudence + legislation."""
    logger.info("clarification_request", clause_length=len(request.clause))

    llm = await get_active_llm_provider(session)
    rag = RAGService(llm_provider=llm)

    # Hybrid search for relevant CNSC jurisprudence
    jurisprudence_context = ""
    legislation_context = ""
    decision_refs: list[str] = []

    try:
        search_query = request.clause[:3000]

        # Hybrid search: vector + trigram + RRF + query expansion
        matched_chunks = await rag.hybrid_search(
            query=search_query,
            session=session,
            limit=6,
            expand=True,
        )

        # Filter by relevance
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
                    part += f"  Argumente contestator: {arg.argumente_contestator[:400]}\n"
                if arg.jurisprudenta_contestator:
                    part += f"  Jurisprudență contestator: {'; '.join(arg.jurisprudenta_contestator)}\n"
                if arg.argumentatie_cnsc:
                    part += f"  Argumentație CNSC: {arg.argumentatie_cnsc[:500]}\n"
                if arg.jurisprudenta_cnsc:
                    part += f"  Jurisprudență CNSC: {'; '.join(arg.jurisprudenta_cnsc)}\n"
                if arg.elemente_retinute_cnsc:
                    part += f"  Elemente reținute CNSC: {arg.elemente_retinute_cnsc[:300]}\n"
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
                "clarification_jurisprudence_found",
                decisions=len(decisions),
                chunks=len(relevant_chunks),
                legislation_fragments=len(leg_fragments) if leg_fragments else 0,
            )

    except Exception as e:
        logger.warning("clarification_jurisprudence_search_failed", error=str(e))

    # Build prompt with jurisprudence + legislation
    context_sections = ""
    if jurisprudence_context:
        context_sections += f"""

=== JURISPRUDENȚĂ CNSC RELEVANTĂ (din baza de date) ===
{jurisprudence_context}
=== SFÂRȘIT JURISPRUDENȚĂ ===

IMPORTANT: Folosește jurisprudența CNSC de mai sus pentru a fundamenta cererea de clarificare.
Citează deciziile specifice când susții că o cerință este restrictivă sau discriminatorie.
Poți cita DOAR deciziile furnizate mai sus. NU inventa alte numere de decizii CNSC."""
    else:
        context_sections += """

Notă: Nu s-a găsit jurisprudență CNSC specifică în baza de date. NU cita și NU inventa numere de decizii CNSC."""

    if legislation_context:
        context_sections += f"""

=== LEGISLAȚIE APLICABILĂ (din baza de date) ===
{legislation_context}
=== SFÂRȘIT LEGISLAȚIE ===

Folosește articolele de lege de mai sus cu citare exactă în argumentare."""

    prompt = f"""Ești un expert în achiziții publice din România. Clientul vrea să conteste sau clarifice următoarea clauză din documentația de atribuire:

"{request.clause}"
{context_sections}

Redactează o Cerere de Clarificare formală către autoritatea contractantă, care:
1. Este politicoasă și profesională
2. Sugerează subtil nelegalitatea sau caracterul restrictiv al cerinței
3. Face referire la legislația aplicabilă (Legea 98/2016, HG 395/2016)
4. Folosește jurisprudența CNSC disponibilă pentru a-și susține argumentele
5. Solicită justificarea obiectivă a cerinței
6. Propune formulări alternative mai puțin restrictive

Structură:
- **Antet** - Către: Autoritatea Contractantă, Ref: Cerere de Clarificare
- **Obiectul clarificării** - Identificarea clauzei problematice
- **Întrebări de clarificare** - Întrebări concrete și bine fundamentate
- **Propuneri** - Sugestii de modificare a clauzei
- **Temei legal** - Referințe la articole de lege relevante și jurisprudență CNSC

Redactează în limba română, limbaj formal și profesionist."""

    try:
        response_text = await llm.complete(
            prompt=prompt,
            temperature=0.3,
            max_tokens=8192,
        )

        logger.info(
            "clarification_generated",
            length=len(response_text),
            decision_refs=decision_refs,
        )
        return ClarificationResponse(content=response_text, decision_refs=decision_refs)

    except Exception as e:
        logger.error("clarification_error", error=str(e))
        raise
