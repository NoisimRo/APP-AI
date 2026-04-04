"""Daily rate limiter for LLM query endpoints.

Uses Redis when available for persistence across restarts and multi-instance
support. Falls back to in-memory counters when Redis is unavailable.
"""

from collections import defaultdict
from datetime import date, datetime, timezone
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

# In-memory fallback counter: {(identifier, date_str): count}
_daily_counts: dict[tuple[str, str], int] = defaultdict(int)


def _get_identifier(user: Optional[User], request: Request) -> str:
    """Get rate limit identifier: user_id if authenticated, IP if not."""
    if user:
        return f"user:{user.id}"
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return f"ip:{forwarded.split(',')[0].strip()}"
    return f"ip:{request.client.host if request.client else 'unknown'}"


def _redis_key(identifier: str, date_str: str) -> str:
    """Build Redis key for rate limiting."""
    return f"expertap:rl:{identifier}:{date_str}"


def _seconds_until_midnight() -> int:
    """Seconds remaining until midnight UTC (TTL for daily counters)."""
    now = datetime.now(timezone.utc)
    midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
    # Add 1 day to get next midnight
    from datetime import timedelta
    next_midnight = midnight + timedelta(days=1)
    return int((next_midnight - now).total_seconds())


async def _redis_get_count(identifier: str, date_str: str) -> int:
    """Get count from Redis, or -1 if unavailable."""
    from app.core.redis import rate_limit_get
    return await rate_limit_get(_redis_key(identifier, date_str))


async def _redis_increment(identifier: str, date_str: str) -> int:
    """Increment count in Redis, or -1 if unavailable."""
    from app.core.redis import rate_limit_increment
    ttl = _seconds_until_midnight()
    return await rate_limit_increment(_redis_key(identifier, date_str), ttl)


async def get_user_usage(user_id: str) -> int:
    """Get today's usage count for a specific user."""
    today = date.today().isoformat()
    identifier = f"user:{user_id}"

    # Try Redis first
    redis_count = await _redis_get_count(identifier, today)
    if redis_count >= 0:
        return redis_count

    # Fallback to in-memory
    return _daily_counts.get((identifier, today), 0)


async def check_rate_limit(
    user: Optional[User], request: Request
) -> tuple[bool, int, int]:
    """Check if rate limit is exceeded.

    Returns: (allowed, used, limit)
    """
    identifier = _get_identifier(user, request)
    today = date.today().isoformat()
    role = user.rol if user else "anonymous"
    limit = ROLE_LIMITS.get(role, 5)

    # Try Redis first
    redis_count = await _redis_get_count(identifier, today)
    if redis_count >= 0:
        return (redis_count < limit, redis_count, limit)

    # Fallback to in-memory
    used = _daily_counts[(identifier, today)]
    return (used < limit, used, limit)


async def increment_usage(user: Optional[User], request: Request) -> None:
    """Increment daily counter after a successful LLM query."""
    identifier = _get_identifier(user, request)
    today = date.today().isoformat()

    # Try Redis first
    redis_count = await _redis_increment(identifier, today)
    if redis_count >= 0:
        return  # Redis handled it

    # Fallback to in-memory
    _daily_counts[(identifier, today)] += 1


async def require_rate_limit(
    request: Request,
    user: Optional[User] = Depends(get_optional_user),
) -> Optional[User]:
    """FastAPI dependency that enforces rate limits.

    Returns the user (or None for anonymous) if within limits.
    Raises HTTP 429 if limit exceeded.
    """
    allowed, used, limit = await check_rate_limit(user, request)
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
