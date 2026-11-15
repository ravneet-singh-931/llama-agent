"""One helper for every `except: pass`, so each one states what it loses.

This codebase has 60 exception handlers whose body is a bare `pass` or
`continue`. Every one of them is a deliberate fallback — telemetry must not fail
a tool call, a dead WebSocket must not stop a broadcast, a corrupt metrics file
must fall through to an empty dict. The behaviour is right.

The problem was that the code did not SAY so. `except Exception: pass` is
indistinguishable from a handler someone wrote to make a red test go green, and
41 of the 60 had no comment at all. A reader cannot tell a considered fallback
from a bug being hidden, and neither can the next editor.

`degrade()` keeps the same control flow and adds three things:

  * the failure is logged at DEBUG, so it is recoverable with
    LOG_LEVEL=DEBUG or by raising the level, without noise in normal runs;
  * the call site has to name, in one argument, what is lost;
  * the argument is a required string, not a comment, so it cannot be dropped
    silently by a formatter or a hurried edit.

Usage:

    try:
        record_llm_call_seconds(...)
    except Exception as e:
        degrade("llm latency metric not recorded for this call", e)

It returns None, so `except Exception: continue` becomes
`except Exception as e: degrade(...); continue`, which keeps the loop behaviour
visible at the call site rather than hiding it in a helper.
"""

import logging

_log = logging.getLogger(__name__)

# Set to True in tests to surface every degraded path instead of logging at DEBUG.
STRICT = False


class DegradedError(RuntimeError):
    """Raised by degrade() when STRICT is on, so tests can assert a fallback fired."""


def degrade(what: str, exc: BaseException | None = None) -> None:
    """Record that a fallback was taken. Never raises unless STRICT is set."""
    if STRICT:
        raise DegradedError(f"degraded: {what}") from exc
    _log.debug("degraded: %s (%s: %s)", what, type(exc).__name__, exc)
