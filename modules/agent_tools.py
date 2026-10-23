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
import os
import re
from typing import Dict, List, Optional, Tuple

from modules.secrets import get_secret
from modules.web_tools import web_search, WEB_SEARCH_TOOL

_MAX_TOOL_ROUNDS = 4
_MAX_READ_CHARS = 6000
# Hard cap on any single tool result fed back to the model (token budget).
_TOOL_RESULT_CHARS = int(os.getenv("AGENT_TOOL_RESULT_CHARS", "4000") or 4000)

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
    # No store bound (e.g. tests / streamlit): use an empty one.
    from modules.rag import SimpleRAG
    return SimpleRAG()


def list_uploaded_documents() -> str:
    """List the documents the user has uploaded."""
    docs = _current_store().list_documents()
    if not docs:
        return "No documents have been uploaded yet."
    return "Uploaded documents:\n" + "\n".join(f"- {d}" for d in docs)


def read_uploaded_document(filename: str) -> str:
    """Read the full text of one uploaded document (by its file name)."""
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
    if len(text) > _MAX_READ_CHARS:
        text = text[:_MAX_READ_CHARS] + "\n\n[...truncated, document is large...]"
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
                "Read the full text of an uploaded document by file name. "
                "Use the exact file name from list_uploaded_documents."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "filename": {
                        "type": "string",
                        "description": "File name of the uploaded document.",
                    }
                },
                "required": ["filename"],
            },
        },
    },
]

# Tools every agent on the team can use.
AGENT_TOOL_SCHEMAS: List[Dict] = [WEB_SEARCH_TOOL, *DOCUMENT_TOOLS]


def dispatch_tool(name: str, arguments: dict) -> str:
    """Execute a tool by name (result capped to AGENT_TOOL_RESULT_CHARS)."""
    result = _dispatch(name, arguments)
    if len(result) > _TOOL_RESULT_CHARS:
        result = (result[:_TOOL_RESULT_CHARS]
                  + f"\n[... tool output truncated at {_TOOL_RESULT_CHARS} characters ...]")
    return result


def _dispatch(name: str, arguments: dict) -> str:
    """Execute a tool by name. Never raises for unknown tools."""
    try:
        if name == "web_search":
            return web_search(str(arguments.get("query", "")))
        if name == "list_uploaded_documents":
            return list_uploaded_documents()
        if name == "read_uploaded_document":
            return read_uploaded_document(str(arguments.get("filename", "")))
    except Exception as e:
        return f"Tool '{name}' failed: {e}"
    return f"Unknown tool: {name}"


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
        except Exception:  # nosec B112
            continue
        if isinstance(args, dict):
            calls.append((m.group("name"), args))
    if calls:
        return calls
    for m in _LEGACY_MARKER_RE.finditer(content):
        try:
            args = json.loads(m.group("args"))
        except Exception:  # nosec B112
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
    for _ in range(max_rounds):
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
                    result = dispatch_tool(name, args)
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
            result = dispatch_tool(tc.function.name, args)
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
        messages.append({
            "role": "user",
            "content": (
                "You have used all your tool calls. Write your final answer "
                "now using the tool results above: include the actual facts, "
                "names, numbers, and details from the results. Do not say "
                "what you would do or that you will search - just deliver "
                "the answer."
            ),
        })
        response = client.chat.completions.create(
            model=model, messages=messages, max_tokens=max_tokens
        )
        if response.choices[0].message.content:
            messages.append({"role": "assistant", "content": response.choices[0].message.content})
    return messages, events
