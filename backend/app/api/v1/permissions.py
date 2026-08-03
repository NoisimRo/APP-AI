"""Role permissions API — the admin "Drepturi & Roluri" page.

Exposes the feature catalog and the role/feature matrix stored in
``role_permissions``. Editing is admin-only; ``/me`` is open to everyone
(including anonymous callers) because the frontend needs it to decide which
navigation entries to render.
"""

from typing import Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import permissions as perms
from app.core.deps import get_optional_user, require_role
from app.core.logging import get_logger
from app.db.session import get_session
from app.models.decision import RolePermission, User

router = APIRouter()
logger = get_logger(__name__)


# =============================================================================
# SCHEMAS
# =============================================================================

class FeatureInfo(BaseModel):
    """A single gateable page/capability, as shown in the admin matrix."""
    key: str
    label: str
    category: str
    description: str
    admin_only: bool
    always_on: bool
    ui_only: bool


class RoleInfo(BaseModel):
    """A column in the matrix."""
    rol: str
    label: str
    locked: bool = Field(
        False, description="Admin row — every feature is forced on."
    )


class PermissionsMatrixResponse(BaseModel):
    """Everything the admin page needs to render the matrix."""
    features: list[FeatureInfo]
    categories: list[str]
    roles: list[RoleInfo]
    matrix: dict[str, list[str]]
    defaults: dict[str, list[str]]
    persisted: bool = Field(
        True,
        description="False when role_permissions is unreachable and the "
                    "shipped defaults are being served instead.",
    )


class PermissionsUpdateRequest(BaseModel):
    """Replace the whole matrix. Roles omitted here are left untouched."""
    matrix: dict[str, list[str]]


class MyPermissionsResponse(BaseModel):
    """Effective access for the calling identity."""
    rol: str
    features: list[str]


# =============================================================================
# HELPERS
# =============================================================================

def _catalog() -> list[FeatureInfo]:
    return [
        FeatureInfo(
            key=f.key,
            label=f.label,
            category=f.category,
            description=f.description,
            admin_only=f.admin_only,
            always_on=f.always_on,
            ui_only=f.ui_only,
        )
        for f in perms.FEATURE_CATALOG
    ]


def _roles() -> list[RoleInfo]:
    return [
        RoleInfo(rol=r, label=perms.ROLE_LABELS.get(r, r), locked=(r == "admin"))
        for r in perms.ALL_ROLES
    ]


async def _build_response(session: AsyncSession) -> PermissionsMatrixResponse:
    """Read the live matrix and wrap it for the admin page."""
    matrix = await perms.load_matrix(session, use_cache=False)

    # Detect the fallback case so the UI can warn that nothing is persisted yet.
    persisted = True
    try:
        result = await session.execute(select(RolePermission.rol))
        persisted = len(result.scalars().all()) > 0
    except Exception:
        await session.rollback()
        persisted = False

    ordered = {
        rol: [k for k in perms.ALL_FEATURE_KEYS if k in matrix.get(rol, set())]
        for rol in perms.ALL_ROLES
    }

    return PermissionsMatrixResponse(
        features=_catalog(),
        categories=perms.FEATURE_CATEGORIES,
        roles=_roles(),
        matrix=ordered,
        defaults=perms.default_matrix(),
        persisted=persisted,
    )


# =============================================================================
# ENDPOINTS
# =============================================================================

@router.get("/", response_model=PermissionsMatrixResponse)
async def get_permissions(
    session: AsyncSession = Depends(get_session),
    _admin: User = Depends(require_role("admin")),
) -> PermissionsMatrixResponse:
    """Return the feature catalog plus the current role/feature matrix."""
    return await _build_response(session)


@router.put("/", response_model=PermissionsMatrixResponse)
async def update_permissions(
    request: PermissionsUpdateRequest,
    session: AsyncSession = Depends(get_session),
    admin: User = Depends(require_role("admin")),
) -> PermissionsMatrixResponse:
    """Persist the matrix.

    Guardrails from ``app.core.permissions.normalize_features`` are applied
    server-side, so a hand-crafted request cannot grant ``settings`` to a free
    account, strip the admin role, or invent feature keys.
    """
    existing = {}
    result = await session.execute(select(RolePermission))
    for row in result.scalars().all():
        existing[row.rol] = row

    changed: dict[str, list[str]] = {}

    for rol in perms.ALL_ROLES:
        if rol not in request.matrix:
            # Not submitted — leave whatever is stored (or seed the default).
            if rol in existing:
                continue
            features = perms.normalize_features(
                rol, perms.DEFAULT_ROLE_FEATURES.get(rol, [])
            )
        else:
            features = perms.normalize_features(rol, request.matrix[rol])

        row = existing.get(rol)
        if row is None:
            row = RolePermission(rol=rol, features=features, updated_by=str(admin.id))
            session.add(row)
            changed[rol] = features
        elif list(row.features or []) != features:
            row.features = features
            row.updated_by = str(admin.id)
            changed[rol] = features

    await session.commit()
    perms.invalidate_cache()

    logger.info(
        "role_permissions_updated",
        admin_id=str(admin.id),
        roles_changed=list(changed.keys()),
        matrix={r: len(f) for r, f in changed.items()},
    )

    return await _build_response(session)


@router.post("/reset", response_model=PermissionsMatrixResponse)
async def reset_permissions(
    session: AsyncSession = Depends(get_session),
    admin: User = Depends(require_role("admin")),
) -> PermissionsMatrixResponse:
    """Restore every role to the shipped defaults."""
    result = await session.execute(select(RolePermission))
    existing = {row.rol: row for row in result.scalars().all()}

    for rol in perms.ALL_ROLES:
        features = perms.normalize_features(rol, perms.DEFAULT_ROLE_FEATURES.get(rol, []))
        row = existing.get(rol)
        if row is None:
            session.add(RolePermission(rol=rol, features=features, updated_by=str(admin.id)))
        else:
            row.features = features
            row.updated_by = str(admin.id)

    await session.commit()
    perms.invalidate_cache()

    logger.info("role_permissions_reset", admin_id=str(admin.id))
    return await _build_response(session)


@router.get("/me", response_model=MyPermissionsResponse)
async def get_my_permissions(
    session: AsyncSession = Depends(get_session),
    user: Optional[User] = Depends(get_optional_user),
) -> MyPermissionsResponse:
    """Effective feature list for the caller — drives frontend navigation.

    Open to anonymous callers, who are resolved against the ``anonymous``
    pseudo-role. This is a convenience for rendering, not a security boundary:
    every protected endpoint still enforces its own ``require_feature``.
    """
    rol = user.rol if user else perms.ANONYMOUS_ROLE
    features = await perms.get_role_features(session, rol)
    return MyPermissionsResponse(
        rol=rol,
        features=[k for k in perms.ALL_FEATURE_KEYS if k in features],
    )
