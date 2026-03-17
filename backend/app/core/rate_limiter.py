"""In-memory daily rate limiter for LLM query endpoints."""

from collections import defaultdict
from datetime import date
from typing import Optional

from fastapi import Depends, HTTPException, Request, status

from app.core.deps import get_optional_user
from app.core.logging import get_logger
from app.models.decision import User

logger = get_logger(__name__)

# Role → max LLM queries per day
ROLE_LIMITS = {
    "anonymous": 5,
    "registered": 5,
    "paid_basic": 20,
    "paid_pro": 100,
    "paid_enterprise": 99999,
    "admin": 99999,
}

# In-memory daily counter: {(identifier, date_str): count}
_daily_counts: dict[tuple[str, str], int] = defaultdict(int)


def _get_identifier(user: Optional[User], request: Request) -> str:
    """Get rate limit identifier: user_id if authenticated, IP if not."""
    if user:
        return f"user:{user.id}"
    # Use client IP for anonymous users
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return f"ip:{forwarded.split(',')[0].strip()}"
    return f"ip:{request.client.host if request.client else 'unknown'}"


def get_user_usage(user_id: str) -> int:
    """Get today's usage count for a specific user."""
    today = date.today().isoformat()
    return _daily_counts.get((f"user:{user_id}", today), 0)


def check_rate_limit(
    user: Optional[User], request: Request
) -> tuple[bool, int, int]:
    """Check if rate limit is exceeded.

    Returns: (allowed, used, limit)
    """
    identifier = _get_identifier(user, request)
    today = date.today().isoformat()
    role = user.rol if user else "anonymous"
    limit = ROLE_LIMITS.get(role, 5)
    used = _daily_counts[(identifier, today)]
    return (used < limit, used, limit)


def increment_usage(user: Optional[User], request: Request) -> None:
    """Increment daily counter after a successful LLM query."""
    identifier = _get_identifier(user, request)
    today = date.today().isoformat()
    _daily_counts[(identifier, today)] += 1


async def require_rate_limit(
    request: Request,
    user: Optional[User] = Depends(get_optional_user),
) -> Optional[User]:
    """FastAPI dependency that enforces rate limits.

    Returns the user (or None for anonymous) if within limits.
    Raises HTTP 429 if limit exceeded.
    """
    allowed, used, limit = check_rate_limit(user, request)
    if not allowed:
        role = user.rol if user else "anonymous"
        logger.warning(
            "rate_limit_exceeded",
            identifier=_get_identifier(user, request),
            role=role,
            used=used,
            limit=limit,
        )
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail={
                "message": f"Ați atins limita de {limit} interogări pe zi",
                "used": used,
                "limit": limit,
                "plan": role,
            },
            headers={"Retry-After": "86400"},
        )
    return user
