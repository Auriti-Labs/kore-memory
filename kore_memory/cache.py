"""
Kore — Optional Redis Cache Layer
Provides caching for search results, graph data, and analytics.
Disabled by default — activate with KORE_REDIS_ENABLED=1.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from . import config

logger = logging.getLogger(__name__)

# ── Redis client (lazy-loaded) ────────────────────────────────────────────────

_redis_client = None
_redis_available = False


def _get_redis_client():
    """Lazy-load Redis client to avoid hard dependency."""
    global _redis_client, _redis_available

    if not config.REDIS_ENABLED:
        return None

    if _redis_client is not None:
        return _redis_client

    try:
        import redis
        from redis.exceptions import ConnectionError, TimeoutError

        _redis_client = redis.Redis(
            host=config.REDIS_HOST,
            port=config.REDIS_PORT,
            db=config.REDIS_DB,
            password=config.REDIS_PASSWORD or None,
            decode_responses=True,
            socket_connect_timeout=5,
            socket_timeout=5,
        )
        # Test connection
        _redis_client.ping()
        _redis_available = True
        logger.info(
            "Redis cache enabled: %s:%d/%d (prefix=%s)",
            config.REDIS_HOST,
            config.REDIS_PORT,
            config.REDIS_DB,
            config.REDIS_PREFIX,
        )
        return _redis_client

    except ImportError:
        logger.warning("Redis cache requested but redis-py not installed (pip install redis)")
        _redis_available = False
        return None

    except (ConnectionError, TimeoutError) as e:
        logger.warning("Redis connection failed: %s — cache disabled", e)
        _redis_available = False
        return None

    except Exception as e:
        logger.warning("Redis unexpected error: %s — cache disabled", e)
        _redis_available = False
        return None


def is_available() -> bool:
    """Check if Redis cache is available and connected."""
    if not config.REDIS_ENABLED:
        return False
    if _redis_available:
        return True
    # Try to connect
    return _get_redis_client() is not None


# ── Cache helpers ─────────────────────────────────────────────────────────────


def _key(name: str) -> str:
    """Build cache key with prefix."""
    return f"{config.REDIS_PREFIX}{name}"


def _serialize(value: Any) -> str:
    """Serialize value to JSON string, handling Pydantic models."""
    def _default(o):
        # Handle Pydantic v2 models
        if hasattr(o, "model_dump"):
            return o.model_dump()
        if hasattr(o, "dict"):
            # Pydantic v1 fallback
            return o.dict()
        return str(o)
    return json.dumps(value, default=_default)


def _deserialize(value: str | None) -> Any | None:
    """Deserialize JSON string to Python value."""
    if value is None:
        return None
    try:
        return json.loads(value)
    except (json.JSONDecodeError, TypeError):
        return None


# ── Cache operations ──────────────────────────────────────────────────────────


def get(name: str) -> Any | None:
    """Get value from cache by name."""
    client = _get_redis_client()
    if not client:
        return None

    try:
        value = client.get(_key(name))
        return _deserialize(value)
    except Exception as e:
        logger.debug("Cache get error for %s: %s", name, e)
        return None


def set(name: str, value: Any, ttl: int | None = None) -> bool:
    """Set value in cache with optional TTL."""
    client = _get_redis_client()
    if not client:
        return False

    try:
        key = _key(name)
        serialized = _serialize(value)
        if ttl is None:
            ttl = config.REDIS_TTL
        if ttl > 0:
            client.setex(key, ttl, serialized)
        else:
            client.set(key, serialized)
        return True
    except Exception as e:
        logger.debug("Cache set error for %s: %s", name, e)
        return False


def delete(name: str) -> bool:
    """Delete value from cache."""
    client = _get_redis_client()
    if not client:
        return False

    try:
        client.delete(_key(name))
        return True
    except Exception as e:
        logger.debug("Cache delete error for %s: %s", name, e)
        return False


def clear_pattern(pattern: str) -> int:
    """Clear all keys matching pattern (e.g., 'search:*'). Returns count deleted."""
    client = _get_redis_client()
    if not client:
        return 0

    try:
        full_pattern = _key(pattern)
        keys = list(client.scan_iter(match=full_pattern))
        if keys:
            return client.delete(*keys)
        return 0
    except Exception as e:
        logger.debug("Cache clear error for %s: %s", pattern, e)
        return 0


# ── High-level cache wrappers ─────────────────────────────────────────────────


class SearchCache:
    """Cache wrapper for search results."""

    @staticmethod
    def _cache_key(query: str, agent_id: str, semantic: bool, limit: int) -> str:
        return f"search:{agent_id}:{'sem' if semantic else 'fts'}:{limit}:{hash(query)}"

    @classmethod
    def get(cls, query: str, agent_id: str, semantic: bool, limit: int) -> list[dict] | None:
        """Get cached search results."""
        key = cls._cache_key(query, agent_id, semantic, limit)
        result = get(key)
        if result is not None:
            logger.debug("Cache HIT for search: %s", query[:50])
        return result

    @classmethod
    def set(cls, query: str, agent_id: str, semantic: bool, limit: int, results: list[dict]) -> bool:
        """Cache search results."""
        key = cls._cache_key(query, agent_id, semantic, limit)
        return set(key, results, ttl=config.REDIS_SEARCH_TTL)

    @classmethod
    def invalidate(cls, agent_id: str) -> int:
        """Invalidate all search cache for an agent."""
        return clear_pattern(f"search:{agent_id}:*")


class GraphCache:
    """Cache wrapper for graph data."""

    @staticmethod
    def _cache_key(kind: str, **params) -> str:
        param_str = ":".join(f"{k}={v}" for k, v in sorted(params.items()))
        return f"graph:{kind}:{param_str}"

    @classmethod
    def get(cls, kind: str, **params) -> dict | None:
        """Get cached graph data."""
        key = cls._cache_key(kind, **params)
        return get(key)

    @classmethod
    def set(cls, kind: str, data: dict, **params) -> bool:
        """Cache graph data."""
        key = cls._cache_key(kind, **params)
        return set(key, data, ttl=config.REDIS_GRAPH_TTL)

    @classmethod
    def invalidate(cls) -> int:
        """Invalidate all graph cache."""
        return clear_pattern("graph:*")


class AnalyticsCache:
    """Cache wrapper for analytics data."""

    @staticmethod
    def _cache_key(agent_id: str, kind: str) -> str:
        return f"analytics:{agent_id}:{kind}"

    @classmethod
    def get(cls, agent_id: str, kind: str) -> dict | None:
        """Get cached analytics."""
        key = cls._cache_key(agent_id, kind)
        return get(key)

    @classmethod
    def set(cls, agent_id: str, kind: str, data: dict) -> bool:
        """Cache analytics data."""
        key = cls._cache_key(agent_id, kind)
        return set(key, data, ttl=config.REDIS_ANALYTICS_TTL)

    @classmethod
    def invalidate(cls, agent_id: str) -> int:
        """Invalidate all analytics cache for an agent."""
        return clear_pattern(f"analytics:{agent_id}:*")


# ── Cache invalidation hooks ──────────────────────────────────────────────────


def invalidate_on_save(agent_id: str) -> None:
    """Invalidate relevant caches when a memory is saved."""
    SearchCache.invalidate(agent_id)
    AnalyticsCache.invalidate(agent_id)
    # Graph may need invalidation if entities/tags changed
    GraphCache.invalidate()


def invalidate_on_delete(agent_id: str) -> None:
    """Invalidate relevant caches when a memory is deleted."""
    SearchCache.invalidate(agent_id)
    AnalyticsCache.invalidate(agent_id)
    GraphCache.invalidate()


def invalidate_on_compress(agent_id: str) -> None:
    """Invalidate all caches after compression (many memories changed)."""
    SearchCache.invalidate(agent_id)
    AnalyticsCache.invalidate(agent_id)
    GraphCache.invalidate()
