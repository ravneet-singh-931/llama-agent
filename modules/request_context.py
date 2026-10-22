"""
Per-request identity propagated through contextvars.

API handlers call :func:`bind_owner` with the caller's stable id (a user
UUID, or ``guest-<cookie-id>`` for anonymous visitors). Code deep in the
call stack - agent tools, the multi-agent answer cache, the ReAct agent's
document tools - reads :func:`get_owner` so documents, caches and memory
are always scoped to the right tenant without threading ``user_id``
through every signature.

``asyncio.to_thread`` copies the context automatically; for raw thread
pools use :func:`submit_with_context`.
"""

from __future__ import annotations

import contextvars
from typing import Any, Callable, Optional

_current_owner: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar(
    "current_owner", default=None
)


def bind_owner(owner: Optional[str]) -> contextvars.Token:
    """Bind ``owner`` for the rest of the current context."""
    return _current_owner.set(str(owner) if owner else None)


def reset_owner(token: contextvars.Token) -> None:
    _current_owner.reset(token)


def get_owner() -> Optional[str]:
    return _current_owner.get()


def submit_with_context(pool, fn: Callable, *args: Any, **kwargs: Any):
    """``pool.submit`` that carries the caller's contextvars into the worker."""
    ctx = contextvars.copy_context()
    return pool.submit(ctx.run, fn, *args, **kwargs)
