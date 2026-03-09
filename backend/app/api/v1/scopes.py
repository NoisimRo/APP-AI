"""Search Scopes API — CRUD for saved filter combinations."""

from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel, Field
from sqlalchemy import select, func, or_
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.db.session import get_session
from app.models.decision import SearchScope, DecizieCNSC

router = APIRouter()
logger = get_logger(__name__)


class ScopeCreate(BaseModel):
    """Request to create a search scope."""

    name: str = Field(..., min_length=1, max_length=100)
    description: str | None = None
    filters: dict = Field(default_factory=dict)


class ScopeUpdate(BaseModel):
    """Request to update a search scope."""

    name: str | None = Field(None, max_length=100)
    description: str | None = None
    filters: dict | None = None


class ScopeResponse(BaseModel):
    """Search scope response."""

    id: str
    name: str
    description: str | None = None
    filters: dict
    decision_count: int = 0
    created_at: str
    updated_at: str


def _scope_to_response(scope: SearchScope) -> ScopeResponse:
    """Convert a SearchScope model to response."""
    return ScopeResponse(
        id=str(scope.id),
        name=scope.name,
        description=scope.description,
        filters=scope.filters or {},
        decision_count=scope.decision_count,
        created_at=scope.created_at.isoformat() if scope.created_at else "",
        updated_at=scope.updated_at.isoformat() if scope.updated_at else "",
    )


async def _count_matching_decisions(filters: dict, session: AsyncSession) -> int:
    """Count decisions matching a set of filters."""
    stmt = select(func.count()).select_from(DecizieCNSC)

    if filters.get("ruling"):
        stmt = stmt.where(DecizieCNSC.solutie_contestatie == filters["ruling"])
    if filters.get("tip_contestatie"):
        stmt = stmt.where(DecizieCNSC.tip_contestatie == filters["tip_contestatie"])
    if filters.get("year"):
        stmt = stmt.where(DecizieCNSC.an_bo == int(filters["year"]))
    if filters.get("coduri_critici"):
        stmt = stmt.where(DecizieCNSC.coduri_critici.overlap(filters["coduri_critici"]))
    if filters.get("cpv_codes"):
        cpv_conditions = [DecizieCNSC.cod_cpv.ilike(f"{c}%") for c in filters["cpv_codes"]]
        stmt = stmt.where(or_(*cpv_conditions))
    if filters.get("search"):
        search_term = f"%{filters['search']}%"
        stmt = stmt.where(
            or_(
                DecizieCNSC.text_integral.ilike(search_term),
                DecizieCNSC.contestator.ilike(search_term),
                DecizieCNSC.autoritate_contractanta.ilike(search_term),
            )
        )

    count = await session.scalar(stmt)
    return count or 0


@router.post("/", response_model=ScopeResponse, status_code=201)
async def create_scope(
    request: ScopeCreate,
    session: AsyncSession = Depends(get_session),
) -> ScopeResponse:
    """Crează un scope de căutare din filtrele curente."""
    logger.info("creating_scope", name=request.name)

    # Count matching decisions
    count = await _count_matching_decisions(request.filters, session)

    scope = SearchScope(
        name=request.name,
        description=request.description,
        filters=request.filters,
        decision_count=count,
    )
    session.add(scope)
    await session.commit()
    await session.refresh(scope)

    logger.info("scope_created", id=scope.id, name=scope.name, count=count)
    return _scope_to_response(scope)


@router.get("/", response_model=list[ScopeResponse])
async def list_scopes(
    session: AsyncSession = Depends(get_session),
) -> list[ScopeResponse]:
    """Listează toate scope-urile de căutare."""
    stmt = select(SearchScope).order_by(SearchScope.created_at.desc())
    result = await session.execute(stmt)
    scopes = list(result.scalars().all())
    return [_scope_to_response(s) for s in scopes]


@router.get("/{scope_id}", response_model=ScopeResponse)
async def get_scope(
    scope_id: str,
    session: AsyncSession = Depends(get_session),
) -> ScopeResponse:
    """Detalii scope de căutare."""
    stmt = select(SearchScope).where(SearchScope.id == scope_id)
    result = await session.execute(stmt)
    scope = result.scalar_one_or_none()

    if not scope:
        raise HTTPException(status_code=404, detail="Scope-ul nu a fost găsit")

    return _scope_to_response(scope)


@router.put("/{scope_id}", response_model=ScopeResponse)
async def update_scope(
    scope_id: str,
    request: ScopeUpdate,
    session: AsyncSession = Depends(get_session),
) -> ScopeResponse:
    """Actualizează un scope de căutare."""
    stmt = select(SearchScope).where(SearchScope.id == scope_id)
    result = await session.execute(stmt)
    scope = result.scalar_one_or_none()

    if not scope:
        raise HTTPException(status_code=404, detail="Scope-ul nu a fost găsit")

    if request.name is not None:
        scope.name = request.name
    if request.description is not None:
        scope.description = request.description
    if request.filters is not None:
        scope.filters = request.filters
        scope.decision_count = await _count_matching_decisions(request.filters, session)

    await session.commit()
    await session.refresh(scope)

    logger.info("scope_updated", id=scope.id, name=scope.name)
    return _scope_to_response(scope)


@router.delete("/{scope_id}", status_code=204)
async def delete_scope(
    scope_id: str,
    session: AsyncSession = Depends(get_session),
):
    """Șterge un scope de căutare."""
    stmt = select(SearchScope).where(SearchScope.id == scope_id)
    result = await session.execute(stmt)
    scope = result.scalar_one_or_none()

    if not scope:
        raise HTTPException(status_code=404, detail="Scope-ul nu a fost găsit")

    await session.delete(scope)
    await session.commit()

    logger.info("scope_deleted", id=scope_id)
