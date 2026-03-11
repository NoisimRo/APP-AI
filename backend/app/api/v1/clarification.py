"""Clarification request generation API endpoint.

Uses RAG vector search to ground clarifications in actual CNSC jurisprudence.
"""

import time

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.db.session import get_session
from app.models.decision import ArgumentareCritica, DecizieCNSC
from app.services.embedding import EmbeddingService
from app.services.llm.factory import get_active_llm_provider, get_embedding_provider

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
    """Generate a formal clarification request with RAG jurisprudence."""
    t0 = time.monotonic()
    logger.info("clarification_request", clause_length=len(request.clause))

    llm = await get_active_llm_provider(session)
    embedding_service = EmbeddingService(llm_provider=get_embedding_provider())

    # Step 1: Search for relevant CNSC jurisprudence via vector search
    jurisprudence_context = ""
    decision_refs: list[str] = []

    try:
        query_vector = await embedding_service.embed_query(request.clause[:3000])
        t_embed = time.monotonic()
        logger.info("timing_clarification_embed", duration_s=round(t_embed - t0, 2))

        stmt = (
            select(
                ArgumentareCritica,
                ArgumentareCritica.embedding.cosine_distance(query_vector).label("distance"),
            )
            .where(ArgumentareCritica.embedding.isnot(None))
            .order_by("distance")
            .limit(6)
        )

        result = await session.execute(stmt)
        rows = result.all()

        # Filter by relevance
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
                    part += f"  Argumente contestator: {arg.argumente_contestator[:300]}\n"
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
                "clarification_jurisprudence_found",
                decisions=len(decisions),
                chunks=len(relevant_chunks),
            )

    except Exception as e:
        logger.warning("clarification_jurisprudence_search_failed", error=str(e))

    logger.info("timing_clarification_search", duration_s=round(time.monotonic() - t0, 2))

    # Step 2: Build prompt with jurisprudence
    jurisprudence_section = ""
    if jurisprudence_context:
        jurisprudence_section = f"""

=== JURISPRUDENȚĂ CNSC RELEVANTĂ (din baza de date) ===
{jurisprudence_context}
=== SFÂRȘIT JURISPRUDENȚĂ ===

IMPORTANT: Folosește jurisprudența CNSC de mai sus pentru a fundamenta cererea de clarificare.
Citează deciziile specifice când susții că o cerință este restrictivă sau discriminatorie.
Poți cita DOAR deciziile furnizate mai sus. NU inventa alte numere de decizii CNSC."""
    else:
        jurisprudence_section = """

Notă: Nu s-a găsit jurisprudență CNSC specifică în baza de date. NU cita și NU inventa numere de decizii CNSC."""

    prompt = f"""Ești un expert în achiziții publice din România. Clientul vrea să conteste sau clarifice următoarea clauză din documentația de atribuire:

"{request.clause}"
{jurisprudence_section}

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
        logger.info("timing_clarification_total", duration_s=round(time.monotonic() - t0, 2))
        return ClarificationResponse(content=response_text, decision_refs=decision_refs)

    except Exception as e:
        logger.error("clarification_error", error=str(e))
        raise
