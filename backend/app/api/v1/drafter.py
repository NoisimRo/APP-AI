"""Drafter API endpoint for generating legal complaints.

Uses RAG vector search to ground the complaint in actual CNSC jurisprudence.
"""

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.db.session import get_session
from app.models.decision import ArgumentareCritica, DecizieCNSC
from app.services.embedding import EmbeddingService
from app.services.llm.gemini import GeminiProvider

router = APIRouter()
logger = get_logger(__name__)


class DrafterRequest(BaseModel):
    """Request payload for complaint drafting."""

    facts: str = Field(..., min_length=1, max_length=50000)
    authority_args: str = Field(default="", max_length=50000)
    legal_grounds: str = Field(default="", max_length=5000)


class DrafterResponse(BaseModel):
    """Response payload for complaint drafting."""

    content: str
    decision_refs: list[str] = Field(default_factory=list)


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

    llm = GeminiProvider(model="gemini-2.5-flash")
    embedding_service = EmbeddingService(llm_provider=llm)

    # Step 1: Search for relevant CNSC jurisprudence via vector search
    jurisprudence_context = ""
    decision_refs: list[str] = []

    try:
        has_embeddings = await session.scalar(
            select(func.count())
            .select_from(ArgumentareCritica)
            .where(ArgumentareCritica.embedding.isnot(None))
        )

        if has_embeddings and has_embeddings > 0:
            # Build search query from facts
            search_query = request.facts[:3000]
            query_vector = await embedding_service.embed_query(search_query)

            stmt = (
                select(
                    ArgumentareCritica,
                    ArgumentareCritica.embedding.cosine_distance(query_vector).label("distance"),
                )
                .where(ArgumentareCritica.embedding.isnot(None))
                .order_by("distance")
                .limit(8)
            )

            result = await session.execute(stmt)
            rows = result.all()

            # Filter by relevance (cosine distance < 0.5)
            relevant_chunks = [
                (row.ArgumentareCritica, row.distance)
                for row in rows
                if row.distance < 0.5
            ]

            if relevant_chunks:
                # Load parent decisions
                dec_ids = list({arg.decizie_id for arg, _ in relevant_chunks})
                dec_result = await session.execute(
                    select(DecizieCNSC).where(DecizieCNSC.id.in_(dec_ids))
                )
                decisions = {d.id: d for d in dec_result.scalars().all()}
                decision_refs = [d.external_id for d in decisions.values()]

                # Build context
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
                    if arg.argumentatie_cnsc:
                        part += f"  Argumentație CNSC: {arg.argumentatie_cnsc[:400]}\n"
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

    # Step 2: Build prompt with jurisprudence context
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

    prompt = f"""Ești un avocat expert în achiziții publice din România. Redactează o contestație către CNSC (Consiliul Național de Soluționare a Contestațiilor).

Detalii faptice: {request.facts}

Argumente Autoritate Contractantă: {request.authority_args or 'Nu au fost furnizate.'}

Temei legal: {request.legal_grounds or 'Nu a fost specificat.'}
{jurisprudence_section}

Structura obligatorie a contestației:
1. **Părțile** - Identificarea contestatorului și a autorității contractante
2. **Situația de fapt** - Descrierea cronologică a evenimentelor
3. **Motivele contestației** - Dezvoltare amplă a argumentelor juridice, cu referire la legislația aplicabilă (Legea 98/2016, HG 395/2016, Legea 101/2016) și la jurisprudența CNSC disponibilă
4. **Solicitare de suspendare** a procedurii de atribuire
5. **Dispozitiv** - Solicitările concrete ale contestatorului

Redactează contestația în limba română, folosind limbaj juridic formal și profesionist.
Fiecare secțiune trebuie să fie clar delimitată cu titluri bold.
Include referințe la articole de lege relevante."""

    try:
        response_text = await llm.complete(
            prompt=prompt,
            temperature=0.3,
            max_tokens=8192,
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
