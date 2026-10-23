"""
v3.4.5 - document (RAG) answers, from a real test session on CRM-3.docx:
- a Groq 429 (daily token limit) was shown raw, with the organisation ID and an
  upgrade link, AND cached, so asking again kept returning the error (the
  semantic cache lives in ChromaDB and survives deploys);
- every answer opened with "Document description: CRM-3 is a Word document...";
- a "not in the document" answer was cut off after "CRM-3 is" (the reasoning
  model's hidden thinking used up the 1024-token budget).
"""

import os
import sys
import uuid
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

TPD_ERROR = (
    "Error code: 429 - {'error': {'message': 'Rate limit reached for model "
    "`openai/gpt-oss-20b` in organization `org_01kptd15pxfbpap6vs64hqf0b9` service tier "
    "`on_demand` on tokens per day (TPD): Limit 200000, Used 199545, Requested 2680. "
    "Please try again in 16m1.199999999s. Need more tokens? Upgrade to Dev Tier today at "
    "https://console.groq.com/settings/billing', 'type': 'tokens', 'code': 'rate_limit_exceeded'}}"
)
GOOD = ("The Rule of 40 is Growth % + FCF %, and a healthy SaaS business should reach "
        "at least 40% (CRM-3.docx, chunk 0).")


# ------------------------------------------------------- friendly errors --

def test_daily_limit_error_is_friendly_and_leaks_nothing():
    from api import friendly_llm_error
    msg = friendly_llm_error(Exception(TPD_ERROR))
    assert "today's free-tier AI usage limit" in msg
    assert "about 17 minutes" in msg  # 16m1.2s rounds up
    for leak in ("org_", "console.groq.com", "Upgrade", "Error code", "199545"):
        assert leak not in msg


def test_per_minute_and_other_errors():
    from api import friendly_llm_error
    per_min = friendly_llm_error(Exception(
        "Error code: 429 - Rate limit reached on tokens per minute (TPM). Please try again in 7.5s."))
    assert "per-minute" in per_min and "in a minute" in per_min
    no_wait = friendly_llm_error(Exception("429 rate_limit_exceeded"))
    assert "try again shortly" in no_wait
    other = friendly_llm_error(Exception("Connection reset by peer at 10.0.0.1"))
    assert "unavailable right now" in other and "10.0.0.1" not in other


# --------------------------------------------- describe only when asked --

@pytest.mark.parametrize("q", [
    "Summarize the uploaded file CRM-3.docx",
    "give me an overview of the document",
    "what is this file?",
    "what is the CRM file",
    "tell me about the uploaded document",
    "what does the CRM-3 document contain",
])
def test_whole_document_questions_get_a_description(q):
    from api import _WHOLE_DOC_RE
    assert _WHOLE_DOC_RE.search(q)


@pytest.mark.parametrize("q", [
    "According to the CRM-3 document, what is the formula for the Rule of 40?",
    "what does the crm file say about capital allocation?",
    "According to the CRM-3 document, what was Shopify's revenue multiple at its peak?",
    "What about if only the multiple expands and revenue stays at 100?",
])
def test_specific_questions_get_no_description(q):
    from api import _WHOLE_DOC_RE
    assert not _WHOLE_DOC_RE.search(q)


class _Client:
    """Fake Groq client: ``script`` is a list of responses/exceptions, used in
    order; the 120-token reformulation call gets ``reformulated``."""

    def __init__(self, script, reformulated=""):
        self.script = list(script)
        self.reformulated = reformulated
        self.calls = []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **kw):
        self.calls.append(kw)
        if kw.get("max_tokens") == 120:
            return _resp(self.reformulated)
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def _resp(text, finish="stop"):
    return SimpleNamespace(choices=[SimpleNamespace(
        message=SimpleNamespace(content=text), finish_reason=finish)])


def _prompt(client, i=0):
    return client.calls[i]["messages"][-1]["content"]


def test_prompt_and_budget():
    from api import _ask_doc_question
    c = _Client([_resp(GOOD), _resp(GOOD)])
    _ask_doc_question(c, "According to the CRM-3 document, what is the Rule of 40?", "ctx")
    assert "do not start by describing the document" in _prompt(c)
    assert "state exactly what this document IS" not in _prompt(c)
    assert "Cite the" in _prompt(c) and "Source:" in _prompt(c)
    assert c.calls[0]["max_tokens"] == 2048
    _ask_doc_question(c, "Summarize the uploaded file CRM-3.docx", "ctx")
    assert "state exactly what this document IS" in _prompt(c, 1)


def test_cut_off_answers():
    from api import _doc_completion_text, _DOC_CUT_NOTE
    assert _doc_completion_text(_resp("**Document description** CRM-3 is", "length")) == ""
    long_cut = _doc_completion_text(_resp("x" * 300, "length"))
    assert long_cut.endswith(_DOC_CUT_NOTE)
    assert _doc_completion_text(_resp("  fine  ")) == "fine"
    assert _doc_completion_text(_resp(None)) == ""


# ------------------------------------------------------ end-to-end flow --

class _Rag:
    def __init__(self, cached=None):
        self.owner = f"test-{uuid.uuid4()}"  # isolates the in-memory LRU
        self.cached = cached
        self.stored = []

    def list_documents(self):
        return ["CRM-3.docx"]

    def get_context(self, query, top_k=3):
        return "Relevant document excerpts:\n--- Source: CRM-3.docx (chunk 0) ---\nRule of 40 = Growth % + FCF %"

    def get_cached_answer(self, query):
        return self.cached

    def cache_answer(self, query, answer):
        self.stored.append(answer)


@pytest.fixture
def use_client(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "test-key")

    def _use(client):
        monkeypatch.setattr("api._get_groq_client", lambda key: client)
    return _use


def test_rate_limit_is_friendly_and_not_cached(use_client):
    from api import answer_from_documents
    rag = _Rag()
    q = "what does the crm file say about capital allocation?"
    # strong tier fails -> fast tier fails too (both rate-limited)
    use_client(_Client([Exception(TPD_ERROR), Exception(TPD_ERROR)]))
    out = answer_from_documents(q, rag)
    assert out.startswith("⚠️") and "org_" not in out and "about 17 minutes" in out
    assert rag.stored == []
    # asking again (limit reset) really retries and answers
    use_client(_Client([_resp(GOOD)]))
    assert answer_from_documents(q, rag) == GOOD
    assert rag.stored == [GOOD]


def test_error_cached_by_old_version_is_ignored(use_client):
    from api import answer_from_documents
    rag = _Rag(cached="Could not summarize the documents right now: " + TPD_ERROR)
    client = _Client([_resp(GOOD)])
    use_client(client)
    assert answer_from_documents("what is the rule of 40 in the CRM-3 document?", rag) == GOOD
    assert len(client.calls) == 1 and rag.stored == [GOOD]


def test_real_cached_answer_is_still_served(use_client):
    from api import answer_from_documents
    rag = _Rag(cached=GOOD)
    client = _Client([])
    use_client(client)
    assert answer_from_documents("what is the rule of 40 in the CRM-3 document?", rag) == GOOD
    assert client.calls == []


def test_cut_off_answer_is_not_cached_and_says_so(use_client):
    from api import answer_from_documents
    rag = _Rag()
    # cut off almost immediately; the reformulation adds nothing new
    use_client(_Client([_resp("**Document description** CRM-3 is", "length")]))
    out = answer_from_documents("According to the CRM-3 document, what was Shopify's peak multiple?", rag)
    assert out.startswith("⚠️") and "ran out of room" in out
    assert rag.stored == []


class _Rag2(_Rag):
    """Returns different excerpts for a reformulated query (second pass runs)."""

    def get_context(self, query, top_k=3):
        return super().get_context(query, top_k) + f"\n--- Source: CRM-3.docx (chunk 9) ---\n{query}"


def test_second_pass_success_after_cut_off_is_cached(use_client):
    from api import answer_from_documents
    rag = _Rag2()
    use_client(_Client([_resp("CRM-3 is", "length"), _resp(GOOD)],
                       reformulated="capital allocation CRM-3"))
    assert answer_from_documents("what about capital allocation?", rag) == GOOD
    assert rag.stored == [GOOD]


def test_second_pass_rate_limit_reports_the_limit(use_client):
    from api import answer_from_documents
    rag = _Rag2()
    use_client(_Client([_resp("CRM-3 is", "length"),
                        Exception(TPD_ERROR), Exception(TPD_ERROR)],
                       reformulated="capital allocation CRM-3"))
    out = answer_from_documents("what about capital allocation?", rag)
    assert out.startswith("⚠️") and "usage limit" in out and "org_" not in out
    assert rag.stored == []
