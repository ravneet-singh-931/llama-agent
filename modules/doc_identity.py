"""Who is asking: request identity, guest cookies, per-owner document scoping.

Extracted from `api.py` as step 3 of splitting it by concern (#15, scope A:
helpers only, routes stay put). The functions here answer one question — "on
whose behalf is this request running?" — and eleven route handlers in `api.py`
ask it via `_request_uid(...)`.

Why this is its own module rather than part of `modules/doc_answers.py`: the
answer cache is keyed by owner, and the owner string comes from here. Keeping
identity and answering apart means the cache's key can be read in one place
while the identity rules can be changed in another, and neither has to import
the other.

**Nothing was rewritten.** `_guest_uid` and `_request_uid` are byte-identical to
their previous versions in `api.py`.

## The one subtle thing: the context variable is MOVED, not copied

`_guest_id_ctx` holds the current request's guest id. `identity_middleware` in
`api.py` sets it; `_request_uid` reads it. That is a single value shared across a
module boundary, so unlike the helper functions it cannot be duplicated — two
context variables would mean the middleware sets one and the reader reads the
other, and every anonymous visitor would silently become a brand-new guest on
each request.

So the object itself lives *here*, and `api.py` imports it back:

    from modules.doc_identity import _guest_id_ctx   # the same object

`api.py` keeps its old module-level name (`_guest_id_ctx`, `GUEST_COOKIE`,
`_GUEST_ID_RE`) as re-exports, so nothing else in that file had to change.

`_user_rag` deliberately did **not** move: it reads `app.state.rag`, and `app`
is the FastAPI instance that `api.py` owns. Moving it would have meant the
identity module importing the API module — a cycle. It stays in `api.py` where
its one dependency lives.
"""

import contextvars
import logging
import re
import uuid
from typing import Any, Dict, Optional

from modules.request_context import bind_owner

_log = logging.getLogger(__name__)

# The guest cookie is set HttpOnly by identity_middleware and holds a 32-hex id.
GUEST_COOKIE = "guest_id"
_GUEST_ID_RE = re.compile(r"^[a-f0-9]{32}$")

# Set by identity_middleware for every HTTP request; read by _request_uid.
# This exact object is shared with api.py (imported, never copied) — see module
# docstring.
_guest_id_ctx: "contextvars.ContextVar[Optional[str]]" = contextvars.ContextVar(
    "guest_id", default=None
)


def _guest_uid(guest_id: Optional[str]) -> str:
    gid = guest_id if guest_id and _GUEST_ID_RE.match(guest_id) else uuid.uuid4().hex
    return f"guest-{gid}"


def _request_uid(user: Optional[Dict[str, Any]]) -> str:
    """Stable identity for the current request, bound for downstream code.

    - Registered user -> their UUID.
    - Anonymous       -> "guest-<per-browser id>" from the HttpOnly guest
      cookie, so two anonymous visitors NEVER share history, facts or
      documents (pg_store skips persistence for non-UUID ids).
    """
    uid = str(user["id"]) if user and user.get("id") else _guest_uid(_guest_id_ctx.get())
    bind_owner(uid)
    return uid
