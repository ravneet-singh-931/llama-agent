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
from typing import Dict, List, Optional, Tuple

from modules.secrets import get_secret
from modules.web_tools import web_search, WEB_SEARCH_TOOL

_MAX_TOOL_ROUNDS = 4
_MAX_READ_CHARS = 6000

# The RAG store the document tools read from (bound at app startup).
_document_store = None


def set_document_store(store) -> None:
    """Bind the RAG instance that owns the uploaded documents."""
    global _document_store
    _document_store = store


def _current_store():
    if _document_store is not None:
        return _document_store
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
    chunks = [d for d in store.documents if d.get("file") == target]
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
        response = client.chat.completions.create(
            model=model,
            messages=messages,
            tools=tools,
            tool_choice="auto",
            max_tokens=max_tokens,
        )
        choice = response.choices[0]
        message = choice.message
        if not getattr(message, "tool_calls", None):
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
                "content": str(result),
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
                "You have used all your tool calls. Give your best answer now "
                "based on what you have."
            ),
        })
        response = client.chat.completions.create(
            model=model, messages=messages, max_tokens=max_tokens
        )
        if response.choices[0].message.content:
            messages.append({"role": "assistant", "content": response.choices[0].message.content})
    return messages, events
