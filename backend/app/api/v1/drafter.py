"""Drafter API endpoint for generating legal documents.

Supports 6 document types (contestație, plângere, concluzii scrise,
punct de vedere, cerere intervenție, decizie CNSC) and 4 perspectives
(contestator, intervenient, autoritate contractantă, CNSC).

Uses multi-chunk RAG for grounding in CNSC jurisprudence when
documents from the case file are provided.
"""

import asyncio
import time
from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import defer

from app.core.logging import get_logger
from app.core.deps import require_feature
from app.core.rate_limiter import require_rate_limit, increment_usage
from app.db.session import get_session
from app.models.decision import ArgumentareCritica, DecizieCNSC, User
from app.services.embedding import EmbeddingService
from app.services.llm.factory import get_active_llm_provider, get_embedding_provider
from app.services.llm.streaming import create_sse_response
from app.services.rag import RAGService
from app.services.drafter_prompts import (
    DOCUMENT_TYPES, PERSPECTIVES, build_prompt,
)
from app.api.v1.scopes import get_scope_decision_ids

router = APIRouter()
logger = get_logger(__name__)

ENDPOINT_TIMEOUT = 300  # 5 minutes


# =============================================================================
# REQUEST / RESPONSE MODELS
# =============================================================================

DOC_TYPE_LITERAL = Literal[
    "contestatie", "plangere", "concluzii_scrise",
    "punct_de_vedere", "cerere_interventie", "decizie_cnsc",
]
PERSPECTIVE_LITERAL = Literal[
    "contestator", "intervenient", "autoritate_contractanta", "cnsc",
]


class DocumentInput(BaseModel):
    """A document from the case file."""
    filename: str
    text: str


class DrafterRequest(BaseModel):
    """Request payload for legal document drafting."""

    facts: str = Field(..., min_length=1, max_length=200000)
    authority_args: str = Field(default="", max_length=200000)
    legal_grounds: str = Field(default="", max_length=50000)
    scope_id: str | None = Field(None, description="Optional scope ID for pre-filtering decisions")
    doc_type: DOC_TYPE_LITERAL = Field(
        default="contestatie",
        description="Document type to generate",
    )
    perspective: PERSPECTIVE_LITERAL = Field(
        default="contestator",
        description="Perspective/role of the drafter",
    )
    # Documents from dosar (case file)
    documents: list[DocumentInput] = Field(
        default_factory=list,
        description="Documents from the case file (extracted text)",
    )
    # Previous document being responded to
    previous_document: str = Field(
        default="",
        max_length=200000,
        description="The document being responded to (e.g., contestație for PDV)",
    )
    # Additional fields
    remedii_solicitate: str = Field(
        default="", max_length=50000,
        description="Specific remedies requested",
    )
    detalii_procedura: str = Field(
        default="", max_length=100000,
        description="Procedure details: type, value, criterion",
    )
    numar_decizie_cnsc: str = Field(
        default="", max_length=200,
        description="For plângere: CNSC decision number being appealed",
    )


class DrafterResponse(BaseModel):
    """Response payload for document drafting."""

    content: str
    decision_refs: list[str] = Field(default_factory=list)
    doc_type: str = "contestatie"
    perspective: str = "contestator"


# =============================================================================
# RAG CONTEXT BUILDER (with multi-chunk support)
# =============================================================================

async def _build_drafter_context(
    request: DrafterRequest,
    session: AsyncSession,
    scope_decision_ids: list[str] | None = None,
) -> tuple[str, list[str]]:
    """Build drafter prompt with multi-chunk RAG for jurisprudence.

    When documents from the dosar are provided, uses multi-chunk search
    for comprehensive coverage of all arguments in the documents.
    """
    t0 = time.monotonic()
    rag_service = RAGService()

    jurisprudence_context = ""
    decision_refs: list[str] = []
    relevant_chunks: list[tuple] = []
    legislation_fragments = []

    try:
        # Combine all text sources for RAG search
        all_texts = []
        if request.documents:
            all_texts = [d.text for d in request.documents]
        if request.previous_document:
            all_texts.append(request.previous_document)
        # Always include facts
        all_texts.append(request.facts)

        if all_texts and sum(len(t) for t in all_texts) > 5000:
            # Use multi-chunk search for comprehensive coverage
            logger.info("drafter_using_multi_chunk_rag", num_texts=len(all_texts))
            relevant_chunks, legislation_fragments = await rag_service.multi_chunk_search(
                documents=all_texts,
                session=session,
                max_decisions=15,
                max_legislation=8,
                scope_decision_ids=scope_decision_ids,
            )
        else:
            # Small input — use single embedding search (faster)
            embedding_service = EmbeddingService(llm_provider=get_embedding_provider())
            search_text = request.facts[:2500]
            if request.legal_grounds:
                search_text += " " + request.legal_grounds[:500]
            query_vector = await embedding_service.embed_query(search_text)

            stmt = (
                select(
                    ArgumentareCritica,
                    ArgumentareCritica.embedding.cosine_distance(query_vector).label("distance"),
                )
                .where(ArgumentareCritica.embedding.isnot(None))
                .order_by("distance")
                .limit(12)
            )
            if scope_decision_ids is not None:
                stmt = stmt.where(ArgumentareCritica.decizie_id.in_(scope_decision_ids))

            result = await session.execute(stmt)
            relevant_chunks = [
                (row.ArgumentareCritica, row.distance)
                for row in result.all()
                if row.distance < 0.5
            ]

            if relevant_chunks:
                legislation_fragments = await rag_service._find_legislation_for_chunks(
                    relevant_chunks, session, limit=8,
                )

        t_search = time.monotonic()
        logger.info("timing_drafter_search", duration_s=round(t_search - t0, 2), chunks=len(relevant_chunks))

        # Build jurisprudence context from matched chunks
        if relevant_chunks:
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
                    part += f"  Argumente contestator: {arg.argumente_contestator[:800]}\n"
                if arg.jurisprudenta_contestator:
                    part += f"  Jurisprudență invocată de contestator: {'; '.join(arg.jurisprudenta_contestator)}\n"
                if arg.argumente_ac:
                    part += f"  Argumente AC: {arg.argumente_ac[:600]}\n"
                if arg.jurisprudenta_ac:
                    part += f"  Jurisprudență invocată de AC: {'; '.join(arg.jurisprudenta_ac)}\n"
                if arg.elemente_retinute_cnsc:
                    part += f"  Elemente reținute de CNSC: {arg.elemente_retinute_cnsc[:600]}\n"
                if arg.argumentatie_cnsc:
                    part += f"  Argumentație CNSC: {arg.argumentatie_cnsc[:800]}\n"
                if arg.jurisprudenta_cnsc:
                    part += f"  Jurisprudență citată de CNSC: {'; '.join(arg.jurisprudenta_cnsc)}\n"
                context_parts.append(part)

            jurisprudence_context = "\n---\n".join(context_parts)

    except Exception as e:
        logger.warning("drafter_context_build_failed", error=str(e))

    # Build legislation context
    legislation_context = ""
    if legislation_fragments:
        try:
            leg_contexts = rag_service._build_legislation_context(legislation_fragments)
            legislation_context = "\n\n".join(leg_contexts)
        except Exception as e:
            logger.warning("drafter_legislation_context_failed", error=str(e))

    logger.info("timing_drafter_context_total", duration_s=round(time.monotonic() - t0, 2))

    # Format sections
    jurisprudence_section = ""
    if jurisprudence_context:
        jurisprudence_section = f"""
=== JURISPRUDENȚĂ CNSC RELEVANTĂ (din baza de date — {len(decision_refs)} decizii) ===
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

Folosește textul exact al articolelor de lege de mai sus în argumentare."""

    # Build documents context from dosar
    documents_context = ""
    if request.documents:
        doc_parts = []
        for i, doc in enumerate(request.documents, 1):
            doc_parts.append(f"--- DOCUMENT {i}: {doc.filename} ---\n{doc.text}")
        documents_context = "\n\n".join(doc_parts)

    # Build the prompt using the type-specific builder
    prompt = build_prompt(
        doc_type=request.doc_type,
        facts=request.facts,
        authority_args=request.authority_args,
        legal_grounds=request.legal_grounds,
        previous_document=request.previous_document,
        documents_context=documents_context,
        jurisprudence_section=jurisprudence_section,
        legislation_section=legislation_section,
        perspective=request.perspective,
        procedure_details=request.detalii_procedura,
        remedies=request.remedii_solicitate,
        extra_fields={
            "numar_decizie_cnsc": request.numar_decizie_cnsc,
        },
    )

    return prompt, decision_refs


# =============================================================================
# API ENDPOINTS
# =============================================================================

@router.get("/types")
async def get_document_types():
    """Get available document types and perspectives."""
    return {
        "document_types": DOCUMENT_TYPES,
        "perspectives": {k: {"name": v["name"], "description": v["description"]} for k, v in PERSPECTIVES.items()},
    }


@router.post("/", response_model=DrafterResponse)
async def draft_document(
    request: DrafterRequest,
    http_request: Request,
    session: AsyncSession = Depends(get_session),
    rate_user: Optional[User] = Depends(require_rate_limit),
    _feature: Optional[User] = Depends(require_feature("drafter")),
) -> DrafterResponse:
    """Generate a legal document draft using LLM with RAG jurisprudence."""
    logger.info(
        "draft_request",
        facts_length=len(request.facts),
        doc_type=request.doc_type,
        perspective=request.perspective,
        num_documents=len(request.documents),
        has_previous_doc=bool(request.previous_document),
    )

    scope_ids = None
    if request.scope_id:
        scope_ids = await get_scope_decision_ids(request.scope_id, session)
        if scope_ids is None:
            raise HTTPException(status_code=404, detail="Scope not found")

    try:
        prompt, decision_refs = await asyncio.wait_for(
            _build_drafter_context(request, session, scope_decision_ids=scope_ids),
            timeout=ENDPOINT_TIMEOUT,
        )
    except asyncio.TimeoutError:
        raise HTTPException(504, f"Construirea contextului a depășit {ENDPOINT_TIMEOUT}s. Reduceți volumul documentelor.")

    llm = await get_active_llm_provider(session)

    try:
        response_text = await llm.complete(
            prompt=prompt,
            temperature=0.3,
            max_tokens=16384,
        )
        await increment_usage(rate_user, http_request)
        return DrafterResponse(
            content=response_text,
            decision_refs=decision_refs,
            doc_type=request.doc_type,
            perspective=request.perspective,
        )

    except Exception as e:
        logger.error("draft_error", error=str(e))
        raise


@router.post("/stream")
async def draft_document_stream(
    request: DrafterRequest,
    http_request: Request,
    session: AsyncSession = Depends(get_session),
    rate_user: Optional[User] = Depends(require_rate_limit),
    _feature: Optional[User] = Depends(require_feature("drafter")),
):
    """Stream a legal document draft via SSE."""
    logger.info(
        "draft_stream_request",
        facts_length=len(request.facts),
        doc_type=request.doc_type,
        perspective=request.perspective,
        num_documents=len(request.documents),
    )
    await increment_usage(rate_user, http_request)

    scope_ids = None
    if request.scope_id:
        scope_ids = await get_scope_decision_ids(request.scope_id, session)
        if scope_ids is None:
            raise HTTPException(status_code=404, detail="Scope not found")

    try:
        prompt, decision_refs = await asyncio.wait_for(
            _build_drafter_context(request, session, scope_decision_ids=scope_ids),
            timeout=ENDPOINT_TIMEOUT,
        )
    except asyncio.TimeoutError:
        raise HTTPException(504, f"Construirea contextului a depășit {ENDPOINT_TIMEOUT}s. Reduceți volumul documentelor.")

    llm = await get_active_llm_provider(session)

    doc_name = DOCUMENT_TYPES.get(request.doc_type, {}).get("name", request.doc_type)
    status_msgs = []
    if decision_refs:
        status_msgs.append(f"Am găsit {len(decision_refs)} decizii CNSC relevante")
    if request.documents:
        status_msgs.append(f"Analizez {len(request.documents)} documente din dosar")
    status_msgs.append(f"Se redactează: {doc_name}...")

    return await create_sse_response(
        llm=llm,
        prompt=prompt,
        temperature=0.3,
        max_tokens=16384,
        metadata={"decision_refs": decision_refs, "doc_type": request.doc_type, "perspective": request.perspective},
        status_messages=status_msgs,
    )
