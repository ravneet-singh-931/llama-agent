"""ONNX cross-encoder reranker (no PyTorch).

- plumbing: real tokenizers + a stand-in session (always runs);
- a real onnxruntime session on a tiny generated graph (needs ``onnx``);
- the REAL ms-marco model (CI downloads it: RERANKER_TEST_MODEL_DIR).
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from modules.rag_optimizations import CrossEncoderReranker, OnnxCrossEncoder  # noqa: E402

pytest.importorskip("onnxruntime")
pytest.importorskip("tokenizers")


def _write_tokenizer(path):
    """A tiny BERT-style tokenizer (WordLevel, [CLS] A [SEP] B [SEP])."""
    from tokenizers import Tokenizer, models, pre_tokenizers, processors

    words = ("what is the refund policy pricing plans start at contact support "
             "for help days refunds within capital of france paris bananas").split()
    vocab = {"[PAD]": 0, "[UNK]": 1, "[CLS]": 2, "[SEP]": 3}
    for w in words:
        vocab.setdefault(w, len(vocab))
    tok = Tokenizer(models.WordLevel(vocab=vocab, unk_token="[UNK]"))
    tok.pre_tokenizer = pre_tokenizers.Whitespace()
    tok.post_processor = processors.TemplateProcessing(
        single="[CLS] $A [SEP]", pair="[CLS] $A [SEP] $B:1 [SEP]:1",
        special_tokens=[("[CLS]", 2), ("[SEP]", 3)])
    tok.save(os.path.join(path, "tokenizer.json"))
    return vocab


class _Input:
    def __init__(self, name):
        self.name = name


class _FakeSession:
    """Scores a pair by how many passage tokens (type 1) also occur in the query."""

    calls = []

    def __init__(self, path, sess_options=None, providers=None):
        self.path = path

    def get_inputs(self):
        return [_Input("input_ids"), _Input("attention_mask"), _Input("token_type_ids")]

    def run(self, _outputs, feeds):
        import numpy as np
        _FakeSession.calls.append(feeds)
        ids, mask, types = feeds["input_ids"], feeds["attention_mask"], feeds["token_type_ids"]
        logits = []
        for row_ids, row_mask, row_types in zip(ids, mask, types):
            q = {i for i, t, m in zip(row_ids, row_types, row_mask) if m and t == 0 and i > 3}
            p = [i for i, t, m in zip(row_ids, row_types, row_mask) if m and t == 1 and i > 3]
            logits.append([float(sum(1 for i in p if i in q)) - 1.0])
        return [np.asarray(logits, dtype=np.float32)]


@pytest.fixture
def fake_model_dir(tmp_path, monkeypatch):
    import onnxruntime
    _write_tokenizer(str(tmp_path))
    (tmp_path / "model_quantized.onnx").write_bytes(b"stand-in")
    _FakeSession.calls = []
    monkeypatch.setattr(onnxruntime, "InferenceSession", _FakeSession)
    return str(tmp_path)


def test_feeds_are_int64_padded_pairs_and_scores_are_probabilities(fake_model_dir):
    enc = OnnxCrossEncoder(fake_model_dir, batch_size=2)
    q = "what is the refund policy"
    scores = enc.predict([(q, "refunds within 30 days"),
                          (q, "pricing plans start at 9"),
                          (q, "the refund policy")])
    assert len(scores) == 3 and all(0.0 < s < 1.0 for s in scores)
    assert scores[2] > scores[0] and scores[2] > scores[1]
    # batch_size=2 -> two session runs; every feed int64, rectangular (padded)
    assert len(_FakeSession.calls) == 2
    feeds = _FakeSession.calls[0]
    assert set(feeds) == {"input_ids", "attention_mask", "token_type_ids"}
    for arr in feeds.values():
        assert str(arr.dtype) == "int64" and arr.ndim == 2
    # the passage half of a pair is marked with token type 1
    assert feeds["token_type_ids"].max() == 1


def test_only_inputs_the_model_declares_are_fed(fake_model_dir, monkeypatch):
    monkeypatch.setattr(_FakeSession, "get_inputs",
                        lambda self: [_Input("input_ids"), _Input("attention_mask"),
                                      _Input("token_type_ids")][:2])
    monkeypatch.setattr(_FakeSession, "run", lambda self, o, feeds: (
        _FakeSession.calls.append(feeds) or [__import__("numpy").zeros((len(feeds["input_ids"]), 1))]))
    OnnxCrossEncoder(fake_model_dir).predict([("a", "b")])
    assert set(_FakeSession.calls[-1]) == {"input_ids", "attention_mask"}


def test_reranker_uses_onnx_backend_and_orders_by_relevance(fake_model_dir):
    rr = CrossEncoderReranker(model_dir=fake_model_dir)
    results = [
        {"content": "pricing plans start at 9", "similarity": 0.9},
        {"content": "contact support for help", "similarity": 0.8},
        # keyword-only hit: no vector similarity at all, but the best answer
        {"content": "the refund policy refunds within 30 days", "similarity": 0.0},
    ]
    out = rr.rerank("what is the refund policy", results, top_k=2)
    assert rr.describe().startswith("onnx")
    assert out[0]["content"].startswith("the refund policy")
    assert len(out) == 2 and "rerank_score" in out[0]


def test_missing_model_falls_back_to_keyword(tmp_path):
    rr = CrossEncoderReranker(model_dir=str(tmp_path))  # empty dir
    out = rr.rerank("refund policy", [{"content": "refund policy: 30 days", "similarity": 0.5}])
    assert rr.backend.startswith("keyword") and len(out) == 1


def test_no_model_dir_configured_falls_back(monkeypatch):
    monkeypatch.delenv("RERANKER_MODEL_DIR", raising=False)
    assert CrossEncoderReranker().describe().startswith(("keyword", "sentence-transformers"))


def test_real_onnxruntime_session_on_a_tiny_graph(tmp_path):
    """Exercises real onnxruntime with int64 BERT-style inputs (needs `onnx`)."""
    onnx = pytest.importorskip("onnx")
    from onnx import TensorProto, helper

    _write_tokenizer(str(tmp_path))
    ins = [helper.make_tensor_value_info(n, TensorProto.INT64, ["b", "s"])
           for n in ("input_ids", "attention_mask", "token_type_ids")]
    out = helper.make_tensor_value_info("logits", TensorProto.FLOAT, ["b", 1])
    nodes = [  # logit = sum(token_type_ids * attention_mask) - 3
        helper.make_node("Mul", ["token_type_ids", "attention_mask"], ["m"]),
        helper.make_node("ReduceSum", ["m", "axes"], ["s"], keepdims=1),
        helper.make_node("Cast", ["s"], ["f"], to=TensorProto.FLOAT),
        helper.make_node("Sub", ["f", "three"], ["logits"]),
    ]
    inits = [helper.make_tensor("axes", TensorProto.INT64, [1], [1]),
             helper.make_tensor("three", TensorProto.FLOAT, [], [3.0])]
    graph = helper.make_graph(nodes, "tiny", ins, [out], inits)
    model = helper.make_model(graph, opset_imports=[helper.make_opsetid("", 13)])
    model.ir_version = 8
    onnx.save(model, str(tmp_path / "model.onnx"))

    scores = OnnxCrossEncoder(str(tmp_path)).predict(
        [("what is", "refund policy"), ("what is", "the refund policy within 30 days")])
    # longer passage -> more type-1 tokens -> higher logit
    assert scores[1] > scores[0]


_REAL = os.getenv("RERANKER_TEST_MODEL_DIR", "")


@pytest.mark.skipif(not _REAL, reason="real model not downloaded (set RERANKER_TEST_MODEL_DIR)")
@pytest.mark.parametrize("query,relevant,distractors", [
    ("What is the capital of France?",
     "Paris is the capital and largest city of France.",
     ["Bananas are a yellow fruit rich in potassium.",
      "France is known for its wine and cheese."]),
    ("How is the Rule of 40 calculated?",
     "Rule of 40 = revenue growth % + free cash flow margin %. Above 40 is considered healthy.",
     ["Net revenue retention measures expansion revenue from existing customers.",
      "The company was founded in 2009 and has 40 offices worldwide."]),
    ("What is the refund window?",
     "Customers can request a full refund within 30 days of purchase.",
     ["Our pricing plans start at $9 per month.",
      "Contact support via email for help with your window installation."]),
    ("Why do SaaS valuation multiples compress?",
     "Multiples compress when the market lowers its expectations for future growth, "
     "even if current metrics stay stable.",
     ["SaaS stands for software as a service.",
      "Valuation reports are published every quarter by the finance team."]),
])
def test_real_model_ranks_the_relevant_passage_first(query, relevant, distractors):
    rr = CrossEncoderReranker(model_dir=_REAL)
    # the relevant passage gets the LOWEST vector similarity on purpose
    results = [{"content": d, "similarity": 0.9} for d in distractors]
    results.append({"content": relevant, "similarity": 0.1})
    out = rr.rerank(query, results, top_k=3)
    assert rr.backend.startswith("onnx"), rr.backend
    assert out[0]["content"] == relevant, [(r["content"][:40], r["rerank_score"]) for r in out]
