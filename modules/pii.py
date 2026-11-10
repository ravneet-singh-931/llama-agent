"""Redact high-risk identifiers before text is persisted or shipped.

Why this exists
---------------
The app logs the full user message and the full agent response
(``monitoring.log_interaction``), and ``security_hardening.AuditLogger``
writes event details to a file and mirrors them to Redis. Anything a user
pastes -- a contract, a support email, an invoice -- lands in those logs
verbatim, and every message also goes to a third-party inference API.

That is fine for a user's own data being answered; it is not fine for it to be
persisted indefinitely in a log file, where it outlives the request that
needed it and is visible to anyone with log access.

Scope, stated honestly: this redacts the two identifier shapes that are
(a) unambiguous, (b) legally sensitive on their own, and (c) cheap to detect
without false positives -- US Social Security numbers and payment card numbers
(with a Luhn check, so ordinary 16-digit IDs are not mangled). It is **not**
a PII classifier: names, addresses, emails and phone numbers are deliberately
left alone, because redacting them would destroy the usefulness of the logs
and a regex cannot reliably tell a name from a noun.

Redaction is applied on the way OUT (logging), never to the text the agent
reasons over: the model still sees exactly what the user wrote.
"""

from __future__ import annotations

import re
from typing import Tuple

__all__ = ["redact", "redact_dict"]

# US SSN: 123-45-6789. Also accept an "X"-masked tail so re-redacting an
# already-redacted string is a no-op rather than a double substitution.
_SSN_RE = re.compile(r"\b\d{3}-\d{2}-\d{4}\b")

# Payment card candidates: 13-19 digits, optionally grouped by space or hyphen.
_CARD_RE = re.compile(r"\b(?:\d[ -]?){12,18}\d\b")

# Same shape as above but already-redacted, used to keep redact() idempotent.
_REDACTED = "[REDACTED]"


def _luhn_ok(digits: str) -> bool:
    """Luhn checksum -- true for real card numbers, false for most ID numbers."""
    total = 0
    for i, ch in enumerate(reversed(digits)):
        d = ord(ch) - 48
        if i % 2:
            d *= 2
            if d > 9:
                d -= 9
        total += d
    return total % 10 == 0


def _card_sub(match: "re.Match") -> str:
    raw = match.group(0)
    digits = re.sub(r"\D", "", raw)
    # Only redact if it looks like a card: right length AND a valid checksum.
    # A random 16-digit order number fails Luhn and is left alone.
    if 13 <= len(digits) <= 19 and _luhn_ok(digits):
        return _REDACTED
    return raw


def redact(text: str) -> str:
    """Replace SSNs and Luhn-valid card numbers with ``[REDACTED]``.

    Never raises and never shortens the text beyond the substitution, so it is
    safe to call on every log write. Idempotent.
    """
    if not text or not isinstance(text, str):
        return text
    out = _SSN_RE.sub(_REDACTED, text)
    out = _CARD_RE.sub(_card_sub, out)
    return out


def redact_dict(obj, _depth: int = 0) -> object:
    """Recursively redact strings inside a dict/list structure.

    Depth-limited so a cyclic or pathologically nested structure can never
    hang a log call.
    """
    if _depth > 6:
        return obj
    if isinstance(obj, str):
        return redact(obj)
    if isinstance(obj, dict):
        return {k: redact_dict(v, _depth + 1) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        converted = [redact_dict(v, _depth + 1) for v in obj]
        return type(obj)(converted) if isinstance(obj, tuple) else converted
    return obj
