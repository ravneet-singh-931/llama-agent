"""v3.5.5: the ONNX reranker scores in small batches. A batch of 16 long
pairs reserved ~940 MB per API pod, and the next upload OOM-killed it."""
import inspect
import os

import pytest

from modules.rag_optimizations import OnnxCrossEncoder

MODEL_DIR = os.environ.get("RERANKER_MODEL_DIR", "/opt/models/reranker")


def test_default_batch_is_small():
    default = inspect.signature(OnnxCrossEncoder.__init__).parameters["batch_size"].default
    assert default <= 4


@pytest.mark.skipif(not os.path.isdir(MODEL_DIR), reason="reranker model not downloaded")
def test_small_batches_give_the_same_scores():
    q = "How do I reset my password?"
    docs = [
        "To reset your password, open Settings and choose Reset password.",
        "Bananas are rich in potassium.",
        "Password resets send a one-time link to your email. " * 30,
        "The cafeteria opens at 8 am.",
        "Security policy: passwords must be at least 12 characters. " * 10,
        "Reset links expire after 15 minutes.",
        "Our office is in Delhi.",
    ]
    pairs = [(q, d) for d in docs]
    big = OnnxCrossEncoder(MODEL_DIR, batch_size=16).predict(pairs)
    small = OnnxCrossEncoder(MODEL_DIR, batch_size=4).predict(pairs)
    # The model is dynamically quantized: activation scales are computed per batch,
    # so a different batch size moves scores by ~1e-3 (measured 0.00105). What
    # matters for retrieval is the order, checked below (and the RAG eval is unchanged).
    assert max(abs(a - b) for a, b in zip(big, small)) < 1e-2
    rank = lambda s: sorted(range(len(s)), key=lambda i: -s[i])
    assert rank(big) == rank(small)
