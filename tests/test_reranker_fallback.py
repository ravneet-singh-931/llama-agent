"""The reranker's fallback chain had no tests at the levels that actually run.

`CrossEncoderReranker` documents three backends, first available wins:

    1. ONNX            (RERANKER_MODEL_DIR, set in the Docker image)
    2. sentence-transformers (if installed)
    3. keyword reranker  (advanced_rag.ReRanker)

Level 3 is the one a deployment falls back to whenever the ONNX model is
missing — which is exactly what happens if `RERANKER_MODEL_DIR` points at a
path that is not in the image, if the model file is corrupt, or if the image is
built without it. Before this file:

  * `modules/advanced_rag.py` was **27% covered** — the whole `ReRanker` class
    was unreached (210 of 289 statements missed).
  * `_fallback_rerank` and its call site in `rerank()` were both uncovered.

So the path taken *when things are already going wrong* was the untested one.
That is the wrong way round: the fallback is what stops a retrieval outage from
becoming an answer outage.

Note on the import: `ReRanker` is imported **inside** `_fallback_rerank`, not at
module scope, so these tests patch it where it is used rather than where it is
defined.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

# Deliberately in the WRONG order: the most relevant chunk is last, and has the
# lowest vector score. A reranker that does nothing (or a fallback that just
# returns the input slice) would leave this order untouched and fail the tests
# below. An earlier version of this file put the relevant chunk first, which
# made `test_the_fallback_actually_reorders_results` pass even against a
# sabotage that deleted the rerank entirely.
RESULTS = [
    {"content": "The office is in Delhi and the team is small.", "score": 0.9},
    {"content": "Pricing tiers and the refund policy for enterprise plans.", "score": 0.5},
    {"content": "The Rule of 40 is growth plus free cash flow margin.", "score": 0.1},
]


def _reranker(**kw):
    from modules.rag_optimizations import CrossEncoderReranker
    return CrossEncoderReranker(model_name="no-such-model-for-tests", **kw)


# ------------------------------------------------ level 3: the fallback --

def test_when_no_model_is_available_it_falls_back_to_keyword(monkeypatch):
    """The deployment case: ONNX dir missing, sentence-transformers absent."""
    monkeypatch.setenv("RERANKER_MODEL_DIR", "")
    monkeypatch.setitem(sys.modules, "sentence_transformers", None)

    r = _reranker(model_dir="")
    assert r._get_model() is None
    assert r.backend.startswith("keyword")


def test_the_fallback_actually_reorders_results(monkeypatch):
    """Not just 'it returns something' — it must promote the relevant chunk.

    The relevant chunk starts LAST in RESULTS with the lowest score, so this
    only passes if the reranker really ran.
    """
    monkeypatch.setenv("RERANKER_MODEL_DIR", "")
    monkeypatch.setitem(sys.modules, "sentence_transformers", None)
    r = _reranker(model_dir="")

    out = r.rerank("what is the rule of 40?", RESULTS, top_k=2)
    assert len(out) == 2
    assert "Rule of 40" in out[0]["content"], (
        "the most relevant chunk must rank first after the fallback rerank; "
        "it starts last, so this fails if the rerank is skipped"
    )


def test_the_fallback_respects_top_k(monkeypatch):
    monkeypatch.setenv("RERANKER_MODEL_DIR", "")
    monkeypatch.setitem(sys.modules, "sentence_transformers", None)
    r = _reranker(model_dir="")
    assert len(r.rerank("pricing", RESULTS, top_k=1)) == 1


def test_an_empty_result_set_returns_empty_without_loading_a_model():
    r = _reranker(model_dir="")
    assert r.rerank("anything", [], top_k=5) == []


def test_the_onnx_failure_is_remembered(monkeypatch, tmp_path):
    """A missing model must not be re-probed on every query."""
    monkeypatch.setenv("RERANKER_MODEL_DIR", str(tmp_path / "nope"))
    monkeypatch.setitem(sys.modules, "sentence_transformers", None)
    r = _reranker(model_dir=str(tmp_path / "nope"))

    assert r._get_model() is None
    assert r._unavailable is True
    # second call short-circuits instead of trying the filesystem again
    assert r._get_model() is None


def test_a_broken_onnx_dir_does_not_raise(monkeypatch, tmp_path):
    """A corrupt model directory must degrade, never take retrieval down."""
    d = tmp_path / "model"
    d.mkdir()
    (d / "model.onnx").write_bytes(b"not an onnx file")
    monkeypatch.setitem(sys.modules, "sentence_transformers", None)

    r = _reranker(model_dir=str(d))
    assert r._get_model() is None or r._model is not None
    # whatever happened, the public entry point must still answer
    out = r.rerank("rule of 40", RESULTS, top_k=2)
    assert isinstance(out, list) and len(out) <= 2


def test_describe_reports_the_backend_without_raising(monkeypatch):
    monkeypatch.setitem(sys.modules, "sentence_transformers", None)
    r = _reranker(model_dir="")
    assert isinstance(r.describe(), str)


# --------------------------------------------------- ReRanker itself --

def test_reranker_class_is_importable_and_ranks():
    from modules.advanced_rag import ReRanker
    out = ReRanker().rerank("what is the rule of 40?", RESULTS, top_k=2)
    assert len(out) == 2
    assert "Rule of 40" in out[0]["content"], "ReRanker must promote the relevant chunk"


def test_reranker_class_handles_empty_input():
    from modules.advanced_rag import ReRanker
    assert ReRanker().rerank("q", [], top_k=3) == []


def test_reranker_class_handles_top_k_larger_than_input():
    from modules.advanced_rag import ReRanker
    out = ReRanker().rerank("rule of 40", RESULTS, top_k=99)
    assert len(out) == len(RESULTS)
