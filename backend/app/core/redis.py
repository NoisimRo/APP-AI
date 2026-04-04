"""Redis client for caching and rate limiting.

Provides an async Redis client with graceful degradation — if Redis
is unavailable, the application continues to work (rate limiting falls
back to in-memory, caching returns misses).
"""

import hashlib
import json
from typing import Optional

import redis.asyncio as aioredis

from app.core.config import get_settings
from app.core.logging import get_logger

logger = get_logger(__name__)

# Module-level client — initialized on startup
_redis: Optional[aioredis.Redis] = None
_available: bool = False


async def init_redis() -> bool:
    """Initialize Redis connection. Called from app lifespan."""
    global _redis, _available

    settings = get_settings()
    url = settings.redis_url

    if not url:
        logger.warning("redis_no_url", message="REDIS_URL not set, running without Redis")
        return False

    try:
        _redis = aioredis.from_url(
            url,
            decode_responses=True,
            socket_connect_timeout=3,
            socket_timeout=3,
            retry_on_timeout=True,
        )
        await _redis.ping()
        _available = True
        logger.info("redis_connected", url=url[:30] + "...")
        return True
    except Exception as e:
        logger.warning("redis_connection_failed", error=str(e),
                       message="Running without Redis (in-memory fallback)")
        _redis = None
        _available = False
        return False


async def close_redis() -> None:
    """Close Redis connection. Called from app lifespan shutdown."""
    global _redis, _available
    if _redis:
        await _redis.close()
        _redis = None
        _available = False
        logger.info("redis_closed")


def is_redis_available() -> bool:
    """Check if Redis is connected and operational."""
    return _available


def get_redis() -> Optional[aioredis.Redis]:
    """Get the Redis client instance (or None if unavailable)."""
    return _redis


# ---------------------------------------------------------------------------
# Rate limiting helpers
# ---------------------------------------------------------------------------

async def rate_limit_increment(key: str, ttl_seconds: int = 86400) -> int:
    """Increment a rate limit counter in Redis.

    Returns the new count, or -1 if Redis is unavailable.
    """
    if not _available or not _redis:
        return -1
    try:
        pipe = _redis.pipeline()
        pipe.incr(key)
        pipe.expire(key, ttl_seconds, nx=True)  # set TTL only if not already set
        results = await pipe.execute()
        return results[0]  # new count after INCR
    except Exception as e:
        logger.warning("redis_rate_limit_error", error=str(e))
        return -1


async def rate_limit_get(key: str) -> int:
    """Get current rate limit count from Redis.

    Returns the count, or -1 if Redis is unavailable.
    """
    if not _available or not _redis:
        return -1
    try:
        val = await _redis.get(key)
        return int(val) if val is not None else 0
    except Exception as e:
        logger.warning("redis_rate_limit_get_error", error=str(e))
        return -1


# ---------------------------------------------------------------------------
# Generic cache helpers
# ---------------------------------------------------------------------------

def _cache_key(prefix: str, data: str) -> str:
    """Build a cache key from a prefix and arbitrary string data."""
    h = hashlib.sha256(data.encode()).hexdigest()[:16]
    return f"expertap:{prefix}:{h}"


async def cache_get(key: str) -> Optional[str]:
    """Get a cached value by key. Returns None on miss or if Redis unavailable."""
    if not _available or not _redis:
        return None
    try:
        return await _redis.get(key)
    except Exception:
        return None


async def cache_set(key: str, value: str, ttl_seconds: int = 3600) -> bool:
    """Set a cached value with TTL. Returns False if Redis unavailable."""
    if not _available or not _redis:
        return False
    try:
        await _redis.set(key, value, ex=ttl_seconds)
        return True
    except Exception:
        return False


async def cache_get_json(key: str) -> Optional[dict | list]:
    """Get a JSON-deserialized cached value."""
    raw = await cache_get(key)
    if raw is None:
        return None
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return None


async def cache_set_json(key: str, value, ttl_seconds: int = 3600) -> bool:
    """Cache a JSON-serializable value."""
    try:
        raw = json.dumps(value, ensure_ascii=False)
        return await cache_set(key, raw, ttl_seconds)
    except (TypeError, ValueError):
        return False


async def cache_get_embedding(query_text: str) -> Optional[list[float]]:
    """Get a cached embedding vector for a query string."""
    key = _cache_key("emb_q", query_text)
    data = await cache_get_json(key)
    if data is not None and isinstance(data, list):
        return data
    return None


async def cache_set_embedding(query_text: str, embedding: list[float],
                              ttl_seconds: int = 604800) -> bool:
    """Cache an embedding vector for a query string (default TTL: 7 days)."""
    key = _cache_key("emb_q", query_text)
    return await cache_set_json(key, embedding, ttl_seconds)


async def health_check() -> dict:
    """Redis health check returning status and latency."""
    if not _available or not _redis:
        return {"status": "unavailable", "latency_ms": None}
    try:
        import time
        start = time.monotonic()
        await _redis.ping()
        latency = round((time.monotonic() - start) * 1000, 1)
        return {"status": "healthy", "latency_ms": latency}
    except Exception as e:
        return {"status": "error", "error": str(e), "latency_ms": None}
