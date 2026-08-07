"""
Postgres Store - durable persistence for conversations and messages.

The Next.js frontend chats through the FastAPI API. This module mirrors every
chat message into PostgreSQL so conversation history is stored in the real
database (not only the JSON memory file), and provides a read API that the
/conversations endpoints serve from.

Design:
- Every public function degrades gracefully: if the database is unreachable we
  log a warning and return None / [] / False so the caller keeps working on the
  JSON memory store (chat must never break because the mirror is down).
- conversation ids and user ids are normalized to real UUIDs so any format
  (real UUID, legacy "default_<uuid>" strings, short hex ids) maps
  deterministically to the same Postgres row on every request.

Tables (created by the app's SQL bootstrap):
    conversations(id uuid PK, user_id uuid, title text, created_at,
                  updated_at, is_active boolean)
    messages(id uuid PK, conversation_id uuid, role text, content text,
             metadata_json jsonb, created_at)
"""

import logging
import os
import uuid
from typing import Any, Dict, List, Optional

logger = logging.getLogger("llama_agent.pg_store")

_pool: Any = None


def get_database_url() -> str:
    """Return the asyncpg-compatible DATABASE_URL (no driver suffix)."""
    url = os.getenv(
        "DATABASE_URL",
        "postgresql+asyncpg://postgres:postgres@localhost:5432/llama_agent",
    )
    return url.replace("+asyncpg", "")


def is_registered_user(user_id: Optional[str]) -> bool:
    """True when user_id is a real UUID (i.e. a registered JWT user).

    Anonymous/derived ids (e.g. "guest", usernames) are normalized to a
    deterministic uuid5 that has no row in the FK-protected `users` table, so
    writes for them must be skipped rather than attempting an INSERT that can
    never satisfy the conversations_user_id_fkey constraint.
    """
    try:
        uuid.UUID(str(user_id or ""))
        return True
    except (ValueError, AttributeError):
        return False


def normalize_user_id(user_id: Optional[str]) -> str:
    """Map any user identifier to a UUID string for the users/conversations tables."""
    user_id = user_id or "guest"
    try:
        return str(uuid.UUID(str(user_id)))
    except (ValueError, AttributeError):
        return str(uuid.uuid5(uuid.NAMESPACE_DNS, f"llama-agent-user:{user_id}"))


def normalize_conversation_id(conversation_id: Optional[str]) -> str:
    """Map any conversation id to a UUID string.

    Real UUIDs pass through unchanged. Legacy ids (e.g. "default_<uuid>",
    "diag_http_test", short hex ids) map to a deterministic UUID so resuming a
    conversation always resolves to the same Postgres row.
    """
    conversation_id = conversation_id or ""
    try:
        return str(uuid.UUID(str(conversation_id)))
    except (ValueError, AttributeError):
        return str(uuid.uuid5(uuid.NAMESPACE_DNS, f"llama-agent-conversation:{conversation_id}"))


async def get_pool():
    """Lazily create (once) a shared asyncpg pool."""
    global _pool
    if _pool is None:
        import asyncpg
        _pool = await asyncpg.create_pool(
            get_database_url(),
            min_size=1,
            max_size=10,
            command_timeout=10,
        )
    return _pool


async def close_pool():
    """Close the shared pool (called on app shutdown)."""
    global _pool
    if _pool is not None:
        try:
            await _pool.close()
        except Exception as e:  # pragma: no cover - best effort on shutdown
            logger.warning("pg_store close failed: %s", e)
        _pool = None


async def ensure_conversation(user_id: str, conversation_id: str,
                              title: str = "New chat") -> Optional[str]:
    """Create (or touch) a conversation row. Returns the normalized id or None.

    Writes for anonymous/derived users (no users row) are skipped: their chat
    history lives in the JSON memory store only.
    """
    if not is_registered_user(user_id):
        logger.debug("pg_store.ensure_conversation: skipping write for %r (memory only)", user_id)
        return None
    norm_user = normalize_user_id(user_id)
    norm_conv = normalize_conversation_id(conversation_id)
    try:
        pool = await get_pool()
        async with pool.acquire() as conn:
            await conn.execute(
                "INSERT INTO conversations (id, user_id, title, created_at, updated_at, is_active) "
                "VALUES ($1, $2, $3, NOW(), NOW(), true) "
                "ON CONFLICT (id) DO UPDATE SET updated_at = NOW()",
                norm_conv, norm_user, (title or "New chat")[:80],
            )
        return norm_conv
    except Exception as e:
        logger.warning("pg_store.ensure_conversation failed: %s", e)
        return None


async def add_message(user_id: str, conversation_id: str, role: str, content: str,
                      title: Optional[str] = None) -> Optional[str]:
    """Persist one message (auto-creating its conversation).

    Returns the normalized conversation id on success, None when the mirror is
    unavailable (caller keeps the JSON memory store as source of truth).
    """
    if not is_registered_user(user_id):
        logger.debug("pg_store.add_message: skipping write for %r (memory only)", user_id)
        return None
    norm_user = normalize_user_id(user_id)
    norm_conv = normalize_conversation_id(conversation_id)
    try:
        pool = await get_pool()
        async with pool.acquire() as conn:
            await conn.execute(
                "INSERT INTO conversations (id, user_id, title, created_at, updated_at, is_active) "
                "VALUES ($1, $2, $3, NOW(), NOW(), true) "
                "ON CONFLICT (id) DO UPDATE SET updated_at = NOW()",
                norm_conv, norm_user, (title or "New chat")[:80],
            )
            await conn.execute(
                "INSERT INTO messages (id, conversation_id, role, content, created_at) "
                "VALUES ($1, $2, $3, $4, NOW())",
                str(uuid.uuid4()), norm_conv, role, content,
            )
            # Auto-title the conversation from the first user message
            if role == "user" and title is None:
                clean = content.strip().replace("\n", " ")[:60] or "New chat"
                await conn.execute(
                    "UPDATE conversations SET title = $1 "
                    "WHERE id = $2 AND title IN ('New chat', 'New Chat')",
                    clean, norm_conv,
                )
        return norm_conv
    except Exception as e:
        logger.warning("pg_store.add_message failed: %s", e)
        return None


async def list_conversations(user_id: str) -> Optional[List[Dict]]:
    """All conversations for a user, newest first (message_count + last_message).

    Returns None when the database is unreachable so callers fall back to the
    JSON memory store.
    """
    norm_user = normalize_user_id(user_id)
    try:
        pool = await get_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT c.id, c.title, c.created_at, c.updated_at,
                       COUNT(m.id)::int AS message_count,
                       (SELECT LEFT(m2.content, 100) FROM messages m2
                         WHERE m2.conversation_id = c.id
                         ORDER BY m2.created_at DESC, m2.id DESC LIMIT 1) AS last_message
                FROM conversations c
                LEFT JOIN messages m ON m.conversation_id = c.id
                WHERE c.user_id = $1 AND c.is_active = true
                GROUP BY c.id
                ORDER BY c.updated_at DESC
                """,
                norm_user,
            )
            return [
                {
                    "id": str(r["id"]),
                    "user_id": norm_user,
                    "title": r["title"],
                    "created_at": r["created_at"].isoformat() if r["created_at"] else None,
                    "updated_at": r["updated_at"].isoformat() if r["updated_at"] else None,
                    "message_count": r["message_count"],
                    "last_message": r["last_message"] or "",
                }
                for r in rows
            ]
    except Exception as e:
        logger.warning("pg_store.list_conversations failed: %s", e)
        return None


async def get_conversation(user_id: str, conversation_id: str) -> Optional[Dict]:
    """One conversation summary (owner-checked). None when missing/not owner/DB down."""
    norm_user = normalize_user_id(user_id)
    norm_conv = normalize_conversation_id(conversation_id)
    try:
        pool = await get_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT c.id, c.title, c.created_at, c.updated_at,
                       (SELECT COUNT(*)::int FROM messages m WHERE m.conversation_id = c.id) AS message_count,
                       (SELECT LEFT(m2.content, 100) FROM messages m2
                         WHERE m2.conversation_id = c.id
                         ORDER BY m2.created_at DESC, m2.id DESC LIMIT 1) AS last_message
                FROM conversations c
                WHERE c.id = $1 AND c.user_id = $2 AND c.is_active = true
                """,
                norm_conv, norm_user,
            )
            if row is None:
                return None
            return {
                "id": str(row["id"]),
                "user_id": norm_user,
                "title": row["title"],
                "created_at": row["created_at"].isoformat() if row["created_at"] else None,
                "updated_at": row["updated_at"].isoformat() if row["updated_at"] else None,
                "message_count": row["message_count"],
                "last_message": row["last_message"] or "",
            }
    except Exception as e:
        logger.warning("pg_store.get_conversation failed: %s", e)
        return None


async def get_messages(user_id: str, conversation_id: str) -> Optional[List[Dict]]:
    """Full message history for a conversation (owner-checked, chronological).

    Returns [] when the conversation does not exist or belongs to another user,
    None when the database is unreachable (caller falls back to JSON memory).
    """
    norm_user = normalize_user_id(user_id)
    norm_conv = normalize_conversation_id(conversation_id)
    try:
        pool = await get_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT m.role, m.content, m.created_at
                FROM messages m
                JOIN conversations c ON c.id = m.conversation_id
                WHERE m.conversation_id = $1 AND c.user_id = $2
                ORDER BY m.created_at ASC, m.id ASC
                """,
                norm_conv, norm_user,
            )
            return [
                {
                    "role": r["role"],
                    "content": r["content"],
                    "timestamp": r["created_at"].isoformat() if r["created_at"] else None,
                }
                for r in rows
            ]
    except Exception as e:
        logger.warning("pg_store.get_messages failed: %s", e)
        return None


async def clear_user(user_id: str) -> bool:
    """Delete every conversation + message belonging to a user."""
    norm_user = normalize_user_id(user_id)
    try:
        pool = await get_pool()
        async with pool.acquire() as conn:
            await conn.execute(
                "DELETE FROM messages WHERE conversation_id IN "
                "(SELECT id FROM conversations WHERE user_id = $1)",
                norm_user,
            )
            await conn.execute("DELETE FROM conversations WHERE user_id = $1", norm_user)
        return True
    except Exception as e:
        logger.warning("pg_store.clear_user failed: %s", e)
        return False


async def delete_conversation(user_id: str, conversation_id: str) -> bool:
    """Hard-delete a conversation and its messages (owner-checked)."""
    norm_user = normalize_user_id(user_id)
    norm_conv = normalize_conversation_id(conversation_id)
    try:
        pool = await get_pool()
        async with pool.acquire() as conn:
            # Messages first (the FK may be RESTRICT, and deleting them keeps
            # the history fully gone even if the conversation row persists).
            await conn.execute(
                "DELETE FROM messages WHERE conversation_id = $1 "
                "AND EXISTS (SELECT 1 FROM conversations c WHERE c.id = $1 AND c.user_id = $2)",
                norm_conv, norm_user,
            )
            res = await conn.execute(
                "DELETE FROM conversations WHERE id = $1 AND user_id = $2",
                norm_conv, norm_user,
            )
            # asyncpg status string is "DELETE <count>"
            try:
                count = int(res.split()[1]) if res and res.startswith("DELETE") else 0
            except (IndexError, ValueError):
                count = 0
            return count > 0
    except Exception as e:
        logger.warning("pg_store.delete_conversation failed: %s", e)
        return False
