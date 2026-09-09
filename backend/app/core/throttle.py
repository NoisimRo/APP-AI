"""Fixed-window abuse throttling for sensitive endpoints (login, register, …).

Distinct from the daily LLM quota in ``rate_limiter``: these windows are
short (minutes) and keyed per client IP or per account so brute-force,
account-spam and email-bombing attempts are cut off. Uses Redis when it is
up, so all Cloud Run instances share one counter; falls back to a process
local dict otherwise.
"""

import time
from collections import defaultdict

from fastapi import HTTPException, Request, status

from app.core.logging import get_logger

logger = get_logger(__name__)

# In-memory fallback: {key: (window_start_epoch, count)}
_windows: dict[str, tuple[float, int]] = defaultdict(lambda: (0.0, 0))
_MAX_MEMORY_KEYS = 50_000


def get_client_ip(request: Request) -> str:
    """Best-effort client IP.

    Cloud Run appends the real client address as the *last* entry of
    ``X-Forwarded-For``; anything before it was supplied by the client and
    must not be trusted, otherwise per-IP limits are trivially bypassed.
    """
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        parts = [p.strip() for p in forwarded.split(",") if p.strip()]
        if parts:
            return parts[-1]
    return request.client.host if request.client else "unknown"


def _prune_memory() -> None:
    """Drop expired in-memory windows so the fallback dict cannot grow forever."""
    if len(_windows) < _MAX_MEMORY_KEYS:
        return
    now = time.monotonic()
    for key in [k for k, (start, _) in _windows.items() if now - start > 3600]:
        _windows.pop(key, None)


async def hit(key: str, limit: int, window_seconds: int) -> bool:
    """Record one attempt for ``key``; return True if it is still allowed."""
    redis_key = f"expertap:throttle:{key}"
    from app.core.redis import rate_limit_increment

    count = await rate_limit_increment(redis_key, window_seconds)
    if count >= 0:
        return count <= limit

    now = time.monotonic()
    start, used = _windows[key]
    if now - start > window_seconds:
        start, used = now, 0
    used += 1
    _windows[key] = (start, used)
    _prune_memory()
    return used <= limit


async def enforce(key: str, limit: int, window_seconds: int, message: str) -> None:
    """Raise HTTP 429 once ``key`` exceeds ``limit`` attempts per window."""
    if await hit(key, limit, window_seconds):
        return
    logger.warning("throttle_exceeded", key=key, limit=limit, window=window_seconds)
    raise HTTPException(
        status_code=status.HTTP_429_TOO_MANY_REQUESTS,
        detail=message,
        headers={"Retry-After": str(window_seconds)},
    )
