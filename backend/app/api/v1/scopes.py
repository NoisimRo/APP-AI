"""Search Scopes API endpoints — saved filter presets for RAG pre-filtering."""

from fastapi import APIRouter, HTTPException, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy import select, func, or_, cast, String
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.db.session import get_session, is_db_available
from app.models.decision import DecizieCNSC, SearchScope

router = APIRouter()
logger = get_logger(__name__)


class ScopeFilters(BaseModel):
    """Filter configuration for a scope."""

    ruling: str | None = None
    tip_contestatie: str | None = None
    years: list[int] | None = None
    coduri_critici: list[str] | None = None
    cpv_codes: list[str] | None = None
    search: str | None = None


class CreateScopeRequest(BaseModel):
    """Request to create a new search scope."""

    name: str = Field(..., min_length=1, max_length=100)
    description: str | None = None
    filters: ScopeFilters


class UpdateScopeRequest(BaseModel):
    """Request to update an existing scope."""

    name: str | None = Field(None, min_length=1, max_length=100)
    description: str | None = None
    filters: ScopeFilters | None = None


class ScopeResponse(BaseModel):
    """Scope response."""

    id: str
    name: str
    description: str | None
    filters: dict
    decision_count: int
    created_at: str
    updated_at: str


def _apply_scope_filters(query, filters: dict):
    """Apply scope filters to a SQLAlchemy query on DecizieCNSC.

    Shared between scope count computation and RAG pre-filtering.
    """
    if filters.get("ruling"):
        ruling_str = filters["ruling"]
        ruling_values = [r.strip() for r in ruling_str.split(",") if r.strip()]
        has_null = "__NULL__" in ruling_values
        non_null_values = [r for r in ruling_values if r != "__NULL__"]
        conditions = []
        if non_null_values:
            if len(non_null_values) == 1:
                conditions.append(DecizieCNSC.solutie_contestatie == non_null_values[0])
            else:
                conditions.append(DecizieCNSC.solutie_contestatie.in_(non_null_values))
        if has_null:
            conditions.append(DecizieCNSC.solutie_contestatie.is_(None))
        if conditions:
            query = query.where(or_(*conditions))

    if filters.get("tip_contestatie"):
        query = query.where(DecizieCNSC.tip_contestatie == filters["tip_contestatie"])

    years = filters.get("years")
    if years:
        if len(years) == 1:
            query = query.where(DecizieCNSC.an_bo == years[0])
        else:
            query = query.where(DecizieCNSC.an_bo.in_(years))

    if filters.get("coduri_critici"):
        query = query.where(
            DecizieCNSC.coduri_critici.overlap(filters["coduri_critici"])
        )

    if filters.get("cpv_codes"):
        cpv_conditions = [
            DecizieCNSC.cod_cpv.ilike(f"{cpv}%") for cpv in filters["cpv_codes"]
        ]
        query = query.where(or_(*cpv_conditions))

    if filters.get("search"):
        search_term = f"%{filters['search'].strip()}%"
        query = query.where(
            or_(
                cast(DecizieCNSC.numar_bo, String).ilike(search_term),
                DecizieCNSC.contestator.ilike(search_term),
                DecizieCNSC.autoritate_contractanta.ilike(search_term),
                DecizieCNSC.cod_cpv.ilike(search_term),
                DecizieCNSC.cpv_descriere.ilike(search_term),
                DecizieCNSC.cpv_categorie.ilike(search_term),
                DecizieCNSC.cpv_clasa.ilike(search_term),
                DecizieCNSC.filename.ilike(search_term),
            )
        )

    return query


async def _count_matching_decisions(filters: dict, session: AsyncSession) -> int:
    """Count decisions matching scope filters."""
    query = select(func.count()).select_from(DecizieCNSC)
    query = _apply_scope_filters(query, filters)
    result = await session.execute(query)
    return result.scalar() or 0


async def get_scope_decision_ids(
    scope_id: str, session: AsyncSession
) -> list[str] | None:
    """Get list of decision IDs matching a scope's filters.

    Returns None if scope not found, empty list if no matches.
    Used by RAG service to pre-filter vector search.
    """
    result = await session.execute(
        select(SearchScope).where(SearchScope.id == scope_id)
    )
    scope = result.scalar_one_or_none()
    if not scope:
        return None

    query = select(DecizieCNSC.id)
    query = _apply_scope_filters(query, scope.filters)
    result = await session.execute(query)
    return [row[0] for row in result.all()]


@router.get("/", response_model=list[ScopeResponse])
async def list_scopes(
    session: AsyncSession = Depends(get_session),
):
    """List all saved search scopes."""
    if not is_db_available():
        return []

    result = await session.execute(
        select(SearchScope).order_by(SearchScope.updated_at.desc())
    )
    scopes = result.scalars().all()

    return [
        ScopeResponse(
            id=s.id,
            name=s.name,
            description=s.description,
            filters=s.filters or {},
            decision_count=s.decision_count,
            created_at=s.created_at.isoformat(),
            updated_at=s.updated_at.isoformat(),
        )
        for s in scopes
    ]


@router.post("/", response_model=ScopeResponse)
async def create_scope(
    request: CreateScopeRequest,
    session: AsyncSession = Depends(get_session),
):
    """Create a new search scope from current filters."""
    if not is_db_available():
        raise HTTPException(status_code=503, detail="Database not available")

    filters_dict = request.filters.model_dump(exclude_none=True)

    # Count matching decisions
    count = await _count_matching_decisions(filters_dict, session)

    scope = SearchScope(
        name=request.name,
        description=request.description,
        filters=filters_dict,
        decision_count=count,
    )
    session.add(scope)
    await session.commit()
    await session.refresh(scope)

    logger.info(
        "scope_created",
        scope_id=scope.id,
        name=scope.name,
        decision_count=count,
    )

    return ScopeResponse(
        id=scope.id,
        name=scope.name,
        description=scope.description,
        filters=scope.filters or {},
        decision_count=scope.decision_count,
        created_at=scope.created_at.isoformat(),
        updated_at=scope.updated_at.isoformat(),
    )


@router.put("/{scope_id}", response_model=ScopeResponse)
async def update_scope(
    scope_id: str,
    request: UpdateScopeRequest,
    session: AsyncSession = Depends(get_session),
):
    """Update an existing scope."""
    if not is_db_available():
        raise HTTPException(status_code=503, detail="Database not available")

    result = await session.execute(
        select(SearchScope).where(SearchScope.id == scope_id)
    )
    scope = result.scalar_one_or_none()
    if not scope:
        raise HTTPException(status_code=404, detail="Scope not found")

    if request.name is not None:
        scope.name = request.name
    if request.description is not None:
        scope.description = request.description
    if request.filters is not None:
        filters_dict = request.filters.model_dump(exclude_none=True)
        scope.filters = filters_dict
        scope.decision_count = await _count_matching_decisions(
            filters_dict, session
        )

    await session.commit()
    await session.refresh(scope)

    return ScopeResponse(
        id=scope.id,
        name=scope.name,
        description=scope.description,
        filters=scope.filters or {},
        decision_count=scope.decision_count,
        created_at=scope.created_at.isoformat(),
        updated_at=scope.updated_at.isoformat(),
    )


@router.delete("/{scope_id}")
async def delete_scope(
    scope_id: str,
    session: AsyncSession = Depends(get_session),
):
    """Delete a scope."""
    if not is_db_available():
        raise HTTPException(status_code=503, detail="Database not available")

    result = await session.execute(
        select(SearchScope).where(SearchScope.id == scope_id)
    )
    scope = result.scalar_one_or_none()
    if not scope:
        raise HTTPException(status_code=404, detail="Scope not found")

    await session.delete(scope)
    await session.commit()

    logger.info("scope_deleted", scope_id=scope_id)
    return {"status": "deleted"}
