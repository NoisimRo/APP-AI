"""ANAP Spete API endpoints."""

from typing import Optional

from fastapi import APIRouter, HTTPException, Query, Depends
from pydantic import BaseModel
from sqlalchemy import select, func, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.db.session import get_session
from app.models.decision import SpetaANAP
from app.services.embedding import EmbeddingService
from app.services.llm.factory import get_embedding_provider

router = APIRouter()
logger = get_logger(__name__)


class SpetaSummary(BaseModel):
    numar_speta: int
    categorie: str
    intrebare: str
    taguri: list[str]
    data_publicarii: str


class SpetaDetail(BaseModel):
    numar_speta: int
    versiune: int
    data_publicarii: str
    categorie: str
    intrebare: str
    raspuns: str
    taguri: list[str]


class SpeteListResponse(BaseModel):
    items: list[SpetaSummary]
    total: int
    page: int
    pages: int


@router.get("/stats")
async def get_spete_stats(session: AsyncSession = Depends(get_session)):
    """Get spete statistics."""
    total = await session.scalar(select(func.count(SpetaANAP.id)))
    categories = await session.scalar(
        select(func.count(func.distinct(SpetaANAP.categorie)))
    )
    with_embeddings = await session.scalar(
        select(func.count(SpetaANAP.id)).where(SpetaANAP.embedding.isnot(None))
    )
    return {
        "total": total or 0,
        "categories": categories or 0,
        "with_embeddings": with_embeddings or 0,
    }


@router.get("/categories")
async def get_categories(session: AsyncSession = Depends(get_session)):
    """Get distinct categories with counts."""
    result = await session.execute(
        select(SpetaANAP.categorie, func.count(SpetaANAP.id))
        .group_by(SpetaANAP.categorie)
        .order_by(SpetaANAP.categorie)
    )
    return [{"categorie": row[0], "count": row[1]} for row in result.all()]


@router.get("/tags")
async def get_tags(session: AsyncSession = Depends(get_session)):
    """Get distinct tags with counts."""
    result = await session.execute(
        text("""
            SELECT tag, count(*) as cnt
            FROM spete_anap, unnest(taguri) AS tag
            GROUP BY tag
            ORDER BY cnt DESC
        """)
    )
    return [{"tag": row[0], "count": row[1]} for row in result.all()]


@router.get("/", response_model=SpeteListResponse)
async def list_spete(
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
    search: Optional[str] = None,
    categorie: Optional[str] = None,
    tag: Optional[str] = None,
    semantic: bool = False,
    session: AsyncSession = Depends(get_session),
):
    """List spete with pagination, filtering, and search."""
    # Semantic search path
    if semantic and search and search.strip():
        try:
            embedding_service = EmbeddingService(
                llm_provider=get_embedding_provider()
            )
            query_vector = await embedding_service.embed_query(search.strip())

            stmt = (
                select(
                    SpetaANAP,
                    SpetaANAP.embedding.cosine_distance(query_vector).label("distance"),
                )
                .where(SpetaANAP.embedding.isnot(None))
            )

            if categorie:
                stmt = stmt.where(SpetaANAP.categorie == categorie)
            if tag:
                stmt = stmt.where(SpetaANAP.taguri.any(tag))

            stmt = stmt.order_by("distance").limit(limit)

            result = await session.execute(stmt)
            rows = result.all()

            items = [
                SpetaSummary(
                    numar_speta=row[0].numar_speta,
                    categorie=row[0].categorie,
                    intrebare=row[0].intrebare,
                    taguri=row[0].taguri or [],
                    data_publicarii=row[0].data_publicarii.isoformat() if row[0].data_publicarii else "",
                )
                for row in rows
                if row.distance < 0.7
            ]

            return SpeteListResponse(
                items=items,
                total=len(items),
                page=1,
                pages=1,
            )
        except Exception as e:
            logger.warning("semantic_search_failed", error=str(e))
            # Fall through to regular search

    # Regular search path
    stmt = select(SpetaANAP)
    count_stmt = select(func.count(SpetaANAP.id))

    # Filters
    if categorie:
        stmt = stmt.where(SpetaANAP.categorie == categorie)
        count_stmt = count_stmt.where(SpetaANAP.categorie == categorie)
    if tag:
        stmt = stmt.where(SpetaANAP.taguri.any(tag))
        count_stmt = count_stmt.where(SpetaANAP.taguri.any(tag))
    if search and search.strip():
        search_term = f"%{search.strip()}%"
        search_filter = SpetaANAP.intrebare.ilike(search_term) | SpetaANAP.raspuns.ilike(search_term)
        stmt = stmt.where(search_filter)
        count_stmt = count_stmt.where(search_filter)

    total = await session.scalar(count_stmt) or 0
    pages = max(1, (total + limit - 1) // limit)

    offset = (page - 1) * limit
    stmt = stmt.order_by(SpetaANAP.numar_speta).offset(offset).limit(limit)

    result = await session.execute(stmt)
    spete = result.scalars().all()

    items = [
        SpetaSummary(
            numar_speta=s.numar_speta,
            categorie=s.categorie,
            intrebare=s.intrebare,
            taguri=s.taguri or [],
            data_publicarii=s.data_publicarii.isoformat() if s.data_publicarii else "",
        )
        for s in spete
    ]

    return SpeteListResponse(items=items, total=total, page=page, pages=pages)


@router.get("/{numar_speta}", response_model=SpetaDetail)
async def get_speta(
    numar_speta: int,
    session: AsyncSession = Depends(get_session),
):
    """Get a single speță by number."""
    result = await session.execute(
        select(SpetaANAP).where(SpetaANAP.numar_speta == numar_speta)
    )
    speta = result.scalar_one_or_none()
    if not speta:
        raise HTTPException(404, f"Speța ANAP nr. {numar_speta} nu a fost găsită")

    return SpetaDetail(
        numar_speta=speta.numar_speta,
        versiune=speta.versiune,
        data_publicarii=speta.data_publicarii.isoformat() if speta.data_publicarii else "",
        categorie=speta.categorie,
        intrebare=speta.intrebare,
        raspuns=speta.raspuns,
        taguri=speta.taguri or [],
    )
