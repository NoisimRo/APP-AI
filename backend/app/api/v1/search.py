"""Search API endpoints."""

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy import select, func, extract
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.db.session import get_session
from app.models.decision import (
    ArgumentareCritica,
    DecizieCNSC,
)
from app.services.embedding import EmbeddingService

router = APIRouter()
logger = get_logger(__name__)


class SearchFilters(BaseModel):
    """Search filters for decisions."""

    cpv_codes: list[str] | None = Field(None, description="Filter by CPV codes")
    criticism_codes: list[str] | None = Field(
        None, description="Filter by criticism codes (D1-D8, R1-R8)"
    )
    ruling: str | None = Field(None, description="Filter by ruling: ADMIS or RESPINS")
    year_from: int | None = Field(None, ge=2000, le=2100)
    year_to: int | None = Field(None, ge=2000, le=2100)


class SearchResult(BaseModel):
    """A search result."""

    decision_id: str
    title: str
    excerpt: str
    score: float = Field(ge=0.0, le=1.0)
    metadata: dict = Field(default_factory=dict)


class SearchResponse(BaseModel):
    """Search response payload."""

    query: str
    results: list[SearchResult]
    total: int
    page: int
    page_size: int


@router.post("/semantic", response_model=SearchResponse)
async def semantic_search(
    query: str = Query(..., min_length=3, max_length=1000),
    filters: SearchFilters | None = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(10, ge=1, le=100),
    session: AsyncSession = Depends(get_session),
) -> SearchResponse:
    """
    Perform semantic search across CNSC decisions.

    Uses vector similarity to find relevant decisions based on meaning,
    not just keyword matching.
    """
    logger.info(
        "semantic_search",
        query=query[:100],
        has_filters=filters is not None,
        page=page,
    )

    embedding_service = EmbeddingService()

    # Embed the query
    query_vector = await embedding_service.embed_query(query)

    # Base query: cosine distance search on ArgumentareCritica
    stmt = (
        select(
            ArgumentareCritica,
            ArgumentareCritica.embedding.cosine_distance(query_vector).label("distance"),
            DecizieCNSC,
        )
        .join(DecizieCNSC, ArgumentareCritica.decizie_id == DecizieCNSC.id)
        .where(ArgumentareCritica.embedding.isnot(None))
    )

    # Apply filters
    if filters:
        if filters.cpv_codes:
            stmt = stmt.where(DecizieCNSC.cod_cpv.in_(filters.cpv_codes))
        if filters.criticism_codes:
            stmt = stmt.where(
                DecizieCNSC.coduri_critici.overlap(filters.criticism_codes)
            )
        if filters.ruling:
            stmt = stmt.where(DecizieCNSC.solutie_contestatie == filters.ruling)
        if filters.year_from:
            stmt = stmt.where(
                extract("year", DecizieCNSC.data_decizie) >= filters.year_from
            )
        if filters.year_to:
            stmt = stmt.where(
                extract("year", DecizieCNSC.data_decizie) <= filters.year_to
            )

    stmt = stmt.order_by("distance")

    # Get total count (without pagination)
    count_stmt = select(func.count()).select_from(stmt.subquery())
    total = await session.scalar(count_stmt) or 0

    # Apply pagination
    offset = (page - 1) * page_size
    stmt = stmt.offset(offset).limit(page_size)

    result = await session.execute(stmt)
    rows = result.all()

    # Deduplicate by decision and build results
    seen_decisions: set[str] = set()
    results: list[SearchResult] = []

    for row in rows:
        arg = row.ArgumentareCritica
        dec = row.DecizieCNSC
        distance = row.distance
        similarity = max(0.0, min(1.0, 1.0 - distance))

        if dec.id in seen_decisions:
            continue
        seen_decisions.add(dec.id)

        # Build excerpt from the matched chunk
        excerpt_parts = []
        if arg.argumentatie_cnsc:
            excerpt_parts.append(arg.argumentatie_cnsc[:300])
        elif arg.elemente_retinute_cnsc:
            excerpt_parts.append(arg.elemente_retinute_cnsc[:300])
        elif arg.argumente_contestator:
            excerpt_parts.append(arg.argumente_contestator[:300])
        excerpt = " ".join(excerpt_parts)[:400] + "..." if excerpt_parts else ""

        results.append(SearchResult(
            decision_id=dec.external_id,
            title=f"Decizia {dec.external_id} - {dec.solutie_contestatie or 'N/A'}",
            excerpt=excerpt,
            score=similarity,
            metadata={
                "numar_decizie": dec.numar_decizie,
                "data_decizie": dec.data_decizie.isoformat() if dec.data_decizie else None,
                "tip_contestatie": dec.tip_contestatie,
                "coduri_critici": dec.coduri_critici,
                "solutie": dec.solutie_contestatie,
                "cod_cpv": dec.cod_cpv,
                "contestator": dec.contestator,
                "autoritate_contractanta": dec.autoritate_contractanta,
                "critica_matched": arg.cod_critica,
            },
        ))

    return SearchResponse(
        query=query,
        results=results,
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get("/similar/{decision_id}", response_model=SearchResponse)
async def find_similar(
    decision_id: str,
    limit: int = Query(5, ge=1, le=20),
    session: AsyncSession = Depends(get_session),
) -> SearchResponse:
    """
    Find decisions similar to a given decision.

    Uses vector similarity to find decisions with similar content,
    arguments, or legal reasoning.
    """
    logger.info("find_similar", decision_id=decision_id, limit=limit)

    # Find the source decision
    stmt = select(DecizieCNSC).where(DecizieCNSC.filename.ilike(f"%{decision_id}%"))
    result = await session.execute(stmt)
    source_decision = result.scalar_one_or_none()

    if not source_decision:
        # Try by external_id pattern (BO{year}_{number})
        parts = decision_id.replace("BO", "").split("_")
        if len(parts) == 2:
            stmt = (
                select(DecizieCNSC)
                .where(DecizieCNSC.an_bo == int(parts[0]))
                .where(DecizieCNSC.numar_bo == int(parts[1]))
            )
            result = await session.execute(stmt)
            source_decision = result.scalar_one_or_none()

    if not source_decision:
        return SearchResponse(
            query=f"similar to {decision_id}",
            results=[],
            total=0,
            page=1,
            page_size=limit,
        )

    # Get embeddings for the source decision's ArgumentareCritica
    stmt = (
        select(ArgumentareCritica)
        .where(ArgumentareCritica.decizie_id == source_decision.id)
        .where(ArgumentareCritica.embedding.isnot(None))
    )
    result = await session.execute(stmt)
    source_args = list(result.scalars().all())

    if not source_args:
        return SearchResponse(
            query=f"similar to {decision_id}",
            results=[],
            total=0,
            page=1,
            page_size=limit,
        )

    # Compute centroid (average) of all the decision's chunk embeddings
    centroid = [0.0] * len(source_args[0].embedding)
    for arg in source_args:
        for i, val in enumerate(arg.embedding):
            centroid[i] += val
    centroid = [v / len(source_args) for v in centroid]

    # Find nearest neighbors excluding the source decision
    stmt = (
        select(
            ArgumentareCritica,
            ArgumentareCritica.embedding.cosine_distance(centroid).label("distance"),
            DecizieCNSC,
        )
        .join(DecizieCNSC, ArgumentareCritica.decizie_id == DecizieCNSC.id)
        .where(ArgumentareCritica.embedding.isnot(None))
        .where(ArgumentareCritica.decizie_id != source_decision.id)
        .order_by("distance")
        .limit(limit * 3)  # Fetch extra for deduplication
    )

    result = await session.execute(stmt)
    rows = result.all()

    # Deduplicate by decision
    seen_decisions: set[str] = set()
    results: list[SearchResult] = []

    for row in rows:
        arg = row.ArgumentareCritica
        dec = row.DecizieCNSC
        distance = row.distance
        similarity = max(0.0, min(1.0, 1.0 - distance))

        if dec.id in seen_decisions:
            continue
        seen_decisions.add(dec.id)

        excerpt_parts = []
        if arg.argumentatie_cnsc:
            excerpt_parts.append(arg.argumentatie_cnsc[:300])
        elif arg.elemente_retinute_cnsc:
            excerpt_parts.append(arg.elemente_retinute_cnsc[:300])
        excerpt = " ".join(excerpt_parts)[:400] + "..." if excerpt_parts else ""

        results.append(SearchResult(
            decision_id=dec.external_id,
            title=f"Decizia {dec.external_id} - {dec.solutie_contestatie or 'N/A'}",
            excerpt=excerpt,
            score=similarity,
            metadata={
                "numar_decizie": dec.numar_decizie,
                "data_decizie": dec.data_decizie.isoformat() if dec.data_decizie else None,
                "tip_contestatie": dec.tip_contestatie,
                "coduri_critici": dec.coduri_critici,
                "solutie": dec.solutie_contestatie,
            },
        ))

        if len(results) >= limit:
            break

    return SearchResponse(
        query=f"similar to {decision_id}",
        results=results,
        total=len(results),
        page=1,
        page_size=limit,
    )
