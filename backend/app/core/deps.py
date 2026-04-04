"""FastAPI auth dependencies — injectable into route handlers."""

from typing import Optional

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import decode_token
from app.core.logging import get_logger
from app.db.session import get_session
from app.models.decision import User

logger = get_logger(__name__)

# auto_error=False allows unauthenticated requests to pass through
oauth2_scheme = OAuth2PasswordBearer(
    tokenUrl="/api/v1/auth/login", auto_error=False
)


async def get_current_user(
    token: Optional[str] = Depends(oauth2_scheme),
    session: AsyncSession = Depends(get_session),
) -> Optional[User]:
    """Returns User if valid token, None if no token, raises 401 if invalid."""
    if token is None:
        return None

    payload = decode_token(token)
    if payload is None or payload.get("type") != "access":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token invalid sau expirat",
            headers={"WWW-Authenticate": "Bearer"},
        )

    user_id = payload.get("sub")
    if not user_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token invalid",
            headers={"WWW-Authenticate": "Bearer"},
        )

    result = await session.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Utilizator inexistent",
            headers={"WWW-Authenticate": "Bearer"},
        )

    return user


async def get_current_active_user(
    current_user: Optional[User] = Depends(get_current_user),
) -> User:
    """Requires authentication. Raises 401 if not authenticated, 403 if deactivated."""
    if current_user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Autentificare necesară",
            headers={"WWW-Authenticate": "Bearer"},
        )
    if not current_user.activ:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Contul este dezactivat",
        )
    return current_user


async def get_optional_user(
    current_user: Optional[User] = Depends(get_current_user),
) -> Optional[User]:
    """Returns user if authenticated, None otherwise. Never raises."""
    return current_user


def require_role(*roles: str):
    """Dependency factory that checks if user has one of the required roles."""
    async def _check_role(
        user: User = Depends(get_current_active_user),
    ) -> User:
        if user.rol not in roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Acces interzis — rol insuficient",
            )
        return user
    return _check_role


# Feature access rules per role
FEATURE_ROLES = {
    "chat": ["registered", "paid_basic", "paid_pro", "paid_enterprise", "admin"],
    "rag": ["registered", "paid_basic", "paid_pro", "paid_enterprise", "admin"],
    "dashboard": ["registered", "paid_basic", "paid_pro", "paid_enterprise", "admin"],
    "datalake": ["registered", "paid_basic", "paid_pro", "paid_enterprise", "admin"],
    "drafter": ["paid_basic", "paid_pro", "paid_enterprise", "admin"],
    "redflags": ["paid_basic", "paid_pro", "paid_enterprise", "admin"],
    "clarification": ["paid_basic", "paid_pro", "paid_enterprise", "admin"],
    "training": ["paid_pro", "paid_enterprise", "admin"],
    "export": ["paid_pro", "paid_enterprise", "admin"],
    "strategy": ["paid_basic", "paid_pro", "paid_enterprise", "admin"],
    "compliance": ["paid_basic", "paid_pro", "paid_enterprise", "admin"],
    "multi_document": ["paid_pro", "paid_enterprise", "admin"],
    "dosare": ["paid_basic", "paid_pro", "paid_enterprise", "admin"],
    "alerts": ["paid_basic", "paid_pro", "paid_enterprise", "admin"],
    "comments": ["paid_basic", "paid_pro", "paid_enterprise", "admin"],
}


def require_feature(feature: str):
    """Dependency factory that checks if user's role allows access to a feature.

    Anonymous users get access to free-tier features (chat, rag, dashboard, datalake).
    """
    allowed_roles = FEATURE_ROLES.get(feature, [])

    async def _check_feature(
        user: Optional[User] = Depends(get_optional_user),
    ) -> Optional[User]:
        # Anonymous access: only free features
        if user is None:
            if feature in ("chat", "rag", "dashboard", "datalake"):
                return None
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Autentificare necesară pentru această funcționalitate",
                headers={"WWW-Authenticate": "Bearer"},
            )

        if user.rol not in allowed_roles:
            # Map feature to required plan for helpful error message
            plan_needed = "Basic"
            if feature == "training" or feature == "export":
                plan_needed = "Pro"
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Funcționalitate disponibilă în planul {plan_needed}. "
                       f"Planul curent: {user.rol}",
            )
        return user

    return _check_feature
