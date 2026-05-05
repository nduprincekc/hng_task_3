import os
import json
import hashlib
import logging
from typing import Optional, Any

logger = logging.getLogger(__name__)

REDIS_URL = os.getenv("REDIS_URL", None)
CACHE_TTL = int(os.getenv("CACHE_TTL_SECONDS", "300"))  # 5 minutes default

_redis_client = None


def get_redis():
    global _redis_client
    if _redis_client is not None:
        return _redis_client
    if not REDIS_URL:
        return None
    try:
        import redis
        _redis_client = redis.from_url(REDIS_URL, decode_responses=True, socket_timeout=2)
        _redis_client.ping()
        logger.info("Redis connected")
        return _redis_client
    except Exception as e:
        logger.warning(f"Redis unavailable, running without cache: {e}")
        return None


def make_cache_key(prefix: str, data: dict) -> str:
    """Deterministic cache key from a normalized dict."""
    # Sort keys so order never matters
    canonical = json.dumps(data, sort_keys=True, separators=(",", ":"))
    digest = hashlib.sha256(canonical.encode()).hexdigest()[:16]
    return f"insighta:{prefix}:{digest}"


def cache_get(key: str) -> Optional[Any]:
    r = get_redis()
    if not r:
        return None
    try:
        val = r.get(key)
        if val:
            return json.loads(val)
    except Exception as e:
        logger.warning(f"Cache get failed: {e}")
    return None


def cache_set(key: str, value: Any, ttl: int = CACHE_TTL) -> None:
    r = get_redis()
    if not r:
        return
    try:
        r.setex(key, ttl, json.dumps(value, default=str))
    except Exception as e:
        logger.warning(f"Cache set failed: {e}")


def cache_invalidate_prefix(prefix: str) -> None:
    """Invalidate all keys matching a prefix (used after bulk ingestion)."""
    r = get_redis()
    if not r:
        return
    try:
        keys = r.keys(f"insighta:{prefix}:*")
        if keys:
            r.delete(*keys)
    except Exception as e:
        logger.warning(f"Cache invalidation failed: {e}")