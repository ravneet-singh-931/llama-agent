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
        monkeypatch.setattr("modules.doc_answers._get_groq_client", lambda key: client)
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


# ================================================================ v3.4.6 ==
# Corrective RAG really runs, versioned answer cache, file-name routing.

UNSUPPORTED_ANSWER = ("Shopify peaked at 80x revenue in 2021 and capital allocation "
                      "covers unit economics (CRM-3.docx, chunk 1).")


class _LLM:
    """Fake GroqLLMClient for OptimizedRAG.verify_answer."""

    def __init__(self, reply):
        self.reply = reply
        self.calls = []

    def complete(self, system_prompt, user_prompt, max_tokens=500):
        self.calls.append({"system": system_prompt, "user": user_prompt, "max_tokens": max_tokens})
        if isinstance(self.reply, Exception):
            raise self.reply
        return self.reply


def _verifier(reply):
    from modules.rag_optimizations import OptimizedRAG
    rag = OptimizedRAG.__new__(OptimizedRAG)  # verify_answer only needs _llm
    rag._llm = _LLM(reply)
    return rag


@pytest.mark.parametrize("reply,ok", [
    ("SUPPORTED", True),
    ("UNSUPPORTED", False),
    ("Checking each claim... the 80x figure is absent. UNSUPPORTED", False),
    ("NOT SUPPORTED", False),
    ("supported", True),
    ("", True),                  # empty (old 5-token budget) -> unsure -> passes
    (RuntimeError("429"), True),  # model unavailable -> unsure -> passes
])
def test_crag_verdicts(reply, ok, monkeypatch):
    monkeypatch.delenv("RAG_CRAG", raising=False)  # on by default now
    rag = _verifier(reply)
    assert rag.verify_answer("some answer", "Source: CRM-3.docx (chunk 0)\nctx") is ok
    call = rag._llm.calls[0]
    # a reasoning model needs room to think before the verdict
    assert call["max_tokens"] >= 256


def test_crag_sees_the_same_excerpts_the_answer_used(monkeypatch):
    monkeypatch.delenv("RAG_CRAG", raising=False)
    rag = _verifier("SUPPORTED")
    context = "Source: CRM-3.docx (chunk 5)\n" + ("x" * 9000) + "LAST-CHUNK-MARKER"
    rag.verify_answer("answer", context)
    assert "LAST-CHUNK-MARKER" in rag._llm.calls[0]["user"]


def test_crag_can_be_switched_off(monkeypatch):
    monkeypatch.setenv("RAG_CRAG", "0")
    rag = _verifier("UNSUPPORTED")
    assert rag.verify_answer("answer", "ctx") is True and rag._llm.calls == []


class _VerifyingRag(_Rag2):
    """_Rag2 plus a scripted Corrective-RAG verifier."""

    def __init__(self, verdicts):
        super().__init__()
        self.verdicts = list(verdicts)
        self.verified = []

    def verify_answer(self, answer, context):
        self.verified.append(answer)
        return self.verdicts.pop(0)


def test_unsupported_answer_is_retried_and_the_retry_answers_the_users_question(use_client):
    from api import answer_from_documents
    rag = _VerifyingRag([False, True])
    client = _Client([_resp(UNSUPPORTED_ANSWER), _resp(GOOD)],
                     reformulated="capital allocation behaviour CRM-3")
    use_client(client)
    q = "What is the capital allocation point in the CRM file?"
    assert answer_from_documents(q, rag) == GOOD
    assert rag.verified == [UNSUPPORTED_ANSWER, GOOD]
    assert rag.stored == [GOOD]
    # the retry still answers the user's question, not the search query
    answer_calls = [c for c in client.calls if c["max_tokens"] != 120]
    assert f"Question: {q}" in answer_calls[1]["messages"][-1]["content"]


def test_answer_that_stays_unsupported_is_flagged_and_not_cached(use_client):
    from api import answer_from_documents, _DOC_UNVERIFIED_NOTE
    rag = _VerifyingRag([False, False])
    use_client(_Client([_resp(UNSUPPORTED_ANSWER), _resp(UNSUPPORTED_ANSWER)],
                       reformulated="capital allocation behaviour CRM-3"))
    out = answer_from_documents("What is the capital allocation point in the CRM file?", rag)
    assert out.startswith(UNSUPPORTED_ANSWER) and out.endswith(_DOC_UNVERIFIED_NOTE)
    assert rag.stored == []


def test_unsupported_without_a_better_retrieval_is_flagged(use_client):
    from api import answer_from_documents, _DOC_UNVERIFIED_NOTE

    class _SameContextRag(_Rag):
        def verify_answer(self, answer, context):
            return False
    rag = _SameContextRag()
    use_client(_Client([_resp(UNSUPPORTED_ANSWER)], reformulated="capital allocation CRM-3"))
    out = answer_from_documents("What is the capital allocation point in the CRM file?", rag)
    assert out.endswith(_DOC_UNVERIFIED_NOTE) and rag.stored == []


def test_thin_answer_skips_the_paid_check(use_client):
    from api import answer_from_documents
    rag = _VerifyingRag([])  # any verify call would pop from an empty list
    not_there = ("The CRM-3 document does not mention Shopify or its peak multiple "
                 "(CRM-3.docx, chunk 0).")
    use_client(_Client([_resp(not_there), _resp(not_there)], reformulated="Shopify peak multiple"))
    assert answer_from_documents("What is Shopify's peak multiple in the CRM file?", rag) == not_there
    assert rag.verified == []


# ---------------------------------------------------- versioned cache --

class _Collection:
    """ChromaDB-like: returns stored entries nearest-first with set distances."""

    def __init__(self):
        self.rows = []  # (distance, metadata)

    def upsert(self, ids, documents, metadatas):
        self.rows.append((0.0, metadatas[0]))

    def query(self, query_texts, n_results=3, include=None, where=None):
        rows = sorted(self.rows, key=lambda r: r[0])[:n_results]
        return {"metadatas": [[m for _d, m in rows]], "distances": [[d for d, _m in rows]]}


def _cache_with(rows):
    from modules.rag_optimizations import SemanticCache
    coll = _Collection()
    coll.rows = list(rows)
    store = SimpleNamespace(client=SimpleNamespace(get_or_create_collection=lambda **kw: coll))
    return SemanticCache(store), coll


def test_answers_cached_by_an_older_version_are_not_served():
    old = {"answer": "**Document description** CRM-3 is", "owner": ""}  # <= 3.4.5: no "v"
    cache, _ = _cache_with([(0.05, old)])
    assert cache.lookup("What is Shopify's peak multiple in the CRM file?") is None


def test_a_nearer_old_answer_does_not_hide_a_current_one():
    from modules.rag_optimizations import SemanticCache
    old = {"answer": "stale", "owner": ""}
    new = {"answer": GOOD, "owner": "", "v": SemanticCache.ANSWER_VERSION}
    cache, _ = _cache_with([(0.01, old), (0.04, new)])
    assert cache.lookup("q") == GOOD
    # but never beyond the similarity threshold
    cache, _ = _cache_with([(0.01, old), (0.50, new)])
    assert cache.lookup("q") is None


# Regressions from the v3.4.7 RAG answer eval: both cache hits were answers to
# OTHER questions. A related question must miss; only a repeat may hit.

def _entry(query, answer):
    from modules.rag_optimizations import SemanticCache
    return {"query": query, "answer": answer, "owner": "", "v": SemanticCache.ANSWER_VERSION}


def test_a_related_but_different_question_misses_the_cache():
    # "Team plan cost?" vs cached "Team plan rate limit?" - similar wording,
    # different question (the old 0.3 limit served the rate-limit answer).
    rate = _entry("What is the rate limit on the Team plan?", "600 requests per minute.")
    for distance in (0.06, 0.2, 0.29):
        cache, _ = _cache_with([(distance, rate)])
        assert cache.lookup("How much does the Team plan cost?") is None


def test_questions_with_different_numbers_never_share_an_answer():
    rev = _entry("What was Kestrel's revenue in the third quarter of 2026?", "$48.6 million.")
    for q in ("What was Kestrel's revenue in the second quarter of 2026?",
              "What was Kestrel's revenue in the third quarter of 2025?",
              "What was Kestrel's revenue in the quarter?"):
        cache, _ = _cache_with([(0.01, rev)])  # even when the embeddings are almost equal
        assert cache.lookup(q) is None, q
    price = _entry("What is the monthly price of the Team plan?", "$49 per user.")
    cache, _ = _cache_with([(0.01, price)])
    assert cache.lookup("What is the annual price of the Team plan?") is None


def test_a_repeated_or_reworded_question_still_hits():
    rev = _entry("What was Kestrel's revenue in Q3 2026?", "$48.6 million.")
    cache, _ = _cache_with([(0.0, rev)])
    assert cache.lookup("What was Kestrel's revenue in Q3 2026?") == "$48.6 million."
    cache, _ = _cache_with([(0.03, rev)])
    assert cache.lookup("what's Kestrel's revenue in Q3 2026") == "$48.6 million."


def test_new_entries_carry_the_version_and_round_trip():
    from modules.rag_optimizations import SemanticCache
    cache, coll = _cache_with([])
    cache.store("q", GOOD)
    assert coll.rows[0][1]["v"] == SemanticCache.ANSWER_VERSION
    assert cache.lookup("q") == GOOD


# ------------------------------------------------- file-name routing --

class _Docs:
    def __init__(self, names):
        self.names = names

    def list_documents(self):
        return self.names


DOCS = _Docs(["uploads/u1/CRM-3.docx", "uploads/u1/API BOOK.docx", "uploads/u1/sales_2024.csv"])


@pytest.mark.parametrize("q", [
    "What does the crm file say about capital allocation?",
    "According to the CRM-3 document, what was Shopify's peak multiple?",
    "What does the CRM-3 document say about cohort durability?",
    "in the api book doc, how is authentication done?",
    "does the sales sheet show a drop in Q3?",
    "Is churn mentioned in CRM-3.docx?",
])
def test_questions_naming_an_uploaded_file_are_document_questions(q):
    from api import is_document_question
    assert is_document_question(q, DOCS)


@pytest.mark.parametrize("q", [
    "what is a crm?",                                  # no file reference
    "write a python file that loads crm data",         # a task for the coder
    "Create a document comparing CRM tools",            # a task for the writer
    "how do I install docker on ubuntu?",               # "doc" inside "docker"
    "what file format should I use for sales data?",    # name word far from "file"
    "which file type is best for exporting crm records?",  # name word far from "file"
    "summarize today's news about sales tax",           # no file reference
])
def test_other_messages_are_not(q):
    from api import is_document_question
    assert not is_document_question(q, DOCS)


def test_routing_survives_a_failing_store():
    from api import is_document_question

    class _Broken:
        def list_documents(self):
            raise RuntimeError("chroma down")
    assert not is_document_question("what does the crm file say about pricing?", _Broken())


# ------------------------------------- cache invalidation across the module --
#
# The generation counter moved from api.py to modules/doc_answers.py when the
# document cluster was extracted. If the two ever became separate copies, every
# check below would still pass *except* the last one: a stale answer would be
# served after the owner's documents changed. That is the regression these
# tests exist to catch, so they assert on the observable behaviour (miss after a
# bump) rather than on where the dict lives.

def test_a_bump_changes_the_cache_key_for_that_owner_only():
    from modules import doc_answers as d

    class _Rag:
        def __init__(self, owner):
            self.owner = owner

    rag = _Rag("owner-bump-checks")
    before = d._doc_cache_key(rag, "what is the rule of 40?")
    d.bump_doc_generation("owner-bump-checks")
    after = d._doc_cache_key(rag, "what is the rule of 40?")
    other = d._doc_cache_key(_Rag("someone-else"), "what is the rule of 40?")

    assert before != after, "bumping must change the key, or stale answers survive"
    assert "0|" in before and "1|" in after
    # another owner is untouched: bumping is per-owner, not global
    assert "|0|" in other


def test_api_bump_reaches_the_module_that_owns_the_counter():
    """api._bump_doc_generation must move the same dict the cache key reads.

    This is the whole point of the extraction: two names, one counter.
    """
    from modules import doc_answers as d
    import api

    class _Rag:
        def __init__(self, owner):
            self.owner = owner

    rag = _Rag("owner-cross-module")
    before = d._doc_cache_key(rag, "q")
    api._bump_doc_generation("owner-cross-module")
    after = d._doc_cache_key(rag, "q")
    assert before != after, "api's bump did not reach the module's counter"


def test_a_cached_answer_is_not_served_after_a_bump(use_client):
    """End to end: answer, bump, ask again -> the model is consulted again."""
    from modules import doc_answers as d
    from api import answer_from_documents

    class _Fresh(_Rag):
        def get_cached_answer(self, query):
            return None

    rag = _Fresh()
    client = _Client([_resp(GOOD), _resp(GOOD)])
    use_client(client)

    first = answer_from_documents("What is the Rule of 40?", rag)
    calls_after_first = len(client.calls)  # a snapshot: client.calls is a list, aliasing it compares it to itself
    # second identical question is served from the LRU, no model call
    second = answer_from_documents("What is the Rule of 40?", rag)
    assert second == first
    assert len(client.calls) == calls_after_first, "repeat should hit the cache"

    d.bump_doc_generation(rag.owner)
    answer_from_documents("What is the Rule of 40?", rag)
    assert len(client.calls) > calls_after_first, "after a bump the model must be asked again"


def test_the_doc_module_has_no_import_of_api():
    """A cycle here would be invisible at runtime but fatal on a cold import."""
    import ast, pathlib
    src = pathlib.Path(d(__file__)).parent.parent.joinpath("modules/doc_answers.py").read_text()
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            assert not any(a.name == "api" for a in node.names)
        if isinstance(node, ast.ImportFrom):
            assert node.module != "api"


def d(p):
    import os
    return os.path.join(os.path.dirname(p), "..")
