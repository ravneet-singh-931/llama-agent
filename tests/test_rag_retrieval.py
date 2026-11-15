"""Retrieval behaviour in `modules/rag.py` and `modules/advanced_rag.py`.

Two different kinds of test in one file, for the same reason: **coverage was
hiding which of these is live**. `advanced_rag.py` sat at 28% and `rag.py` at
67%, and in both cases the untested parts contained real logic - but only one of
the two modules is on the request path.

`modules/rag.py::SimpleRAG` is **live**: `api.py` builds it at startup and every
document question goes through its `get_context`. Its owner scoping is covered
here because a leak would be a cross-tenant defect.

`modules/advanced_rag.py` is **not reachable from the application**. `AdvancedRAG`
is constructed nowhere, and `QueryDecomposer`, the live decomposer in
`rag_optimizations.py`, is only reachable through `OptimizedRAG.query()`, which
has no caller either. That does not make the bugs below harmless - it makes them
**latent**, and latent code is what someone wires up next. They are pinned rather
than fixed, and the tests say which is which.
"""
from modules.rag import SimpleRAG
from modules.advanced_rag import SubQuestionEngine
from modules.rag_optimizations import QueryDecomposer


# ------------------------------------------------------------ SimpleRAG: live --

def test_documents_are_chunks_of_the_configured_size():
    rag = SimpleRAG()
    rag.add_text("x" * 1200, source="big.txt", owner="alice")
    chunks = rag.get_document_chunks("big.txt", owner="alice")
    assert [len(c["content"]) for c in chunks] == [500, 500, 200]
    assert [c["chunk_index"] for c in chunks] == [0, 1, 2]


def test_add_document_replaces_a_re_uploaded_file():
    """Re-uploading the same file must replace it, not double every chunk.

    This is the only path the application uses (`api.py` 605, `llama_agent1.py`
    495, `evals/rag_eval.py`), and it does replace - verified here rather than
    assumed, because a duplicated re-upload would double the context and dilute
    retrieval without any error.
    """
    import tempfile
    import os

    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "doc.txt")
        rag = SimpleRAG()

        with open(path, "w") as f:
            f.write("first version")
        rag.add_document(path, owner="alice")

        with open(path, "w") as f:
            f.write("second version")
        rag.add_document(path, owner="alice")

        chunks = rag.get_document_chunks("doc.txt", owner="alice")
        assert len(chunks) == 1, f"re-upload duplicated chunks: {chunks}"
        assert chunks[0]["content"] == "second version"


def test_add_text_appends_and_that_is_documented_behaviour():
    """`add_text` has NO replace logic - it appends, always.

    My first version of this file asserted that it replaced, and failed. That was
    my error, not a bug: `add_text` is not on any request path (nothing in the
    application calls it - `add_document` is the only entry used), and its job is
    to add a chunk of text under a source label, not to own a file.

    Pinned so the difference between the two methods is visible: if someone
    routes user uploads through `add_text`, every re-upload silently duplicates.
    """
    rag = SimpleRAG()
    rag.add_text("first version", source="doc.txt", owner="alice")
    rag.add_text("second version", source="doc.txt", owner="alice")

    chunks = rag.get_document_chunks("doc.txt", owner="alice")
    assert len(chunks) == 2, (
        "add_text now replaces - if that was deliberate, update this test and say "
        "so in CHANGELOG; if uploads were moved onto it, that is the reason"
    )


def test_the_same_filename_for_two_owners_is_two_documents():
    """The upload path is per-owner; a shared filename must not collide.

    Measured: alice's content survives bob uploading a different `report.txt`.
    """
    rag = SimpleRAG()
    rag.add_text("alice pricing data", source="report.txt", owner="alice")
    rag.add_text("bob hiring data", source="report.txt", owner="bob")

    a = rag.get_document_chunks("report.txt", owner="alice")
    b = rag.get_document_chunks("report.txt", owner="bob")
    assert [c["content"] for c in a] == ["alice pricing data"]
    assert [c["content"] for c in b] == ["bob hiring data"]


def test_get_context_never_returns_another_owners_text():
    """The load-bearing isolation test: no keyword match, documents exist.

    That branch returns *every* document of the owner - so it is the branch most
    likely to leak if the owner filter is wrong.
    """
    rag = SimpleRAG()
    rag.add_text("ALICE SECRET SALARY BAND", source="a.txt", owner="alice")
    rag.add_text("bob public notes", source="b.txt", owner="bob")

    ctx = rag.get_context("zzz nothing matches zzz", owner="bob")
    assert "ALICE SECRET" not in ctx
    assert "bob public notes" in ctx


def test_a_filename_match_outranks_content():
    """Asking about "the captainassistant file" must retrieve that file even if
    its body never repeats the name."""
    rag = SimpleRAG()
    rag.add_text("unrelated body text", source="captainassistant.md", owner="a")
    rag.add_text("captainassistant appears in this body", source="other.md", owner="a")

    top = rag.search("captainassistant", top_k=2, owner="a")[0]
    assert top["file"] == "captainassistant.md"
    # filename tokens score 2x content tokens
    assert top["relevance_score"] == 2


def test_search_is_deterministic_on_ties():
    """Two chunks with equal score must come back in insertion order, or the
    same question returns different context on different runs."""
    rag = SimpleRAG()
    rag.add_text("alpha " + "x" * 500, source="one.txt", owner="a")
    rag.add_text("alpha " + "y" * 500, source="two.txt", owner="a")

    first = rag.search("alpha", top_k=2, owner="a")
    for _ in range(5):
        assert rag.search("alpha", top_k=2, owner="a") == first


def test_an_empty_knowledge_base_says_so():
    rag = SimpleRAG()
    assert rag.get_context("anything") == "No documents have been uploaded."


def test_the_cached_answer_is_per_owner():
    """A cache keyed only on the query would serve alice's answer to bob."""
    rag = SimpleRAG()
    rag.cache_answer("what is the policy?", "alice's policy", owner="alice")

    assert rag.get_cached_answer("what is the policy?", owner="alice") == "alice's policy"
    assert rag.get_cached_answer("what is the policy?", owner="bob") is None


def test_the_cache_key_ignores_spacing_and_case():
    rag = SimpleRAG()
    rag.cache_answer("  What   Is  X? ", "an answer", owner="alice")
    assert rag.get_cached_answer("what is x?", owner="alice") == "an answer"


def test_adding_a_document_invalidates_only_that_owners_cache():
    rag = SimpleRAG()
    rag.cache_answer("q", "alice ans", owner="alice")
    rag.cache_answer("q", "bob ans", owner="bob")

    rag.add_text("new content", source="new.txt", owner="alice")

    assert rag.get_cached_answer("q", owner="alice") is None
    assert rag.get_cached_answer("q", owner="bob") == "bob ans", (
        "adding a document for alice wiped bob's cache"
    )


def test_deleting_with_no_owner_removes_every_owners_copy():
    """MEASURED, and deliberate per `doc_scope.py`: `owner=None` means "no
    filter", documented as admin/maintenance use only.

    It is still worth pinning, because it means this method must never be given
    a None owner by a request handler. `scoped()` always binds an owner
    (`doc_scope.py` 158), so no endpoint can reach this state.
    """
    rag = SimpleRAG()
    rag.add_text("alice doc", source="x.txt", owner="alice")
    rag.add_text("bob doc", source="x.txt", owner="bob")

    assert rag.delete_document("x.txt") == 2
    assert not rag.has_document("x.txt", owner="alice")
    assert not rag.has_document("x.txt", owner="bob")


def test_deleting_as_one_owner_leaves_the_other_alone():
    rag = SimpleRAG()
    rag.add_text("alice doc", source="x.txt", owner="alice")
    rag.add_text("bob doc", source="x.txt", owner="bob")

    assert rag.delete_document("x.txt", owner="alice") == 1
    assert rag.has_document("x.txt", owner="bob"), "deleting alice's copy removed bob's"


def test_stats_are_scoped_to_one_owner():
    rag = SimpleRAG()
    rag.add_text("a" * 600, source="a.txt", owner="alice")
    rag.add_text("b", source="b.txt", owner="bob")

    assert rag.get_stats(owner="alice")["total_chunks"] == 2
    assert rag.get_stats(owner="bob")["total_chunks"] == 1


# ------------------------------------------- QueryDecomposer: LATENT bugs ----

class _CountingLLM:
    def __init__(self):
        self.calls = []

    def complete(self, system_prompt="", user_prompt="", max_tokens=0):
        self.calls.append(user_prompt)
        return "an answer"


def test_a_single_topic_query_costs_one_model_call():
    """The baseline: no complex indicator, no decomposition."""
    llm = _CountingLLM()
    result = QueryDecomposer(llm_client=llm).answer("What is the capital of France?")
    assert len(llm.calls) == 1
    assert result["sub_questions"] == ["What is the capital of France?"]


def test_a_compound_query_is_split_and_costs_one_call_per_part():
    llm = _CountingLLM()
    result = QueryDecomposer(llm_client=llm).answer(
        "Compare the pricing and refund policy")
    assert len(result["sub_questions"]) == 2
    assert len(llm.calls) == 2, "each sub-question is a separate model call"


def test_the_split_ignores_whether_the_two_clauses_are_related():
    """LATENT BUG, measured. `decompose` triggers on any complex indicator
    (`better` here) and then splits on " and " whether or not that "and"
    connects two topics. Result: a second model call answering a non-question.

    Not reachable from the app today: `QueryDecomposer` is only used by
    `OptimizedRAG.query()`, which has no caller. Pinned so that wiring it up
    later is a deliberate act, not an accident.
    """
    llm = _CountingLLM()
    result = QueryDecomposer(llm_client=llm).answer(
        "Is it better to call the API and then wait?")

    assert result["sub_questions"] == [
        "What is the is it better to call the api?",
        "What is the then wait?",
    ], "the split changed - if it now keeps the sentence intact, invert this test"
    assert len(llm.calls) == 2


def test_the_sub_question_template_produces_a_double_article():
    """LATENT BUG, measured: `f"What is the {fragment}?"` is applied to a
    fragment that already begins with "the"."""
    subs = QueryDecomposer(llm_client=_CountingLLM()).decompose(
        "Compare the pricing and refund policy")
    assert subs[0] == "What is the the pricing?", (
        f"the double-article bug is fixed - invert this test. got {subs[0]!r}"
    )


def test_the_garbled_sub_questions_are_shown_to_the_user():
    """LATENT BUG, measured end to end: `_combine_answers` renders every
    sub-question into the answer text. So the user would read
    "**2. What is the then wait?**" in their reply."""
    d = QueryDecomposer(llm_client=_CountingLLM())
    combined = d.answer("Is it better to call the API and then wait?")["combined_answer"]

    assert "What is the then wait?" in combined, (
        "the answer no longer contains the sub-questions - the shape changed, "
        "re-check this test"
    )


def test_the_decomposer_answers_every_sub_question_it_creates():
    """One model call per sub-question and one answer per sub-question - if
    these two lengths ever differ, `zip()` in `_combine_answers` would silently
    drop answers."""
    llm = _CountingLLM()
    result = QueryDecomposer(llm_client=llm).answer("Compare cats and dogs")
    assert len(result["sub_answers"]) == len(result["sub_questions"]) == len(llm.calls)


# ---------------------------------- SubQuestionEngine: dead code, same shape --

def test_the_dead_engine_decomposes_any_query_containing_and():
    """`SubQuestionEngine` (advanced_rag) is unused. Its `should_decompose`
    returns True for any query containing " and " - the `len(parts) >= 2` check
    is always true once the split happens, and the comment promising "if parts
    seem like different topics" has no code behind it.

    Pinned, not fixed: it is dead code, and changing it would be a change with no
    observable effect.
    """
    engine = SubQuestionEngine()
    assert engine.should_decompose("What is 2 and 2?") is True
    assert engine.should_decompose("cats and dogs") is True
    assert engine.should_decompose("What is the capital of France?") is False
