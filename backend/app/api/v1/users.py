"""Admin User Management API — CRUD for user accounts."""

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import require_role
from app.core.logging import get_logger
from app.db.session import get_session
from app.models.decision import User

router = APIRouter()
logger = get_logger(__name__)


# =============================================================================
# SCHEMAS
# =============================================================================

class UserListItem(BaseModel):
    id: str
    email: str | None
    nume: str | None
    rol: str
    activ: bool
    email_verified: bool
    created_at: str | None
    last_login: str | None


class UpdateUserRequest(BaseModel):
    rol: str | None = Field(None, max_length=30)
    activ: bool | None = None
    nume: str | None = Field(None, max_length=200)


VALID_ROLES = {"admin", "registered", "paid_basic", "paid_pro", "paid_enterprise"}


# =============================================================================
# ENDPOINTS (all admin-only)
# =============================================================================

@router.get("/")
async def list_users(
    skip: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
    rol: str | None = None,
    activ: bool | None = None,
    search: str | None = None,
    session: AsyncSession = Depends(get_session),
    admin: User = Depends(require_role("admin")),
):
    """List all users with optional filtering."""
    query = select(User).order_by(User.created_at.desc())

    if rol:
        query = query.where(User.rol == rol)
    if activ is not None:
        query = query.where(User.activ == activ)
    if search:
        search_term = f"%{search}%"
        query = query.where(
            (User.email.ilike(search_term)) | (User.nume.ilike(search_term))
        )

    # Count total
    count_query = select(func.count()).select_from(query.subquery())
    total = (await session.execute(count_query)).scalar() or 0

    # Paginate
    result = await session.execute(query.offset(skip).limit(limit))
    users = result.scalars().all()

    return {
        "users": [
            UserListItem(
                id=u.id,
                email=u.email,
                nume=u.nume,
                rol=u.rol,
                activ=u.activ,
                email_verified=u.email_verified,
                created_at=u.created_at.isoformat() if u.created_at else None,
                last_login=u.last_login.isoformat() if u.last_login else None,
            )
            for u in users
        ],
        "total": total,
        "skip": skip,
        "limit": limit,
    }


@router.get("/{user_id}")
async def get_user(
    user_id: str,
    session: AsyncSession = Depends(get_session),
    admin: User = Depends(require_role("admin")),
):
    """Get user detail by ID."""
    result = await session.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="Utilizator inexistent")

    from app.core.rate_limiter import get_user_usage
    return {
        "id": user.id,
        "email": user.email,
        "nume": user.nume,
        "rol": user.rol,
        "activ": user.activ,
        "email_verified": user.email_verified,
        "created_at": user.created_at.isoformat() if user.created_at else None,
        "updated_at": user.updated_at.isoformat() if user.updated_at else None,
        "last_login": user.last_login.isoformat() if user.last_login else None,
        "queries_today": await get_user_usage(user.id),
    }


@router.put("/{user_id}")
async def update_user(
    user_id: str,
    req: UpdateUserRequest,
    session: AsyncSession = Depends(get_session),
    admin: User = Depends(require_role("admin")),
):
    """Update user role, status, or name."""
    result = await session.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="Utilizator inexistent")

    if req.rol is not None:
        if req.rol not in VALID_ROLES:
            raise HTTPException(
                status_code=400,
                detail=f"Rol invalid. Roluri valide: {', '.join(sorted(VALID_ROLES))}",
            )
        user.rol = req.rol
    if req.activ is not None:
        user.activ = req.activ
    if req.nume is not None:
        user.nume = req.nume

    await session.commit()
    await session.refresh(user)

    logger.info(
        "admin_user_updated",
        admin_id=admin.id,
        target_user_id=user.id,
        changes=req.model_dump(exclude_none=True),
    )

    return {"status": "ok", "user_id": user.id}


@router.delete("/{user_id}")
async def delete_user(
    user_id: str,
    session: AsyncSession = Depends(get_session),
    admin: User = Depends(require_role("admin")),
):
    """Delete a user and all their saved content (cascading)."""
    result = await session.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="Utilizator inexistent")

    if user.id == admin.id:
        raise HTTPException(
            status_code=400, detail="Nu vă puteți șterge propriul cont"
        )

    logger.info(
        "admin_user_deleted",
        admin_id=admin.id,
        target_user_id=user.id,
        target_email=user.email,
    )

    await session.delete(user)
    await session.commit()
    return {"status": "ok", "message": "Utilizatorul a fost șters"}
