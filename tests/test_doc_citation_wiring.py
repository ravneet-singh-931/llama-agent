"""The citation check, tested where it actually runs.

``modules/citations.py`` has its own tests in test_reliability_batch.py, and
they were the only ones. The functions that *call* it — ``_check_doc_citations``
and the two note constants in ``api.py`` — had none, which is the same
"tested the module, never the wiring" mistake that has shipped bugs in this
repo repeatedly. These tests pin the wiring: which note appears for which
failure, that a clean answer is untouched, and that nothing can break an
answer.
"""

import pytest


@pytest.fixture
def api_mod():
    import api
    return api


def _context(*chunks):
    """Build the '--- Source: file ---' block the answer is checked against."""
    return "\n\n".join(
        f"--- Source: {name} (chunk {idx}) ---\n{text}"
        for idx, (name, text) in enumerate(chunks, start=1))


# --------------------------------------------------------------------- happy

def test_a_correctly_cited_answer_is_returned_unchanged(api_mod):
    answer = "Revenue grew 20% (report.pdf, chunk 1)."
    ctx = _context(("report.pdf", "Revenue grew 20% year over year."))
    assert api_mod._check_doc_citations(answer, ctx) == answer


def test_prose_without_any_citation_is_reported_unsourced(api_mod):
    """The prompt asks for a citation per claim, so silence is a finding.

    This is deliberate (see check_citations' docstring): an answer about
    documents that cites nothing cannot be traced, and quietly passing it is
    how an unsourced paragraph reaches a reader looking rigorous.
    """
    answer = "The document describes the company's approach."
    ctx = _context(("notes.pdf", "We take a careful approach to the problem."))
    assert api_mod._DOC_UNSOURCED_NOTE in api_mod._check_doc_citations(answer, ctx)


def test_empty_context_never_produces_a_note(api_mod):
    # No excerpts means no citation could possibly be valid; flagging every
    # answer here would fire on every non-document question.
    assert api_mod._check_doc_citations("Revenue grew 20%.", "") == "Revenue grew 20%."


# ------------------------------------------------------------------- failures

def test_a_cited_file_that_was_never_retrieved_is_flagged(api_mod):
    answer = "Revenue grew 20% (q4-marketing-deck.pdf, chunk 3)."
    ctx = _context(("report.pdf", "Revenue grew 20% year over year."))
    out = api_mod._check_doc_citations(answer, ctx)
    assert out.startswith(answer), "the original answer must survive"
    assert api_mod._DOC_BAD_CITATION_NOTE.format(
        bad="q4-marketing-deck.pdf (chunk 3)") in out


def test_a_real_file_with_the_wrong_chunk_is_flagged(api_mod):
    answer = "Revenue grew 20% (report.pdf, chunk 9)."
    ctx = _context(("report.pdf", "Revenue grew 20% year over year."))
    out = api_mod._check_doc_citations(answer, ctx)
    assert api_mod._DOC_BAD_CITATION_NOTE.format(bad="report.pdf (chunk 9)") in out


def test_claims_without_any_citation_get_the_unsourced_note(api_mod):
    answer = "Revenue grew 20% and costs fell 15%."
    ctx = _context(("report.pdf", "Revenue grew 20% year over year. Costs 15% lower."))
    out = api_mod._check_doc_citations(answer, ctx)
    assert api_mod._DOC_UNSOURCED_NOTE in out


# ------------------------------------------------------------------- safety

def test_a_broken_checker_never_breaks_the_answer(api_mod, monkeypatch):
    """A detector must never be able to take down an answer."""
    import modules.citations as citations

    def boom(*a, **k):
        raise RuntimeError("citation checker exploded")

    monkeypatch.setattr(citations, "check_citations", boom)
    answer = "Revenue grew 20% (report.pdf, chunk 1)."
    assert api_mod._check_doc_citations(answer, _context(("report.pdf", "x"))) == answer


def test_the_failure_is_counted_for_the_metric(api_mod, monkeypatch):
    """The note is visible; the counter makes it queryable across runs."""
    import modules.prometheus_metrics as pm

    seen = []
    monkeypatch.setattr(pm, "record_citation_failure", lambda kind: seen.append(kind))

    answer = "Revenue grew 20% (q4-marketing-deck.pdf, chunk 3)."
    api_mod._check_doc_citations(answer, _context(("report.pdf", "Revenue grew 20%.")))
    assert seen == ["invented_file"]


def test_an_uncounted_failure_does_not_break_the_note(api_mod, monkeypatch):
    """If the metric import fails the reader still gets the warning."""
    import modules.prometheus_metrics as pm

    def boom(_kind):
        raise RuntimeError("metrics down")

    monkeypatch.setattr(pm, "record_citation_failure", boom)

    answer = "Revenue grew 20% (q4-marketing-deck.pdf, chunk 3)."
    out = api_mod._check_doc_citations(answer, _context(("report.pdf", "Revenue grew 20%.")))
    assert api_mod._DOC_BAD_CITATION_NOTE.format(
        bad="q4-marketing-deck.pdf (chunk 3)") in out


# --------------------------------------------------------------- the constants

def test_note_constants_are_actually_formatted(api_mod):
    """_DOC_BAD_CITATION_NOTE is a format string; an unformatted one would
    reach the user with a literal {bad} in it."""
    rendered = api_mod._DOC_BAD_CITATION_NOTE.format(bad="file.pdf (chunk 1)")
    assert "{bad}" not in rendered and "file.pdf (chunk 1)" in rendered
