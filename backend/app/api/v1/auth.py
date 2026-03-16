"""Authentication API — register, login, token refresh, profile."""

import secrets
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordRequestForm
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import (
    create_access_token,
    create_refresh_token,
    decode_token,
    get_password_hash,
    verify_password,
)
from app.core.deps import get_current_active_user
from app.core.logging import get_logger
from app.db.session import get_session
from app.models.decision import User

router = APIRouter()
logger = get_logger(__name__)


# =============================================================================
# PYDANTIC SCHEMAS
# =============================================================================

class RegisterRequest(BaseModel):
    email: str = Field(..., min_length=5, max_length=255)
    password: str = Field(..., min_length=8, max_length=128)
    nume: str | None = Field(None, max_length=200)


class LoginResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    user: dict


class RefreshRequest(BaseModel):
    refresh_token: str


class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str = Field(..., min_length=8, max_length=128)


class ForgotPasswordRequest(BaseModel):
    email: str


class ResetPasswordRequest(BaseModel):
    token: str
    new_password: str = Field(..., min_length=8, max_length=128)


class UpdateProfileRequest(BaseModel):
    nume: str | None = Field(None, max_length=200)


def _user_to_dict(user: User, queries_today: int = 0, queries_limit: int = 5) -> dict:
    """Serialize user to response dict."""
    from app.core.rate_limiter import ROLE_LIMITS
    limit = ROLE_LIMITS.get(user.rol, 5)
    return {
        "id": user.id,
        "email": user.email,
        "nume": user.nume,
        "rol": user.rol,
        "activ": user.activ,
        "email_verified": user.email_verified,
        "created_at": user.created_at.isoformat() if user.created_at else None,
        "queries_today": queries_today,
        "queries_limit": limit,
    }


def _create_tokens(user: User) -> tuple[str, str]:
    """Create access + refresh token pair for a user."""
    token_data = {"sub": user.id, "email": user.email, "rol": user.rol}
    return (
        create_access_token(token_data),
        create_refresh_token(token_data),
    )


# =============================================================================
# ENDPOINTS
# =============================================================================

@router.post("/register", response_model=LoginResponse)
async def register(
    req: RegisterRequest,
    session: AsyncSession = Depends(get_session),
):
    """Register a new user account."""
    # Check email uniqueness
    result = await session.execute(
        select(User).where(User.email == req.email.lower().strip())
    )
    if result.scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Adresa de email este deja înregistrată",
        )

    user = User(
        email=req.email.lower().strip(),
        nume=req.nume,
        password_hash=get_password_hash(req.password),
        rol="registered",
        activ=True,
        email_verified=False,
    )
    session.add(user)
    await session.commit()
    await session.refresh(user)

    logger.info("user_registered", email=user.email, user_id=user.id)

    access_token, refresh_token = _create_tokens(user)
    return LoginResponse(
        access_token=access_token,
        refresh_token=refresh_token,
        user=_user_to_dict(user),
    )


@router.post("/login", response_model=LoginResponse)
async def login(
    form_data: OAuth2PasswordRequestForm = Depends(),
    session: AsyncSession = Depends(get_session),
):
    """Login with email and password. Uses OAuth2 form for Swagger compatibility."""
    result = await session.execute(
        select(User).where(User.email == form_data.username.lower().strip())
    )
    user = result.scalar_one_or_none()

    if not user or not user.password_hash:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Email sau parolă incorectă",
        )

    if not verify_password(form_data.password, user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Email sau parolă incorectă",
        )

    if not user.activ:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Contul este dezactivat",
        )

    # Update last_login
    user.last_login = datetime.now(timezone.utc)
    await session.commit()

    logger.info("user_login", email=user.email, user_id=user.id)

    access_token, refresh_token = _create_tokens(user)
    return LoginResponse(
        access_token=access_token,
        refresh_token=refresh_token,
        user=_user_to_dict(user),
    )


@router.post("/refresh")
async def refresh_token(
    req: RefreshRequest,
    session: AsyncSession = Depends(get_session),
):
    """Refresh access token using a valid refresh token."""
    payload = decode_token(req.refresh_token)
    if payload is None or payload.get("type") != "refresh":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Refresh token invalid sau expirat",
        )

    user_id = payload.get("sub")
    result = await session.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()

    if not user or not user.activ:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Utilizator inexistent sau dezactivat",
        )

    access_token, new_refresh = _create_tokens(user)
    return {
        "access_token": access_token,
        "refresh_token": new_refresh,
        "token_type": "bearer",
    }


@router.get("/me")
async def get_me(
    user: User = Depends(get_current_active_user),
    session: AsyncSession = Depends(get_session),
):
    """Get current user profile and usage stats."""
    from app.core.rate_limiter import get_user_usage
    queries_today = get_user_usage(user.id)
    return _user_to_dict(user, queries_today=queries_today)


@router.put("/me")
async def update_me(
    req: UpdateProfileRequest,
    user: User = Depends(get_current_active_user),
    session: AsyncSession = Depends(get_session),
):
    """Update current user profile."""
    if req.nume is not None:
        user.nume = req.nume
    await session.commit()
    await session.refresh(user)
    return _user_to_dict(user)


@router.post("/change-password")
async def change_password(
    req: ChangePasswordRequest,
    user: User = Depends(get_current_active_user),
    session: AsyncSession = Depends(get_session),
):
    """Change password for current user."""
    if not user.password_hash or not verify_password(
        req.current_password, user.password_hash
    ):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Parola curentă este incorectă",
        )

    user.password_hash = get_password_hash(req.new_password)
    await session.commit()
    return {"status": "ok", "message": "Parola a fost schimbată cu succes"}


@router.post("/forgot-password")
async def forgot_password(
    req: ForgotPasswordRequest,
    session: AsyncSession = Depends(get_session),
):
    """Request password reset. Always returns 200 (no email enumeration)."""
    result = await session.execute(
        select(User).where(User.email == req.email.lower().strip())
    )
    user = result.scalar_one_or_none()

    if user and user.password_hash:
        # Generate reset token
        raw_token = secrets.token_urlsafe(32)
        user.reset_token = get_password_hash(raw_token)
        user.reset_token_expires = datetime.now(timezone.utc).replace(
            hour=datetime.now(timezone.utc).hour + 1
        )
        await session.commit()

        # TODO: Send email with reset link containing raw_token
        logger.info(
            "password_reset_requested",
            email=user.email,
            token=raw_token,  # Log for manual handling until email is integrated
        )

    return {"status": "ok", "message": "Dacă adresa există, veți primi un email cu instrucțiuni"}


@router.post("/reset-password")
async def reset_password(
    req: ResetPasswordRequest,
    session: AsyncSession = Depends(get_session),
):
    """Reset password using a valid reset token."""
    # Find users with non-expired reset tokens
    result = await session.execute(
        select(User).where(
            User.reset_token.isnot(None),
            User.reset_token_expires > datetime.now(timezone.utc),
        )
    )
    users = result.scalars().all()

    # Check token against each user's hashed reset_token
    target_user = None
    for u in users:
        if verify_password(req.token, u.reset_token):
            target_user = u
            break

    if not target_user:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Token de resetare invalid sau expirat",
        )

    target_user.password_hash = get_password_hash(req.new_password)
    target_user.reset_token = None
    target_user.reset_token_expires = None
    await session.commit()

    logger.info("password_reset_completed", email=target_user.email)
    return {"status": "ok", "message": "Parola a fost resetată cu succes"}
