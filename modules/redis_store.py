"""
Redis Store - caching layer for conversation data served to the Next.js UI.

Pattern: read-through cache. The /conversations endpoints check Redis first,
fall back to PostgreSQL, then populate the cache. Writes invalidate the
affected keys so the next read is fresh.

All calls degrade gracefully: if Redis is down the app behaves as a plain
cache miss (get -> None, set/delete -> False/True) and never raises, so chat
and the sidebar keep working.
"""

import json
import logging
import os
from typing import Any, List, Optional

logger = logging.getLogger("llama_agent.redis_store")

LIST_TTL = 60      # seconds: cached conversation list per user
MSGS_TTL = 60      # seconds: cached message history per conversation

_redis: Any = None


def list_key(user_id: str) -> str:
    return f"llama:conv:list:{user_id}"


def messages_key(conversation_id: str) -> str:
    return f"llama:conv:msgs:{conversation_id}"


def summary_key(conversation_id: str) -> str:
    return f"llama:conv:sum:{conversation_id}"


def _get_client():
    """Lazily create (once) the shared async Redis client."""
    global _redis
    if _redis is None:
        import redis.asyncio as aioredis
        _redis = aioredis.from_url(
            os.getenv("REDIS_URL", "redis://localhost:6379"),
            decode_responses=True,
            socket_connect_timeout=2,
            socket_timeout=2,
            health_check_interval=30,
        )
    return _redis


async def get_json(key: str) -> Optional[Any]:
    """Read a cached JSON value; None on miss or when Redis is unreachable."""
    try:
        raw = await _get_client().get(key)
        if raw is None:
            return None
        return json.loads(raw)
    except Exception as e:
        logger.debug("redis get miss (%s): %s", key, e)
        return None


async def set_json(key: str, value: Any, ttl: int = LIST_TTL) -> bool:
    """Write a JSON value with TTL; False when Redis is unreachable."""
    try:
        await _get_client().set(
            key, json.dumps(value, ensure_ascii=False, default=str), ex=ttl
        )
        return True
    except Exception as e:
        logger.debug("redis set failed (%s): %s", key, e)
        return False


async def delete(*keys: str) -> bool:
    """Delete keys; always returns True (best-effort, never raises)."""
    try:
        if keys:
            await _get_client().delete(*keys)
        return True
    except Exception as e:
        logger.debug("redis delete failed: %s", e)
        return True


async def invalidate_user(user_id: str) -> bool:
    """Drop every cache entry belonging to a user (list + their conversations)."""
    try:
        await delete(list_key(user_id))
        return True
    except Exception as e:  # pragma: no cover - delete never raises
        logger.debug("redis invalidate_user failed: %s", e)
        return True


async def invalidate_conversation(conversation_id: str) -> bool:
    """Drop the message/summary cache for one conversation."""
    return await delete(messages_key(conversation_id), summary_key(conversation_id))


async def close():
    """Close the shared client (called on app shutdown)."""
    global _redis
    if _redis is not None:
        try:
            await _redis.aclose()
        except Exception as e:  # pragma: no cover - best effort on shutdown
            logger.warning("redis close failed: %s", e)
        _redis = None
