"""The three streaming helpers moved out of api.py in #15 step 4.

Why these tests exist at all: before this file, **none of the three was called
by any test**. The doc suite exercises the document path, the chat tests go
through the HTTP endpoint, and neither reaches `_redacted_stream`,
`_reasoning_sse` or `_mirror_message` directly — measured at 0% coverage of
those lines. So the move could not have been caught by the suite either way.

`_redacted_stream` is the one that matters: it is the **output** half of the PII
boundary. Redaction already happened on the way into logs; without this, a model
that echoed an SSN or a card number out of an uploaded document sent it straight
to the browser. It is also stream-aware, which is the property most likely to be
lost in a refactor: a number split across two tokens must still be redacted
whole.
"""

import asyncio
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

# Luhn-valid test card (Stripe's documented test number).
CARD = "4111 1111 1111 1111"
SSN = "123-45-6789"


@pytest.fixture
def u():
    from modules import api_utils
    return api_utils


# ------------------------------------------------- _redacted_stream --

def test_plain_text_passes_through_unchanged(u):
    out = "".join(u._redacted_stream(["Hello", " there", ", friend."]))
    assert out == "Hello there, friend."


def test_a_card_in_one_chunk_is_redacted(u):
    out = "".join(u._redacted_stream([f"Your card is {CARD} ok"]))
    assert CARD not in out
    assert "[REDACTED]" in out


def test_a_ssn_in_one_chunk_is_redacted(u):
    out = "".join(u._redacted_stream([f"SSN {SSN} noted"]))
    assert SSN not in out
    assert "[REDACTED]" in out


def test_a_card_split_across_chunks_is_still_redacted(u):
    """The reason StreamRedactor exists, and the easiest thing to break.

    Concatenating first and redacting afterwards would pass this trivially;
    this asserts the *streaming* path, so the pieces really are separate calls.
    """
    pieces = ["Your card is 4111", " 1111", " 1111", " 1111 ok"]
    out = "".join(u._redacted_stream(pieces))
    assert "4111" not in out.replace("[REDACTED]", "")
    assert "[REDACTED]" in out


def test_a_card_split_at_an_awkward_point_is_still_redacted(u):
    pieces = ["4111 1111", " 1111 1111"]
    out = "".join(u._redacted_stream(pieces))
    assert CARD not in out
    assert "[REDACTED]" in out


def test_held_back_tail_is_flushed_at_the_end(u):
    """Text held back for safety must not be silently dropped."""
    # A long digit run is held back; if flush were missing it would vanish.
    out = "".join(u._redacted_stream(["x" * 30, "1234"]))
    assert "1234" in out, "held-back digits must be flushed, not discarded"


def test_an_empty_stream_produces_nothing(u):
    assert "".join(u._redacted_stream([])) == ""


def test_empty_and_none_chunks_are_tolerated(u):
    assert "".join(u._redacted_stream(["", "", "hi", ""])) == "hi"


def test_the_context_manager_contract_still_holds(u):
    """It must stay a lazy generator, not become a list-returning function."""
    gen = u._redacted_stream(["a"])
    assert hasattr(gen, "__next__")
    assert next(gen) == "a"


# -------------------------------------------------- _reasoning_sse --

def test_a_tool_event_becomes_structured_sse(u):
    frame = u._reasoning_sse({"type": "tool", "content": "  searching docs  "})
    assert frame.startswith("data: ")
    assert frame.endswith("\n\n")
    payload = json.loads(frame[len("data: "):].strip())
    assert payload == {"type": "reasoning", "stage": "tool", "content": "searching docs"}


def test_a_plain_note_keeps_its_stage(u):
    payload = json.loads(u._reasoning_sse({"stage": "plan", "content": "thinking"})[6:].strip())
    assert payload == {"type": "reasoning", "stage": "plan", "content": "thinking"}


def test_a_note_without_a_stage_defaults_to_note(u):
    payload = json.loads(u._reasoning_sse({"content": "x"})[6:].strip())
    assert payload["stage"] == "note"


def test_a_tool_marker_without_content_does_not_emit_None(u):
    """`str(None)` would render the literal word None in the UI."""
    payload = json.loads(u._reasoning_sse({"type": "tool"})[6:].strip())
    assert payload["content"] == ""


def test_newlines_in_content_cannot_break_the_sse_framing(u):
    """A raw newline would end the framed event early and corrupt the stream.

    SSE terminates an event at a blank line, so the payload must reach the
    client as a single line. `json.dumps` escapes \n, which is what makes this
    safe — if the payload were ever built by string formatting instead, this
    test is the one that would notice.
    """
    frame = u._reasoning_sse({"type": "tool", "content": "line1\nline2"})
    body = frame[len("data: "):-2]
    assert "\n" not in body, f"frame body must be a single line, got {body!r}"
    # ...and the newline must survive *inside* the decoded content, not be lost.
    assert json.loads(body)["content"] == "line1\nline2"


# --------------------------------------------------- _mirror_message --

def test_mirror_writes_postgres_and_invalidates_caches(u, monkeypatch):
    from modules import pg_store, redis_store

    calls = []
    async def add_message(*a):
        calls.append(("pg",) + a)
    async def invalidate_user(x):
        calls.append(("user", x))
    async def invalidate_conversation(x):
        calls.append(("conv", x))
    monkeypatch.setattr(pg_store, "add_message", add_message)
    monkeypatch.setattr(pg_store, "normalize_conversation_id", lambda c: f"norm:{c}")
    monkeypatch.setattr(redis_store, "invalidate_user", invalidate_user)
    monkeypatch.setattr(redis_store, "invalidate_conversation", invalidate_conversation)

    out = asyncio.run(u._mirror_message("u1", "c1", "user", "hello"))
    assert out == "c1"
    assert ("pg", "u1", "c1", "user", "hello") in calls
    assert ("user", "u1") in calls
    assert ("conv", "norm:c1") in calls


def test_mirror_never_raises_when_postgres_is_down(u, monkeypatch):
    """A mirror failure must not cost the user their reply."""
    from modules import pg_store

    async def boom(*a, **k):
        raise RuntimeError("postgres down")
    monkeypatch.setattr(pg_store, "add_message", boom)

    assert asyncio.run(u._mirror_message("u", "c", "user", "x")) == "c"


def test_mirror_logs_through_its_own_module_logger(u, monkeypatch, caplog):
    """`_log` used to be api.py's logger; after the move it must be api_utils'."""
    from modules import pg_store

    async def boom(*a, **k):
        raise RuntimeError("postgres down")
    monkeypatch.setattr(pg_store, "add_message", boom)

    with caplog.at_level("WARNING", logger="modules.api_utils"):
        asyncio.run(u._mirror_message("u", "c", "user", "x"))
    assert any("persist mirror failed" in r.message for r in caplog.records)


# ----------------------------------------------------- the move --

@pytest.mark.parametrize("name", ["_redacted_stream", "_reasoning_sse", "_mirror_message"])
def test_api_reexports_are_the_same_objects(u, name):
    """One definition, two names. A copy would drift silently."""
    import api
    assert getattr(api, name) is getattr(u, name)


def test_api_utils_does_not_import_api():
    import ast
    import pathlib

    src = pathlib.Path(
        os.path.join(os.path.dirname(__file__), "..", "modules", "api_utils.py")
    ).read_text()
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, ast.Import):
            assert not any(a.name == "api" for a in node.names)
        if isinstance(node, ast.ImportFrom):
            assert node.module != "api"
