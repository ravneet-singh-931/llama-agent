"""
Tests for Tier-3 helpers: agent tool dispatch, document tools, the tool
round loop, multi-document comparison detection, and per-document retrieval.

All tests are offline (no Groq API calls, no network).
Run with: pytest tests/test_agent_tools.py -v
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from modules.agent_tools import (
    set_document_store,
    list_uploaded_documents,
    read_uploaded_document,
    dispatch_tool,
    run_tool_rounds,
    run_tool_loop,
    AGENT_TOOL_SCHEMAS,
)
from modules.rag import SimpleRAG


# ============ HELPERS ============

def _store_with_docs():
    rag = SimpleRAG()
    rag.add_text(
        "This CRM document describes the 2026 pricing: the Pro plan costs "
        "49 dollars per month and the Enterprise plan is custom priced.",
        source="uploads/CRM-3.docx",
    )
    rag.add_text(
        "sample.txt contains example notes. Pricing is mentioned once: the "
        "starter tier is free and the team tier is 19 dollars per month.",
        source="uploads/sample.txt",
    )
    return rag


class _FakeFunction:
    def __init__(self, name, arguments):
        self.name = name
        self.arguments = arguments


class _FakeToolCall:
    def __init__(self, name, arguments, tid="call_1"):
        self.id = tid
        self.function = _FakeFunction(name, arguments)


class _FakeMessage:
    def __init__(self, content, tool_calls=None):
        self.content = content
        self.tool_calls = tool_calls


class _FakeChoice:
    def __init__(self, message):
        self.message = message


class _FakeResponse:
    def __init__(self, choices):
        self.choices = choices


class _FakeCompletions:
    """Scripted chat completions: each create() plays the next script entry;
    exhausted scripts fall back to a plain text answer."""

    def __init__(self, script):
        self._script = list(script)
        self._calls = 0

    def create(self, **kwargs):
        if self._calls < len(self._script):
            content, tool_calls = self._script[self._calls]
        else:
            content, tool_calls = "fallback final answer", None
        self._calls += 1
        return _FakeResponse([_FakeChoice(_FakeMessage(content, tool_calls))])


class _FakeChat:
    def __init__(self, script):
        self.completions = _FakeCompletions(script)


class _FakeClient:
    def __init__(self, script):
        self.chat = _FakeChat(script)


# ============ TOOL SCHEMAS ============

def test_agent_tool_schemas_include_search_and_docs():
    names = {t["function"]["name"] for t in AGENT_TOOL_SCHEMAS}
    assert "web_search" in names
    assert "list_uploaded_documents" in names
    assert "read_uploaded_document" in names


# ============ DISPATCH ============

def test_dispatch_unknown_tool():
    assert dispatch_tool("teleport", {"query": "x"}) == "Unknown tool: teleport"


def test_dispatch_web_search_empty_query():
    # No network: an empty query returns a message without any provider call.
    assert dispatch_tool("web_search", {}) == "Please provide a search query."


# ============ DOCUMENT TOOLS ============

def test_list_documents_with_store():
    set_document_store(_store_with_docs())
    out = list_uploaded_documents()
    assert "CRM-3.docx" in out and "sample.txt" in out


def test_list_documents_empty_store():
    set_document_store(SimpleRAG())
    assert list_uploaded_documents() == "No documents have been uploaded yet."


def test_read_document_by_basename():
    set_document_store(_store_with_docs())
    out = read_uploaded_document("CRM-3.docx")
    assert "pricing" in out.lower()


def test_read_document_by_full_path():
    set_document_store(_store_with_docs())
    out = read_uploaded_document("uploads/sample.txt")
    assert "pricing" in out.lower()


def test_read_document_by_stem_without_extension():
    set_document_store(_store_with_docs())
    out = read_uploaded_document("CRM-3")
    assert "pricing" in out.lower()


def test_read_document_not_found_lists_available():
    set_document_store(_store_with_docs())
    out = read_uploaded_document("missing.txt")
    assert "not found" in out
    assert "CRM-3.docx" in out  # hints the available documents


def test_read_document_truncates_large_files():
    rag = SimpleRAG()
    rag.add_text(("lorem ipsum dolor sit amet " * 400), source="uploads/big.txt")
    set_document_store(rag)
    out = read_uploaded_document("big.txt")
    assert "[...truncated" in out


# ============ TOOL ROUND LOOP ============

def test_tool_rounds_direct_answer():
    client = _FakeClient([("final answer text", None)])
    msgs = [{"role": "user", "content": "hi"}]
    out_msgs, events, final = run_tool_rounds(client, "m", msgs, tools=AGENT_TOOL_SCHEMAS)
    assert final == "final answer text"
    assert events == []
    assert out_msgs[-1]["content"] == "final answer text"


def test_tool_rounds_executes_tool_call():
    set_document_store(_store_with_docs())
    client = _FakeClient([
        (None, [_FakeToolCall("list_uploaded_documents", "{}")]),
        ("final answer text", None),
    ])
    msgs = [{"role": "user", "content": "read my docs"}]
    out_msgs, events, final = run_tool_rounds(client, "m", msgs, tools=AGENT_TOOL_SCHEMAS)
    assert final == "final answer text"
    assert events == ["⚙️ **list_uploaded_documents**"]
    # Tool result is appended with the tool role before the answer.
    assert out_msgs[-2]["role"] == "tool"


def test_tool_rounds_caps_infinite_loops():
    set_document_store(SimpleRAG())
    # Model always calls a tool -> the round cap must stop it.
    client = _FakeClient(
        [(None, [_FakeToolCall("list_uploaded_documents", "{}")]) for _ in range(10)]
    )
    msgs = [{"role": "user", "content": "do something"}]
    out_msgs, events, final = run_tool_rounds(
        client, "m", msgs, tools=AGENT_TOOL_SCHEMAS, max_rounds=4
    )
    assert final is None
    assert len(events) == 4


def test_tool_loop_wraps_up_after_cap():
    set_document_store(SimpleRAG())
    client = _FakeClient(
        [(None, [_FakeToolCall("list_uploaded_documents", "{}")]) for _ in range(2)]
    )
    msgs = [{"role": "user", "content": "do something"}]
    out_msgs, events = run_tool_loop(
        client, "m", msgs, tools=AGENT_TOOL_SCHEMAS, max_rounds=2
    )
    assert len(events) == 2
    assert out_msgs[-1]["role"] == "assistant"
    assert out_msgs[-1]["content"] == "fallback final answer"


# ============ MULTI-DOC COMPARISON DETECTION ============

def test_detect_compare_two_docs():
    from api import _detect_compare_documents
    rag = _store_with_docs()
    is_cmp, files = _detect_compare_documents(
        "Compare what the CRM doc and sample.txt say about pricing", rag
    )
    assert is_cmp
    assert len(set(files)) == 2
    assert any("CRM-3.docx" in f for f in files)
    assert any("sample.txt" in f for f in files)


def test_detect_compare_greeting():
    from api import _detect_compare_documents
    rag = _store_with_docs()
    assert _detect_compare_documents("hello there", rag) == (False, [])


def test_detect_compare_requires_two_references():
    from api import _detect_compare_documents
    rag = _store_with_docs()
    # Only one file is actually referenced -> not a comparison.
    is_cmp, files = _detect_compare_documents("compare the pricing in the CRM file", rag)
    assert not is_cmp
    assert files == []


def test_detect_compare_single_doc_store():
    from api import _detect_compare_documents
    rag = SimpleRAG()
    rag.add_text("only one doc here", source="uploads/only.txt")
    assert _detect_compare_documents("compare x and y", rag) == (False, [])


def test_compare_question_routes_as_document_question():
    """The user's exact example must reach the RAG answerer, not an agent."""
    from api import is_document_question
    rag = _store_with_docs()
    assert is_document_question(
        "Compare what the CRM doc and sample.txt say about pricing", rag
    ) is True


def test_compare_question_without_store_stays_general():
    from api import is_document_question
    rag = _store_with_docs()
    # Non-document comparison is NOT a document question.
    assert is_document_question("compare python and javascript", rag) is False


# ============ PER-DOCUMENT RETRIEVAL (CITATIONS / COMPARE CONTEXT) ============

def test_get_context_for_document_known():
    rag = _store_with_docs()
    ctx = rag.get_context_for_document("pricing", "CRM-3.docx", top_k=2)
    assert "Source: uploads/CRM-3.docx" in ctx
    assert "pricing" in ctx.lower()


def test_get_context_for_document_unknown():
    rag = _store_with_docs()
    assert rag.get_context_for_document("pricing", "nope.txt", top_k=2) == ""


def test_get_context_has_citable_source_labels():
    rag = _store_with_docs()
    ctx = rag.get_context("what does the CRM doc say about pricing")
    assert "Source: uploads/CRM-3.docx" in ctx
    assert "(chunk 0)" in ctx


def test_doc_answer_prompt_requires_citations():
    """The RAG answer prompt must keep demanding inline citations."""
    import inspect
    from api import _ask_doc_question
    src = inspect.getsource(_ask_doc_question)
    assert "citation" in src.lower()
    assert "Source:" in src
