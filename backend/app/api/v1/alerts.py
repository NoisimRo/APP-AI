"""Alert Rules API — CRUD for decision alert subscriptions.

Users define filter-based rules that match against new CNSC decisions.
When matches are found during daily pipeline, notifications are sent.
"""

from typing import Optional

from fastapi import APIRouter, HTTPException, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_current_active_user
from app.core.logging import get_logger
from app.db.session import get_session, is_db_available
from app.models.decision import AlertRule, User

router = APIRouter()
logger = get_logger(__name__)


# =============================================================================
# PYDANTIC SCHEMAS
# =============================================================================

class AlertFilters(BaseModel):
    """Filter criteria for matching decisions."""
    cod_cpv: list[str] = []
    coduri_critici: list[str] = []
    complet: list[str] = []
    tip_procedura: list[str] = []
    solutie: list[str] = []
    keywords: list[str] = []


class AlertRuleCreate(BaseModel):
    nume: str = Field(..., min_length=1, max_length=200)
    descriere: str | None = None
    filters: AlertFilters
    frecventa: str = Field("zilnic", pattern="^(zilnic|saptamanal)$")


class AlertRuleUpdate(BaseModel):
    nume: str | None = Field(None, max_length=200)
    descriere: str | None = None
    filters: AlertFilters | None = None
    activ: bool | None = None
    frecventa: str | None = Field(None, pattern="^(zilnic|saptamanal)$")


class AlertRuleResponse(BaseModel):
    id: str
    nume: str
    descriere: str | None
    filters: dict
    activ: bool
    frecventa: str
    ultima_notificare: str | None
    total_notificari: int
    created_at: str
    updated_at: str


def _rule_to_response(rule: AlertRule) -> AlertRuleResponse:
    return AlertRuleResponse(
        id=rule.id,
        nume=rule.nume,
        descriere=rule.descriere,
        filters=rule.filters or {},
        activ=rule.activ,
        frecventa=rule.frecventa,
        ultima_notificare=rule.ultima_notificare.isoformat() if rule.ultima_notificare else None,
        total_notificari=rule.total_notificari,
        created_at=rule.created_at.isoformat(),
        updated_at=rule.updated_at.isoformat(),
    )


# =============================================================================
# ENDPOINTS
# =============================================================================

@router.post("/", response_model=AlertRuleResponse)
async def create_alert_rule(
    request: AlertRuleCreate,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_active_user),
):
    """Create a new alert rule for decision matching."""
    if not is_db_available():
        raise HTTPException(status_code=503, detail="Database not available")

    # Validate at least one filter is set
    filters_dict = request.filters.model_dump()
    has_filter = any(v for v in filters_dict.values() if v)
    if not has_filter:
        raise HTTPException(400, "Trebuie definit cel puțin un filtru")

    # Limit number of rules per user
    count_result = await session.execute(
        select(func.count()).select_from(AlertRule).where(AlertRule.user_id == user.id)
    )
    count = count_result.scalar() or 0
    if count >= 20:
        raise HTTPException(400, "Limita maximă de 20 reguli de alertă a fost atinsă")

    rule = AlertRule(
        user_id=user.id,
        nume=request.nume,
        descriere=request.descriere,
        filters=filters_dict,
        frecventa=request.frecventa,
    )
    session.add(rule)
    await session.commit()
    await session.refresh(rule)

    logger.info("alert_rule_created", rule_id=rule.id, nume=rule.nume)
    return _rule_to_response(rule)


@router.get("/", response_model=list[AlertRuleResponse])
async def list_alert_rules(
    activ: bool | None = Query(None, description="Filter by active status"),
    skip: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
    session: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_active_user),
):
    """List user's alert rules."""
    if not is_db_available():
        return []

    query = select(AlertRule).where(AlertRule.user_id == user.id)
    if activ is not None:
        query = query.where(AlertRule.activ == activ)

    query = query.order_by(AlertRule.created_at.desc()).offset(skip).limit(limit)
    result = await session.execute(query)
    rules = result.scalars().all()

    return [_rule_to_response(r) for r in rules]


@router.get("/{rule_id}", response_model=AlertRuleResponse)
async def get_alert_rule(
    rule_id: str,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_active_user),
):
    """Get a single alert rule."""
    if not is_db_available():
        raise HTTPException(status_code=503, detail="Database not available")

    result = await session.execute(
        select(AlertRule).where(AlertRule.id == rule_id)
    )
    rule = result.scalar_one_or_none()
    if not rule:
        raise HTTPException(404, "Regulă de alertă negăsită")

    if rule.user_id != user.id and user.rol != "admin":
        raise HTTPException(403, "Nu aveți acces la această regulă")

    return _rule_to_response(rule)


@router.put("/{rule_id}", response_model=AlertRuleResponse)
async def update_alert_rule(
    rule_id: str,
    request: AlertRuleUpdate,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_active_user),
):
    """Update an alert rule."""
    if not is_db_available():
        raise HTTPException(status_code=503, detail="Database not available")

    result = await session.execute(
        select(AlertRule).where(AlertRule.id == rule_id)
    )
    rule = result.scalar_one_or_none()
    if not rule:
        raise HTTPException(404, "Regulă de alertă negăsită")

    if rule.user_id != user.id and user.rol != "admin":
        raise HTTPException(403, "Nu aveți acces la această regulă")

    update_data = request.model_dump(exclude_unset=True)

    if "filters" in update_data and update_data["filters"] is not None:
        filters_dict = update_data["filters"]
        if isinstance(filters_dict, AlertFilters):
            filters_dict = filters_dict.model_dump()
        has_filter = any(v for v in filters_dict.values() if v)
        if not has_filter:
            raise HTTPException(400, "Trebuie definit cel puțin un filtru")
        update_data["filters"] = filters_dict

    for key, value in update_data.items():
        setattr(rule, key, value)

    await session.commit()
    await session.refresh(rule)

    logger.info("alert_rule_updated", rule_id=rule_id)
    return _rule_to_response(rule)


@router.delete("/{rule_id}")
async def delete_alert_rule(
    rule_id: str,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_active_user),
):
    """Delete an alert rule."""
    if not is_db_available():
        raise HTTPException(status_code=503, detail="Database not available")

    result = await session.execute(
        select(AlertRule).where(AlertRule.id == rule_id)
    )
    rule = result.scalar_one_or_none()
    if not rule:
        raise HTTPException(404, "Regulă de alertă negăsită")

    if rule.user_id != user.id and user.rol != "admin":
        raise HTTPException(403, "Nu aveți acces la această regulă")

    await session.delete(rule)
    await session.commit()

    logger.info("alert_rule_deleted", rule_id=rule_id)
    return {"status": "deleted"}


@router.post("/{rule_id}/toggle", response_model=AlertRuleResponse)
async def toggle_alert_rule(
    rule_id: str,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_active_user),
):
    """Toggle an alert rule's active status."""
    if not is_db_available():
        raise HTTPException(status_code=503, detail="Database not available")

    result = await session.execute(
        select(AlertRule).where(AlertRule.id == rule_id)
    )
    rule = result.scalar_one_or_none()
    if not rule:
        raise HTTPException(404, "Regulă de alertă negăsită")

    if rule.user_id != user.id and user.rol != "admin":
        raise HTTPException(403, "Nu aveți acces la această regulă")

    rule.activ = not rule.activ
    await session.commit()
    await session.refresh(rule)

    logger.info("alert_rule_toggled", rule_id=rule_id, activ=rule.activ)
    return _rule_to_response(rule)
