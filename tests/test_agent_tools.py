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


class _FakeToolUseFailed(Exception):
    """Mirrors Groq's HTTP 400 with code 'tool_use_failed'."""
    body = {"error": {"code": "tool_use_failed"}}


class _FakeRateLimitError(Exception):
    body = {"error": {"code": "rate_limit_exceeded"}}


class _FakeRetryCompletions:
    """create() entries: ('RAISE', exc) raises exc; otherwise (content,
    tool_calls) is returned. Records whether each call passed tools=."""

    def __init__(self, script):
        self._script = list(script)
        self._calls = 0
        self.tools_flags = []

    def create(self, **kwargs):
        self.tools_flags.append("tools" in kwargs)
        entry = self._script[self._calls]
        self._calls += 1
        if entry[0] == "RAISE":
            raise entry[1]
        content, tool_calls = entry
        return _FakeResponse([_FakeChoice(_FakeMessage(content, tool_calls))])


class _FakeRetryChat:
    def __init__(self, script):
        self.completions = _FakeRetryCompletions(script)


class _FakeRetryClient:
    def __init__(self, script):
        self.chat = _FakeRetryChat(script)


class _StreamDelta:
    def __init__(self, text):
        self.content = text


class _StreamChoice:
    def __init__(self, text):
        self.delta = _StreamDelta(text)


class _StreamChunk:
    def __init__(self, text):
        self.choices = [_StreamChoice(text)]


class _StreamCompletions:
    """Streaming chat completions for _stream_with_tools.

    Each create() plays the next script entry:
      ("RAISE", exc)      -> raise exc (models the strong tier failing)
      ("STREAM", [tokens]) -> return chunks with delta.content = token
    Records every model and messages payload it was called with.
    """

    def __init__(self, script):
        self._script = list(script)
        self._calls = 0
        self.models = []
        self.messages = []

    def create(self, **kwargs):
        self.models.append(kwargs.get("model"))
        self.messages.append(kwargs.get("messages"))
        entry = self._script[self._calls]
        self._calls += 1
        if entry[0] == "RAISE":
            raise entry[1]
        return [_StreamChunk(t) for t in entry[1]]


class _StreamChat:
    def __init__(self, script):
        self.completions = _StreamCompletions(script)


class _StreamClient:
    def __init__(self, script):
        self.chat = _StreamChat(script)


# ============ TOOL SCHEMAS ============

def test_agent_tool_schemas_include_search_and_docs():
    names = {t["function"]["name"] for t in AGENT_TOOL_SCHEMAS}
    assert "web_search" in names
    assert "list_uploaded_documents" in names
    assert "read_uploaded_document" in names


# ============ DISPATCH ============

def test_dispatch_unknown_tool():
    # The error is the model's only feedback channel, so it must say what it
    # CAN call - "Unknown tool: teleport" left it nowhere to go.
    out = dispatch_tool("teleport", {"query": "x"})
    assert out.startswith("Unknown tool: teleport.")
    assert "web_search" in out and "read_uploaded_document" in out


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


def test_read_document_pages_large_files():
    """A long document is PAGED, not amputated: the reply states the total
    length and the offset to ask for next. Truncating silently meant a
    40-page PDF became its first three pages with no signal."""
    rag = SimpleRAG()
    rag.add_text(("lorem ipsum dolor sit amet " * 400), source="uploads/big.txt")
    set_document_store(rag)
    out = read_uploaded_document("big.txt")
    # The store re-joins chunks, so take the total from the header itself
    # rather than recomputing it.
    assert "characters total" in out
    assert "start_char=" in out          # tells the model how to continue
    total = int(out.split(": ")[1].split(" characters total")[0])
    assert total > 6000, "the test document must exceed one window"
    first = read_uploaded_document("big.txt", 0)
    second = read_uploaded_document("big.txt", 6000)
    assert first != second               # different window
    tail = read_uploaded_document("big.txt", total - 10)
    assert "end of the document" in tail


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


# ============ BUG FIX: CURRENT-INFO QUESTIONS MUST REACH WEB-SEARCH TOOLS ============
# "what is the latest AI news?" used to fall through to the tool-less ReAct
# agent and answer from stale static knowledge. Recency queries must route to
# the research agent, which owns the web_search tool.

def test_route_multi_agent_recency_to_research():
    from api import route_multi_agent
    assert route_multi_agent("what is the latest AI news?") == (True, "research")
    assert route_multi_agent("any recent announcements from OpenAI?") == (True, "research")
    assert route_multi_agent("what happened with the stock market this week?") == (True, "research")
    assert route_multi_agent("breaking news about the election") == (True, "research")


def test_route_multi_agent_general_question_stays_react():
    from api import route_multi_agent
    # Static-knowledge questions must NOT hit the multi-agent path.
    assert route_multi_agent("what is a quasar?") == (False, "research")
    assert route_multi_agent("how does recursion work in python?") == (False, "research")
    assert route_multi_agent("explain the water cycle") == (False, "research")


# ============ BUG FIX: COMPARE TOPIC DISTILLATION ============
# Retrieval used to run on the FULL comparison request, so file-name and
# framing words ("compare", "doc", "txt") diluted the per-document search.

def test_distill_compare_query_keeps_topic_only():
    from api import _distill_compare_query
    q = _distill_compare_query(
        "Compare what the CRM doc and sample.txt say about pricing",
        ["uploads/CRM-3.docx", "uploads/sample.txt"],
    )
    assert q == "pricing"
    q2 = _distill_compare_query(
        "differences between the budget and the sales forecast",
        ["uploads/financials.xlsx", "uploads/other.xlsx"],
    )
    assert q2 == "budget sales forecast"
    # No topic words left -> empty topic (callers fall back to the raw query).
    assert _distill_compare_query("compare the files", ["uploads/a.pdf", "uploads/b.pdf"]) == ""


# ============ BUG FIX: PER-DOC RETRIEVAL PICKS THE TOPIC SECTION ============
# Presence-only scoring let incidental single mentions beat the real topic
# chunk on tie-breaks. Frequency scoring must pick the dense chunk and pull
# in its neighbors while dropping distant incidental mentions.

def _pad(text: str, width: int) -> str:
    return text.ljust(width, ".")


def test_get_context_for_document_picks_topic_run_not_incidental():
    rag = SimpleRAG()
    chunk0 = _pad(
        "The proposal mentioned pricing once in the appendix about vendor "
        "costs and contract renewal terms for the fiscal year.",
        500,
    )
    chunk1 = _pad(
        "This middle section covers operations only: onboarding, support "
        "tiers, escalation paths and internal tooling.",
        500,
    )
    chunk2 = _pad(
        "The core pricing discussion: pricing tiers, annual pricing "
        "discounts, volume pricing, and how pricing affects renewals.",
        300,
    )
    chunk3 = _pad(
        "Closing paragraph: pricing was reviewed and the decision deferred.",
        300,
    )
    rag.add_text(
        chunk0 + chunk1 + chunk2 + chunk3, source="uploads/CRM-3.docx"
    )
    ctx = rag.get_context_for_document("pricing", "CRM-3.docx", top_k=1)
    # The topic run (chunks 1-3, around the dense chunk 2) is retrieved...
    assert "chunk 2" in ctx
    assert "chunk 1" in ctx
    assert "chunk 3" in ctx
    # ...while distant single-mention chunks are NOT.
    assert "chunk 0" not in ctx


def test_get_context_for_document_dense_chunk_beats_single_mention():
    rag = SimpleRAG()
    chunk0 = _pad(
        "The proposal mentioned pricing once in the appendix about vendor "
        "costs and contract renewal terms for the fiscal year.",
        500,
    )
    filler = _pad(
        "no topic words here at all, just filler prose.", 500
    )
    dense = _pad(
        "The core pricing discussion: pricing tiers, annual pricing "
        "discounts, volume pricing, and how pricing affects renewals.",
        200,
    )
    # 500 + 500 + 200 chars -> chunk 0 = incidental mention, chunk 1 =
    # filler, chunk 2 = the dense topic section.
    rag.add_text(chunk0 + filler + dense, source="uploads/CRM-3.docx")
    ctx = rag.get_context_for_document("pricing", "CRM-3.docx", top_k=1)
    # The dense topic chunk is always retrieved, and the bulk of the
    # retrieved context comes from it, not the incidental mention.
    assert "chunk 2" in ctx
    assert ctx.count("pricing") >= 4


# ============ ROBUSTNESS: TOOL-LOOP ANSWER EXTRACTION ============

def test_last_assistant_content_skips_tool_call_messages():
    from multi_agent import BaseAgent
    # A tool-call message followed by an empty assistant message
    # should return '' (not the tool marker).
    msgs = [
        {"role": "user", "content": "q"},
        {"role": "assistant", "content": "<web_search>{}</web_search>",
         "tool_calls": [{"id": "1", "type": "function",
                         "function": {"name": "web_search",
                                      "arguments": "{}"}}]},
        {"role": "tool", "tool_call_id": "1", "content": "results"},
        {"role": "assistant", "content": ""},
    ]
    assert BaseAgent._last_assistant_content(msgs) == ""
    # A real answer after the tool round is returned correctly.
    msgs.append({"role": "assistant", "content": "the real answer"})
    assert BaseAgent._last_assistant_content(msgs) == "the real answer"


def test_tool_rounds_continues_on_empty_content():
    """Empty-content no-tool-call responses must not prematurely end
    the loop; the model gets another chance to answer."""
    set_document_store(SimpleRAG())
    client = _FakeClient([
        (None, None),  # empty content, no tool_calls -> skip, continue
        ("the answer", None),  # real answer -> return
    ])
    msgs = [{"role": "user", "content": "hi"}]
    out_msgs, events, final = run_tool_rounds(
        client, "m", msgs, tools=AGENT_TOOL_SCHEMAS, max_tokens=10,
    )
    assert final == "the answer"
    assert len(out_msgs) == 2  # only the real answer appended


def test_execute_with_tools_forces_wrap_up_on_transcript():
    """When the model ends on a tool-call message (echoing the
    transcript), _execute_with_tools must force a clean wrap-up
    answer instead of returning the markers."""
    os.environ["GROQ_API_KEY"] = "test-key"
    from multi_agent import ResearchAgent
    agent = ResearchAgent()

    # _run_tools returns messages ending with a tool-call assistant
    # message (no final answer). The tool-call content contains
    # spaces (realistic Groq/Llama output).
    fake_final = [
        {"role": "system", "content": agent._system},
        {"role": "user", "content": agent._task_prompt.format(task="t")},
        {"role": "assistant",
         "content": '<list_uploaded_documents>{}</list_uploaded_documents>',
         "tool_calls": [{"id": "c0", "type": "function",
                          "function": {"name": "list_uploaded_documents",
                                       "arguments": "{}"}}]},
        {"role": "tool", "tool_call_id": "c0", "content": "uploads/CRM-3.docx"},
        {"role": "assistant",
         "content": '{"query": "latest AI news 2024-2026"}',
         "tool_calls": [{"id": "c1", "type": "function",
                          "function": {"name": "web_search",
                                       "arguments": '{"query": "latest AI news 2024-2026"}'}}]},
        {"role": "tool", "tool_call_id": "c1", "content": "results"},
    ]
    agent._run_tools = lambda msgs: (fake_final, ["⚙️ list_uploaded_documents", "⚙️ web_search"])
    agent._chat = lambda msgs, max_tokens=2048: _FakeResponse(
        [_FakeChoice(_FakeMessage("clean final answer"))]
    )
    result = agent._execute_with_tools("t")
    assert result == "clean final answer"


def test_execute_with_tools_falls_back_when_tool_loop_raises():
    """If the tool loop raises (API error, rate limit), execute()
    must fall back to a plain non-tool answer instead of crashing."""
    os.environ["GROQ_API_KEY"] = "test-key"
    from multi_agent import ResearchAgent
    agent = ResearchAgent()
    agent._run_tools = lambda msgs: (_ for _ in ()).throw(
        RuntimeError("api down")
    )
    agent._chat = lambda msgs, max_tokens=2048: _FakeResponse(
        [_FakeChoice(_FakeMessage("plain fallback answer"))]
    )
    result = agent._execute_with_tools("t")
    assert result == "plain fallback answer"


def test_execute_with_tools_returns_fallback_message_when_all_fail():
    """When both the tool loop and the plain fallback fail, a
    friendly message is returned instead of an empty string."""
    os.environ["GROQ_API_KEY"] = "test-key"
    from multi_agent import ResearchAgent
    agent = ResearchAgent()
    agent._run_tools = lambda msgs: (_ for _ in ()).throw(
        RuntimeError("api down")
    )
    agent._chat = lambda msgs, max_tokens=2048: _FakeResponse(
        [_FakeChoice(_FakeMessage(""))]
    )
    result = agent._execute_with_tools("t")
    assert result != ""
    assert "try again" in result.lower()


# ============ STREAMING WITH TOOLS ============
# _stream_with_tools drives the same tool loop as _execute_with_tools but
# yields tool activity + answer tokens. It must survive a rate-limited
# strong tier: retry the whole tool round on the fast tier, then stream
# the final answer — never the generic "couldn't complete" message.

def _stream_tokens(out):
    return "".join(o["content"] for o in out if o["type"] == "token").strip()


def test_stream_with_tools_falls_back_to_fast_tier_on_rate_limit(monkeypatch):
    """When the strong tier is rate-limited, the whole tool round must be
    retried on the fast tier and the detailed answer still streamed."""
    os.environ["GROQ_API_KEY"] = "test-key"
    from multi_agent import ResearchAgent
    from modules.model_tiers import resolve_model
    agent = ResearchAgent()
    client = _StreamClient([])  # no regeneration call may happen
    agent.client = client

    calls = {"n": 0, "models": []}

    def fake_run_tool_rounds(client_, model_, messages_, tools_, **kwargs):
        calls["models"].append(model_)
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("429 rate limit reached")
        return messages_, ["⚙️ web_search"], "final content"

    monkeypatch.setattr(
        "modules.agent_tools.run_tool_rounds", fake_run_tool_rounds
    )
    out = list(agent._stream_with_tools("t"))
    assert calls["n"] == 2
    assert calls["models"][0] == agent.model
    assert calls["models"][1] == resolve_model("fast")
    assert {"type": "tool", "content": "⚙️ web_search"} in out
    assert _stream_tokens(out) == "final content"
    # The answer is streamed directly - no extra (TPM-prone) generation.
    assert client.chat.completions.models == []


def test_stream_with_tools_plain_stream_when_both_tool_rounds_fail(monkeypatch):
    """If the tool loop fails on both tiers, fall back to a plain
    non-tool stream (strong, then fast) instead of the failure message."""
    os.environ["GROQ_API_KEY"] = "test-key"
    from multi_agent import ResearchAgent
    from modules.model_tiers import resolve_model
    agent = ResearchAgent()
    client = _StreamClient([
        ("RAISE", RuntimeError("429 rate limit reached")),  # strong plain stream
        ("STREAM", ["plain ", "answer"]),                   # fast plain stream
    ])
    agent.client = client

    def fake_run_tool_rounds(client_, model_, messages_, tools_, **kwargs):
        raise RuntimeError("api down")

    monkeypatch.setattr(
        "modules.agent_tools.run_tool_rounds", fake_run_tool_rounds
    )
    out = list(agent._stream_with_tools("t"))
    assert client.chat.completions.models == [
        agent.model, resolve_model("fast"),
    ]
    assert _stream_tokens(out) == "plain answer"


def test_stream_with_tools_wrap_up_generation_falls_back_to_fast_tier(monkeypatch):
    """When the tool round hits the cap without an answer, the forced
    wrap-up generation retries on the fast tier if the strong tier
    rate-limits mid-run."""
    os.environ["GROQ_API_KEY"] = "test-key"
    from multi_agent import ResearchAgent
    from modules.model_tiers import resolve_model
    agent = ResearchAgent()
    client = _StreamClient([
        ("RAISE", RuntimeError("429 rate limit reached")),  # strong wrap-up
        ("STREAM", ["final ", "answer"]),                   # fast wrap-up
    ])
    agent.client = client

    def fake_run_tool_rounds(client_, model_, messages_, tools_, **kwargs):
        # Round cap reached without an answer -> caller must force wrap-up.
        return messages_, ["⚙️ web_search"], None

    monkeypatch.setattr(
        "modules.agent_tools.run_tool_rounds", fake_run_tool_rounds
    )
    out = list(agent._stream_with_tools("t"))
    assert client.chat.completions.models == [
        agent.model, resolve_model("fast"),
    ]
    assert {"type": "tool", "content": "⚙️ web_search"} in out
    assert _stream_tokens(out) == "final answer"


def test_stream_with_tools_streams_existing_answer_without_api_call(monkeypatch):
    """A real answer from the tool round is streamed as tokens directly —
    no regeneration API call — so a rate-limited tier can't lose it."""
    os.environ["GROQ_API_KEY"] = "test-key"
    from multi_agent import ResearchAgent
    agent = ResearchAgent()
    client = _StreamClient([])  # any create() call would fail the test
    agent.client = client

    def fake_run_tool_rounds(client_, model_, messages_, tools_, **kwargs):
        return messages_, ["⚙️ web_search"], "real detailed answer"

    monkeypatch.setattr(
        "modules.agent_tools.run_tool_rounds", fake_run_tool_rounds
    )
    out = list(agent._stream_with_tools("t"))
    assert {"type": "tool", "content": "⚙️ web_search"} in out
    assert _stream_tokens(out) == "real detailed answer"
    assert client.chat.completions.models == []




# ============ BUG FIX: TRANSCRIPT-STYLE TOOL CALLS ============
# llama-3.3-70b writes tool calls as plain-text markers inside content
# (<function\name={args}</function>, <name>{args}</name>) instead of using
# Groq's native tool_calls field. Those markers must be parsed and executed
# as real tool calls — not returned as the "final answer" (which left
# web_search never running and answers coming from stale model memory).

def test_parse_transcript_tool_calls_func_format():
    from modules.agent_tools import _parse_transcript_tool_calls
    out = _parse_transcript_tool_calls(
        '<function\\web_search={"query": "OpenAI recent announcements"}</function>'
    )
    assert out == [("web_search", {"query": "OpenAI recent announcements"})]


def test_parse_transcript_tool_calls_legacy_format():
    from modules.agent_tools import _parse_transcript_tool_calls
    out = _parse_transcript_tool_calls(
        '<web_search>{"query": "latest AI news"}</web_search>'
    )
    assert out == [("web_search", {"query": "latest AI news"})]


def test_parse_transcript_tool_calls_equals_variants():
    from modules.agent_tools import _parse_transcript_tool_calls
    # <function=name{json}</function> (no separator after the name)
    assert _parse_transcript_tool_calls(
        '<function=web_search{"query": "OpenAI recent announcements"}</function>'
    ) == [("web_search", {"query": "OpenAI recent announcements"})]
    # <function=name={json}</function> (extra '=' separator)
    assert _parse_transcript_tool_calls(
        '<function=web_search={"query": "latest AI news"}</function>'
    ) == [("web_search", {"query": "latest AI news"})]
    # <function=name {json} </function> (space separators)
    assert _parse_transcript_tool_calls(
        '<function=web_search {"query": "latest AI news"} </function>'
    ) == [("web_search", {"query": "latest AI news"})]


def test_parse_transcript_tool_calls_ignores_malformed():
    from modules.agent_tools import _parse_transcript_tool_calls
    assert _parse_transcript_tool_calls("plain answer text") == []
    assert _parse_transcript_tool_calls(
        '<function\\web_search={"broken"}</function>'
    ) == []
    assert _parse_transcript_tool_calls("") == []


def test_tool_rounds_executes_transcript_style_tool_call():
    """A content-only assistant message carrying a transcript marker must
    execute the tool (append a real tool result) and continue to an answer."""
    set_document_store(_store_with_docs())
    client = _FakeClient([
        ('<function\\list_uploaded_documents>{}</function>', None),
        ("final answer text", None),
    ])
    msgs = [{"role": "user", "content": "read my docs"}]
    out_msgs, events, final = run_tool_rounds(
        client, "m", msgs, tools=AGENT_TOOL_SCHEMAS
    )
    assert final == "final answer text"
    assert events == ["⚙️ **list_uploaded_documents**"]
    # The tool result was appended as a real tool message.
    assert out_msgs[-2]["role"] == "tool"
    assert "CRM-3.docx" in out_msgs[-2]["content"]


def test_tool_rounds_retries_without_tools_on_tool_use_failed():
    """A Groq 400 'tool_use_failed' must be retried WITHOUT the tools param
    so the model's marker comes back as plain content and still executes."""
    set_document_store(_store_with_docs())
    fake = _FakeRetryClient([
        ("RAISE", _FakeToolUseFailed()),
        ('<function=list_uploaded_documents{}</function>', None),
        ("final answer text", None),
    ])
    msgs = [{"role": "user", "content": "read my docs"}]
    out_msgs, events, final = run_tool_rounds(
        fake, "m", msgs, tools=AGENT_TOOL_SCHEMAS, max_rounds=4
    )
    assert final == "final answer text"
    assert events == ["⚙️ **list_uploaded_documents**"]
    # First call had tools=, the retry dropped it, the next round has it again.
    assert fake.chat.completions.tools_flags == [True, False, True]
    # The tool result was appended as a real tool message.
    assert out_msgs[-2]["role"] == "tool"
    assert "CRM-3.docx" in out_msgs[-2]["content"]


def test_tool_rounds_re_raises_non_tool_use_errors():
    """Errors other than tool_use_failed must propagate, not be retried."""
    set_document_store(SimpleRAG())
    fake = _FakeRetryClient([("RAISE", _FakeRateLimitError())])
    msgs = [{"role": "user", "content": "hi"}]
    import pytest
    with pytest.raises(_FakeRateLimitError):
        run_tool_rounds(fake, "m", msgs, tools=AGENT_TOOL_SCHEMAS, max_rounds=4)
    # Only the single failing call was made (no retry).
    assert fake.chat.completions.tools_flags == [True]


def test_tool_rounds_pushes_frame_response_to_use_tools():
    """A narration-only response ("I'll search for...") is not an answer:
    the loop must push the model to actually use the tools and continue."""
    set_document_store(SimpleRAG())
    client = _FakeClient([
        ("I'll search for the latest news on OpenAI.", None),
        ("Here is the answer.", None),
    ])
    msgs = [{"role": "user", "content": "any recent announcements?"}]
    out_msgs, events, final = run_tool_rounds(
        client, "m", msgs, tools=AGENT_TOOL_SCHEMAS, max_rounds=4
    )
    assert final == "Here is the answer."
    # The frame + push message are present between the task and the answer.
    assert [m["role"] for m in out_msgs].count("user") >= 2


def test_is_frame_response_detects_narration():
    from modules.agent_tools import is_frame_response
    assert is_frame_response("I'll search for the latest news on OpenAI.")
    assert is_frame_response("Searching for the latest announcements from OpenAI.")
    assert is_frame_response("Let me look up the latest tech news this week.")
    # Real answers are NOT frames.
    assert not is_frame_response("OpenAI announced GPT-5 at its launch event.")
    # Long text is an answer even if it opens with a narration phrase.
    assert not is_frame_response("Searching for the latest information... " + "x" * 300)


def test_duckduckgo_bot_wall_returns_unavailable_message():
    """When DuckDuckGo serves its anomaly/challenge page, the agent must be
    told search is unavailable (not a misleading 'no results')."""
    import modules.web_tools as wt
    wall = (
        "<html><body>Anomaly detected! Please solve the challenge to "
        "continue searching.</body></html>"
    )
    orig = wt._http_get
    wt._http_get = lambda url, timeout=15: wall
    try:
        out = wt._search_duckduckgo("OpenAI news", 5)
        assert "unavailable" in out.lower()
        assert "blocking" in out.lower()
    finally:
        wt._http_get = orig


def test_duckduckgo_normal_page_still_formats_results():
    """A normal DDG results page must keep formatting results normally."""
    import modules.web_tools as wt
    page = (
        '<a class="result__a" href="https://example.com/1">Example Title</a>'
        '<a class="result__snippet">Example snippet text here.</a>'
    )
    orig = wt._http_get
    wt._http_get = lambda url, timeout=15: page
    try:
        out = wt._search_duckduckgo("OpenAI news", 5)
        assert "Example Title" in out
        assert "https://example.com/1" in out
    finally:
        wt._http_get = orig


# ============ REPEAT-CALL GUARD (tool-loop loop detection) ============

def test_repeated_identical_call_is_refused_then_recovers(monkeypatch):
    """The same (tool, args) pair runs at most _MAX_IDENTICAL_CALLS times.

    The default of 2 allows ONE retry (a transient error is worth retrying);
    the third identical call is refused and the model is told to change
    approach. A NEW query is never affected.
    """
    import modules.agent_tools as at

    seen = []
    monkeypatch.setattr(at, "dispatch_tool",
                        lambda name, args: seen.append(dict(args)) or "SAME RESULT")

    same = _FakeToolCall("web_search", '{"query": "same query"}')
    other = _FakeToolCall("web_search", '{"query": "different query"}')
    # Rounds: same, same (retry ok), same (REFUSED), new query, then answer.
    client = _FakeClient([
        ("", [same]), ("", [same]), ("", [same]), ("", [other]), ("done", None),
    ])
    msgs = [{"role": "user", "content": "hi"}]
    out_msgs, _events, final = run_tool_rounds(
        client, "m", msgs, tools=AGENT_TOOL_SCHEMAS, max_rounds=5)

    tool_contents = [m["content"] for m in out_msgs if m.get("role") == "tool"]
    assert len(tool_contents) == 4
    assert "SAME RESULT" in tool_contents[0]          # 1st: executed
    assert "SAME RESULT" in tool_contents[1]          # 2nd: retry allowed
    assert "already called web_search" in tool_contents[2]   # 3rd: refused
    assert "SAME RESULT" not in tool_contents[2]
    assert "SAME RESULT" in tool_contents[3]          # different query: fine
    assert len(seen) == 3, "dispatch runs 3x (2 same + 1 new), not 4"
    assert final == "done"


def test_repeat_guard_does_not_fire_on_different_args(monkeypatch):
    """Two DIFFERENT queries are never treated as repeats."""
    import modules.agent_tools as at
    monkeypatch.setattr(at, "dispatch_tool", lambda name, args: "OK")
    calls = [_FakeToolCall("web_search", '{"query": "a"}'),
             _FakeToolCall("web_search", '{"query": "b"}'),
             _FakeToolCall("web_search", '{"query": "c"}')]
    client = _FakeClient([("", [c]) for c in calls] + [("done", None)])
    msgs = [{"role": "user", "content": "hi"}]
    out_msgs, _e, _f = run_tool_rounds(
        client, "m", msgs, tools=AGENT_TOOL_SCHEMAS, max_rounds=4)
    tool_contents = [m["content"] for m in out_msgs if m.get("role") == "tool"]
    assert all("already called" not in c for c in tool_contents)
