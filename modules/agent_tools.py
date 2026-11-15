"""
Real tools for the multi-agent team.

Implements the tools the Agent Team UI advertises (web_search,
read_document) as actual callable functions wired through Groq function
calling:

  - web_search(query)          -> real web search (see modules/web_tools.py)
  - list_uploaded_documents()  -> files the user has uploaded (any document)
  - read_uploaded_document(n)  -> full text of one uploaded document

Document tools operate ONLY on documents registered in the app's RAG store
(which the user uploaded) - they never open arbitrary filesystem paths, so
path traversal is impossible. The store is bound at startup via
set_document_store() (the API binds app.state.rag; other entry points get a
fresh empty store and the tools degrade gracefully).

dispatch_tool() executes a tool by name; run_tool_loop() drives the Groq
function-calling loop (tool calls -> results -> final answer) with a hard
iteration cap so a misbehaving model can never loop forever.
"""

import json
import logging
import os
import re
import time
from typing import Dict, List, Optional, Tuple

from modules.silent import degrade
from modules import recording
from modules.web_tools import web_search, WEB_SEARCH_TOOL

logger = logging.getLogger(__name__)

_MAX_TOOL_ROUNDS = 4
_MAX_READ_CHARS = 6000
# Hard cap on any single tool result fed back to the model (token budget).
_TOOL_RESULT_CHARS = int(os.getenv("AGENT_TOOL_RESULT_CHARS", "4000") or 4000)

# How many times the same (tool, args) pair may be called in one loop before
# we stop executing it. The round cap alone bounds the damage but still pays
# for the wasted call; this catches the "model keeps asking the same thing"
# failure mode at the second attempt.
_MAX_IDENTICAL_CALLS = int(os.getenv("AGENT_MAX_IDENTICAL_CALLS", "2") or 2)

# Total input tokens one tool loop may spend across all its rounds.
# _MAX_TOOL_ROUNDS bounds the number of rounds and _MAX_IDENTICAL_CALLS bounds
# repeats, but neither bounds SIZE: each round resends the whole conversation
# plus the tool results, so four rounds of a long web search is 4x the tokens
# of one and the caps never fire. This is the missing ceiling.
_MAX_TOOL_TOKENS = int(os.getenv("AGENT_MAX_TOOL_TOKENS", "48000") or 48000)

# The RAG store the document tools read from (bound at app startup).
_document_store = None


def set_document_store(store) -> None:
    """Bind the RAG instance that owns the uploaded documents."""
    global _document_store
    _document_store = store


def get_document_store():
    """The raw (unscoped) bound store, or None."""
    return _document_store


def _current_store():
    """The document store, scoped to the current request's owner.

    API handlers bind the caller via modules.request_context.bind_owner, so
    the agent can only list/read documents that THIS user uploaded.
    """
    from modules.doc_scope import scoped
    from modules.request_context import get_owner

    if _document_store is not None:
        return scoped(_document_store, get_owner())
    # No store bound (e.g. in tests): use an empty one.
    from modules.rag import SimpleRAG
    return SimpleRAG()


def list_uploaded_documents() -> str:
    """List the documents the user has uploaded."""
    docs = _current_store().list_documents()
    if not docs:
        return "No documents have been uploaded yet."
    return "Uploaded documents:\n" + "\n".join(f"- {d}" for d in docs)


def read_uploaded_document(filename: str, start_char: int = 0) -> str:
    """Read one uploaded document (by file name), one window at a time.

    A long document is returned in ``_MAX_READ_CHARS`` windows rather than
    being silently amputated: the header reports the total size and the
    offset to ask for next, so the model can page through a 40-page PDF
    instead of answering from its first three pages and never knowing there
    was more.
    """
    store = _current_store()
    filename = (filename or "").strip()

    available = store.list_documents()
    # Match by exact path, by basename, or by basename without extension.
    target = None
    for path in available:
        base = os.path.basename(path)
        if filename == path or filename == base or filename == os.path.splitext(base)[0]:
            target = path
            break

    if target is None:
        hint = "\n".join(f"- {d}" for d in available) if available else "none yet"
        return f"Document '{filename}' not found. Available documents:\n{hint}"

    # Rebuild the text from the store's chunks (same content RAG answers from).
    # get_document_chunks is part of the common store interface (SimpleRAG,
    # VectorStore, KeywordStore, OptimizedRAG) - no reliance on `.documents`.
    chunks = list(store.get_document_chunks(target) or [])
    chunks.sort(key=lambda d: d.get("chunk_index", 0))
    text = "\n".join(d.get("content", "") for d in chunks)
    if not text:
        return f"Document '{target}' is empty."
    total = len(text)
    if total > _MAX_READ_CHARS:
        start = max(0, min(int(start_char or 0), total - 1))
        end = min(start + _MAX_READ_CHARS, total)
        header = (f"[Document '{target}': {total} characters total. "
                  f"Showing {start}-{end}.")
        if end < total:
            header += (f" Call again with start_char={end} to read the next "
                       f"{_MAX_READ_CHARS} characters.]")
        else:
            header += " This is the end of the document.]"
        return header + "\n\n" + text[start:end]
    return text


# ---------------------------------------------------------------------------
# Groq function-calling schemas + dispatch
# ---------------------------------------------------------------------------

DOCUMENT_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "list_uploaded_documents",
            "description": (
                "List the files the user has uploaded. Call this BEFORE "
                "reading a document so you know the file names."
            ),
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_uploaded_document",
            "description": (
                "Read the text of an uploaded document by file name. "
                "Use the exact file name from list_uploaded_documents. "
                "Long documents are returned in windows: the reply states the "
                "total length and tells you which start_char to pass next. "
                "Do NOT use this for general knowledge - only for the user's "
                "own uploaded files."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "filename": {
                        "type": "string",
                        "description": "File name of the uploaded document.",
                    },
                    "start_char": {
                        "type": "integer",
                        "description": (
                            "Character offset to start from (default 0). Use "
                            "the value the previous reply told you to pass to "
                            "continue reading a long document."
                        ),
                    },
                },
                "required": ["filename"],
            },
        },
    },
]

# Tools every agent on the team can use.
AGENT_TOOL_SCHEMAS: List[Dict] = [WEB_SEARCH_TOOL, *DOCUMENT_TOOLS]


# Prefixes _dispatch uses for a tool that did not run. Kept next to the
# audit call so the two cannot drift.
#
# "No documents have been uploaded yet" is deliberately NOT here: the tool ran
# correctly and the user simply has no uploads. Recording that as a failure
# would make a healthy tenant look broken in the audit trail.
_ERROR_PREFIXES = ("Unknown tool:", "Tool '", "Document '")


def _audit_user() -> str:
    """Best-effort owner id for the audit trail.

    The tool dispatcher has no request context of its own; api.py binds one
    via modules.request_context for the duration of a request. Falls back to
    "unknown" rather than inventing an identity.
    """
    try:
        from modules.request_context import get_owner
        return get_owner() or "unknown"
    except Exception:
        return "unknown"


def dispatch_tool(name: str, arguments: dict) -> str:
    """Execute a tool by name (result capped to AGENT_TOOL_RESULT_CHARS).

    Times the call for the per-tool latency histogram. The histogram lives
    here rather than in _dispatch so a truncated-but-successful call is still
    measured.
    """
    t0 = time.monotonic()
    ok, detail = True, ""
    try:
        # An eval replay serves the tool's recorded result instead of running
        # it. Calling the real tool would go to the network, spend quota and
        # return a different answer - which is not a replay. See
        # modules/recording.py.
        result = _cap(recording.tool_call(
            name, arguments, lambda: _dispatch(name, arguments)))
        # _dispatch never raises for tools: failures come back as strings
        # ("Tool 'x' failed: ...", "Unknown tool: x"). An audit line that
        # reported SUCCESS for an unknown tool would be worse than none.
        if result.startswith(_ERROR_PREFIXES):
            ok = False
            detail = f"error={result.split('.')[0][:60]}"
        return result
    except Exception as e:
        ok, detail = False, f"error={type(e).__name__}"
        raise
    finally:
        elapsed = time.monotonic() - t0
        try:
            from modules.prometheus_metrics import record_tool_seconds, tool_span
            record_tool_seconds(name, elapsed)
            # A span as well as a histogram: the histogram answers "how slow
            # is web_search on average", the span answers "what did THIS run
            # do, and which call was the slow one".
            with tool_span(name, elapsed, ok=ok, detail=detail or ""):
                pass
        except Exception as _e:  # nosec B110 - telemetry must never fail a tool call
            degrade("tool latency span not recorded (trace shows the call, no duration)", _e)
        # Audit trail. Tool calls are the only outbound surface (web_search
        # leaves the process, the document tools read user files) and were the
        # one security-relevant event with no record. Logged AFTER the call so
        # a failure is recorded as a failure rather than a request.
        try:
            from modules.security_hardening import AuditLogger
            AuditLogger().log_tool_call(
                tool=name, user_id=_audit_user(), success=ok,
                duration_ms=elapsed * 1000, detail=detail)
        except Exception as _e:  # nosec B110 - auditing must never fail a tool call
            degrade("tool call not written to the audit log", _e)


def _cap(result: str) -> str:
    """Cap one tool result at _TOOL_RESULT_CHARS, saying so in the text."""
    if len(result) > _TOOL_RESULT_CHARS:
        return (result[:_TOOL_RESULT_CHARS]
                + f"\n[... tool output truncated at {_TOOL_RESULT_CHARS} characters ...]")
    return result


_KNOWN_TOOLS = ("web_search", "list_uploaded_documents", "read_uploaded_document")


def _dispatch(name: str, arguments: dict) -> str:
    """Execute a tool by name. Never raises for unknown tools.

    Error strings are the model's ONLY feedback channel, so they say what it
    can do next instead of just what went wrong. The document tool already
    did this ("Document 'x' not found. Available documents: ..."); this
    applies the same rule to the dispatch layer.
    """
    try:
        if name == "web_search":
            return web_search(str(arguments.get("query", "")),
                              topic=str(arguments.get("topic") or ""),
                              time_range=str(arguments.get("time_range") or ""))
        if name == "list_uploaded_documents":
            return list_uploaded_documents()
        if name == "read_uploaded_document":
            return read_uploaded_document(
                str(arguments.get("filename", "")),
                start_char=int(arguments.get("start_char") or 0),
            )
    except Exception as e:
        return (f"Tool '{name}' failed: {e}. "
                f"If this was a network or rate-limit error, retry with a "
                f"different query or use another tool.")
    return (f"Unknown tool: {name}. Valid tools are: "
            f"{', '.join(_KNOWN_TOOLS)}.")


# ---------------------------------------------------------------------------
# Transcript-style tool calls
# ---------------------------------------------------------------------------
# Some models (e.g. llama-3.3-70b-versatile) write tool calls as plain-text
# markers inside the assistant content instead of using Groq's native
# tool_calls field. Several shapes are parsed and executed as real tool calls:
#   <function\web_search={"query": "..."}</function>
#   <function=web_search={"query": "..."}</function>
#   <function=web_search {"query": "..."} </function>
#   <web_search>{"query": "..."}</web_search>

_FUNC_MARKER_RE = re.compile(
    r"<function[\\=](?P<name>\w+)\s*[=:>\s]?\s*(?P<args>\{.*?\})\s*</function>",
    re.DOTALL,
)
_LEGACY_MARKER_RE = re.compile(
    r"<(?P<name>\w+)>\s*(?P<args>\{.*?\})\s*</(?P=name)>",
    re.DOTALL,
)


def _is_tool_use_failed(e: Exception) -> bool:
    """True when Groq's backend failed to parse a model tool-call marker
    (HTTP 400, code 'tool_use_failed')."""
    body = getattr(e, "body", None)
    if isinstance(body, dict):
        err = body.get("error") or {}
        return err.get("code") == "tool_use_failed"
    text = str(body or e)
    return "tool_use_failed" in text


def is_tool_call_error(e: Exception) -> bool:
    """True when Groq rejected the reply because the model emitted a tool call
    it was not allowed to make: 'Tool choice is none, but model called a tool'
    (no tools offered, e.g. the wrap-up after the tool-round cap) or
    'tool_use_failed' (unparsable tool-call marker)."""
    if _is_tool_use_failed(e):
        return True
    low = f"{getattr(e, 'body', '') or ''} {e}".lower()
    return "tool choice is none" in low or "model called a tool" in low


WRAP_UP_INSTRUCTION = (
    "You have used all your tool calls. Write your final answer now using the "
    "tool results above: include the actual facts, names, numbers, and details "
    "from the results. Do not say what you would do or that you will search - "
    "just deliver the answer."
)


def plain_wrap_up_messages(messages: List[dict], max_result_chars: int = 4000) -> List[dict]:
    """The conversation without its tool-call turns: the system prompt and the
    first user message, with the tool results pasted in as plain text, then the
    wrap-up instruction. With no tool calls in the history the model has
    nothing to continue, so it answers instead of trying another tool call."""
    system = [m for m in messages if m.get("role") == "system"][:1]
    task = next((str(m.get("content") or "") for m in messages if m.get("role") == "user"), "")
    results = [str(m.get("content") or "")[:max_result_chars]
               for m in messages if m.get("role") == "tool"]
    body = task
    if results:
        body += "\n\nResults from your tools:\n\n" + "\n\n---\n\n".join(results)
    body += "\n\n" + WRAP_UP_INSTRUCTION.replace("tool results above", "tool results")
    return system + [{"role": "user", "content": body}]


def _parse_transcript_tool_calls(content: str) -> List[Tuple[str, dict]]:
    """Extract transcript-style tool calls from assistant text.

    Returns a list of (tool_name, arguments) for every marker found.
    Malformed markers are ignored; never raises.
    """
    if not content:
        return []
    calls: List[Tuple[str, dict]] = []
    for m in _FUNC_MARKER_RE.finditer(content):
        try:
            args = json.loads(m.group("args"))
        except Exception as _e:  # nosec B112
            degrade("one function-call block had invalid JSON args (skipped)", _e)
            continue
        if isinstance(args, dict):
            calls.append((m.group("name"), args))
    if calls:
        return calls
    for m in _LEGACY_MARKER_RE.finditer(content):
        try:
            args = json.loads(m.group("args"))
        except Exception as _e:  # nosec B112
            degrade("one legacy function-call block had invalid JSON args (skipped)", _e)
            continue
        if isinstance(args, dict):
            calls.append((m.group("name"), args))
    return calls


# ---------------------------------------------------------------------------
# Narration-frame responses
# ---------------------------------------------------------------------------
# Some models answer with a sentence about what they WILL do ("I'll search
# for the latest news...") instead of actually answering. Those frames are
# not answers: run_tool_rounds() pushes the model to use the tools instead
# of returning the frame as the final response.

_FRAME_RESPONSE_RE = re.compile(
    r"^\s*(?:"
    r"i'?ll\s+(?:search|look|look\s+up|find|check|research|investigate|"
    r"get|gather|fetch|browse|see)\b"
    r"|i\s+will\s+(?:search|look|look\s+up|find|check|research|"
    r"investigate|get|gather)\b"
    r"|let'?s\s+(?:search|look|find|check|see)\b"
    r"|let\s+me\s+(?:search|look|look\s+up|find|check|research|see)\b"
    r"|searching\s+for\b|looking\s+(?:for|up)\b|checking\s+for\b"
    r"|i\s+need\s+to\s+(?:search|look|find|check)\b"
    r"|now\s+(?:searching|looking|checking)\b"
    r"|starting\s+(?:a\s+)?(?:search|research)\b"
    r").*$",
    re.IGNORECASE | re.DOTALL,
)

_MAX_FRAME_CHARS = 200


def is_frame_response(content: str) -> bool:
    """True when the model only narrated what it WILL do (no answer yet).

    Narration frames like "I'll search for the latest news..." are not
    answers. Longer text is treated as a real answer even if it opens with
    a narration phrase, so legitimate detailed answers are never discarded.
    """
    text = (content or "").strip()
    if not text or len(text) > _MAX_FRAME_CHARS:
        return False
    return _FRAME_RESPONSE_RE.match(text) is not None


def run_tool_rounds(client, model: str, messages: List[dict],
                    tools: List[Dict] = None,
                    max_rounds: int = _MAX_TOOL_ROUNDS,
                    max_tokens: int = 2048) -> Tuple[List[dict], List[str], Optional[str]]:
    """Drive tool-calling rounds until the model answers or the cap is hit.

    Returns (messages, events, final_content):
      - messages: the conversation with assistant tool calls + tool results
        appended (a final answer, if produced, is the last assistant message)
      - events: human-readable tool activity strings for streaming status
      - final_content: the model's final answer text, or None when the round
        cap was reached without an answer (callers then force a wrap-up).
    """
    if not tools:
        return messages, [], None
    events: List[str] = []
    seen: Dict[tuple, int] = {}
    spent_tokens = 0

    def _over_budget() -> Optional[str]:
        """Message to hand the model when the token ceiling is reached.

        Returned as content rather than raised: the loop should still produce
        an answer from what it already has. An exception here would turn a
        budget stop into a failed chat.
        """
        return (f"I have gathered enough information and used my research "
                f"budget ({spent_tokens:,} tokens). Answer now using only the "
                f"results already collected, and say what is uncertain.")

    def _guarded_dispatch(name: str, args: dict) -> str:
        """Dispatch, but refuse to re-run the same (tool, args) repeatedly.

        _MAX_TOOL_ROUNDS bounds the total damage; this bounds the waste. A
        model stuck on "search the same query again" would otherwise pay for
        that identical call every round. Keyed on the arguments so a genuine
        second search with a NEW query is unaffected.
        """
        try:
            key = (name, json.dumps(args, sort_keys=True, default=str))
        except Exception:  # unserialisable args - just run it
            return dispatch_tool(name, args)
        seen[key] = seen.get(key, 0) + 1
        # The default of 2 permits ONE retry: a transient error is worth
        # repeating, an unchanged success is not. The 3rd call is refused.
        if seen[key] > _MAX_IDENTICAL_CALLS:
            return (f"You have already called {name} with exactly these "
                    f"arguments {seen[key] - 1} times and the result will not "
                    f"change. Use a DIFFERENT query, a different tool, or "
                    f"answer with what you already have.")
        return dispatch_tool(name, args)

    for _ in range(max_rounds):
        # Count what this round will actually send, then stop before paying
        # for it. Estimated with the same helper llm_guard uses so the two
        # cannot disagree about what a token is.
        try:
            from modules.llm_guard import estimate_tokens
            round_tokens = estimate_tokens(messages, tools)
        except Exception:
            round_tokens = 0
        if round_tokens and spent_tokens + round_tokens > _MAX_TOOL_TOKENS:
            events.append("🛑 tool budget reached")
            messages.append({"role": "user", "content": _over_budget()})
            return messages, events, None
        spent_tokens += round_tokens
        try:
            response = client.chat.completions.create(
                model=model,
                messages=messages,
                tools=tools,
                tool_choice="auto",
                max_tokens=max_tokens,
            )
        except Exception as e:
            # Groq's backend sometimes fails to parse llama's <function=...>
            # markers (HTTP 400 'tool_use_failed') even though the marker is
            # perfectly valid for our own parser. Retry the round WITHOUT
            # tools so the marker comes back as plain content, which we then
            # parse and execute ourselves. Any other error keeps failing.
            if not _is_tool_use_failed(e):
                raise
            response = client.chat.completions.create(
                model=model,
                messages=messages,
                max_tokens=max_tokens,
            )
        choice = response.choices[0]
        message = choice.message
        if not getattr(message, "tool_calls", None):
            # Some models (e.g. llama-3.3-70b) write tool calls as plain-text
            # transcript markers inside content instead of Groq's native
            # tool_calls field. Parse those markers and execute them as real
            # tool calls, then let the model answer with the results.
            transcript_calls = _parse_transcript_tool_calls(message.content or "")
            if transcript_calls:
                messages.append({
                    "role": "assistant",
                    "content": message.content,
                    "tool_calls": [
                        {
                            "id": f"call_t{i}",
                            "type": "function",
                            "function": {
                                "name": name,
                                "arguments": json.dumps(args),
                            },
                        }
                        for i, (name, args) in enumerate(transcript_calls)
                    ],
                })
                for i, (name, args) in enumerate(transcript_calls):
                    result = _guarded_dispatch(name, args)
                    events.append(f"⚙️ **{name}**")
                    messages.append({
                        "role": "tool",
                        "tool_call_id": f"call_t{i}",
                        "content": f"<<UNTRUSTED_DATA>>\n{result}\n<</UNTRUSTED_DATA>>",
                    })
                continue
            # Narration-only frame ("I'll search for the latest news...")
            # with no actual tool call: not an answer. Push the model to
            # use the tools and give it another chance to answer.
            if is_frame_response(message.content or ""):
                messages.append({"role": "assistant", "content": message.content})
                messages.append({
                    "role": "user",
                    "content": (
                        "Do not just say what you will do. Use the available "
                        "tools to get the actual information, then give your "
                        "full answer with the details."
                    ),
                })
                continue
            # Model answered without calling a tool.
            if message.content:
                messages.append({"role": "assistant", "content": message.content})
                return messages, events, message.content
            # Empty response (some small models do this) — keep going
            # until the round cap, then callers force a wrap-up answer.
            continue

        messages.append({
            "role": "assistant",
            "content": message.content,
            "tool_calls": [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {
                        "name": tc.function.name,
                        "arguments": tc.function.arguments,
                    },
                }
                for tc in message.tool_calls
            ],
        })

        for tc in message.tool_calls:
            try:
                args = json.loads(tc.function.arguments or "{}")
            except Exception:
                args = {}
            result = _guarded_dispatch(tc.function.name, args)
            events.append(f"⚙️ **{tc.function.name}**")
            messages.append({
                "role": "tool",
                "tool_call_id": tc.id,
                "content": f"<<UNTRUSTED_DATA>>\n{result}\n<</UNTRUSTED_DATA>>",
            })

    # Iteration cap reached without a final answer.
    return messages, events, None


def run_tool_loop(client, model: str, messages: List[dict],
                  tools: List[Dict] = None,
                  max_rounds: int = _MAX_TOOL_ROUNDS,
                  max_tokens: int = 2048) -> Tuple[List[dict], List[str]]:
    """Drive the Groq function-calling loop.

    Executes tool calls (appending tool results to the conversation) until
    the model answers or the iteration cap is hit. Returns
    (final_messages, events) where events are human-readable tool activity
    strings for streaming status.
    """
    messages, events, final_content = run_tool_rounds(
        client, model, messages, tools, max_rounds=max_rounds, max_tokens=max_tokens
    )
    if final_content is None:
        # Iteration cap reached without a final answer - tell the model to wrap up.
        messages.append({"role": "user", "content": WRAP_UP_INSTRUCTION})
        try:
            response = client.chat.completions.create(
                model=model, messages=messages, max_tokens=max_tokens
            )
        except Exception as e:
            # The model tried yet another tool call with no tools offered:
            # ask again without the tool-call turns in the history.
            if not is_tool_call_error(e):
                raise
            logger.warning("wrap-up rejected (%s); retrying without tool history", str(e)[:120])
            response = client.chat.completions.create(
                model=model, messages=plain_wrap_up_messages(messages), max_tokens=max_tokens
            )
        if response.choices[0].message.content:
            messages.append({"role": "assistant", "content": response.choices[0].message.content})
    return messages, events
