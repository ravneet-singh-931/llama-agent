"""Tests for the RAG eval harness (evals/rag_eval.py).

These check the harness itself - dataset labels, scoring, isolation check,
answer checks - with a small hashing embedding, so they run offline. The
real retrieval numbers come from `python -m evals.rag_eval retrieval` (CI
step and Docker image), which uses ChromaDB's real embedding model.
"""

import glob
import hashlib
import json
import os
import re
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

pytest.importorskip("chromadb")
np = pytest.importorskip("numpy")

from chromadb.api.types import EmbeddingFunction  # noqa: E402

from evals import rag_eval  # noqa: E402


class HashEmbedding(EmbeddingFunction):
    """Deterministic bag-of-words hashing embedding (offline stand-in)."""

    def __init__(self):
        pass

    def __call__(self, input):
        out = []
        for text in input:
            v = np.zeros(512, dtype=np.float32)
            for w in text.lower().split():
                v[int(hashlib.md5(w.strip('.,?!:;()"').encode()).hexdigest(), 16) % 512] += 1
            n = np.linalg.norm(v)
            out.append((v / n if n else v).tolist())
        return out

    @staticmethod
    def name():
        return "hash-bow-test"

    def get_config(self):
        return {}

    @staticmethod
    def build_from_config(config):
        return HashEmbedding()


@pytest.fixture(scope="module")
def index():
    return rag_eval.Index(embedding_function=HashEmbedding())


QUESTIONS = rag_eval.load_questions()


# ------------------------------------------------------------------ dataset

def test_dataset_shape():
    ids = [q["id"] for q in QUESTIONS]
    assert len(ids) == len(set(ids)), "duplicate question ids"
    files = {os.path.basename(p) for p in rag_eval.CORPUS}
    for q in QUESTIONS:
        assert q["type"] in ("keyword", "paraphrase", "unanswerable"), q["id"]
        if q["type"] == "unanswerable":
            assert q["file"] is None and not q["relevant"], q["id"]
        else:
            assert q["file"] in files and q["relevant"] and q["answer_any"], q["id"]


def test_each_label_is_unique_to_its_file():
    """A labelled passage must appear exactly once in the whole corpus, in the
    question's own file - otherwise a 'hit' could be a different document."""
    docs = {os.path.basename(p): open(p, encoding="utf-8").read()
            for p in glob.glob(os.path.join(rag_eval.HERE, "corpus*", "*.txt"))}
    for q in QUESTIONS:
        for passage in q["relevant"]:
            found = [(f, d.count(passage)) for f, d in docs.items() if passage in d]
            assert found == [(q["file"], 1)], (q["id"], passage, found)


def test_unanswerable_facts_are_not_in_the_user_corpus():
    user_text = " ".join(open(p, encoding="utf-8").read() for p in rag_eval.CORPUS)
    for q in QUESTIONS:
        for bad in q.get("forbidden", []):
            assert bad not in user_text, (q["id"], bad)


# ------------------------------------------------------------------ scoring

def test_scores_math():
    s = rag_eval._scores([1, 2, None, 4])
    assert s == {"n": 4, "hit@1": 0.25, "hit@3": 0.5, "hit@6": 0.75,
                 "mrr": round((1 + 0.5 + 0.25) / 4, 3)}


def test_is_relevant_needs_file_and_passage():
    q = {"file": "a.txt", "relevant": ["up to  three days"]}
    assert rag_eval.is_relevant({"file": "a.txt", "content": "Work Up to three\ndays per week"}, q)
    assert not rag_eval.is_relevant({"file": "b.txt", "content": "up to three days"}, q)
    assert not rag_eval.is_relevant({"file": "a.txt", "content": "up to two days"}, q)


def test_first_hit_rank_only_counts_top_k():
    q = {"file": "a.txt", "relevant": ["x"]}
    ranked = [{"file": "a.txt", "content": "no"}] * 6 + [{"file": "a.txt", "content": "x"}]
    assert rag_eval.first_hit_rank(ranked, q) is None
    assert rag_eval.first_hit_rank(ranked[5:], q) == 2


# ------------------------------------------------------------------ harness end to end

def test_all_labels_survive_chunking(index):
    assert rag_eval.unreachable_labels(index, QUESTIONS) == []


def test_retrieval_report_and_no_cross_user_leak(index):
    rep = rag_eval.run_retrieval(index, QUESTIONS)
    assert rep["leaks"] == []
    assert rep["questions"] == sum(1 for q in QUESTIONS if q["relevant"])
    assert rep["chunks"] > 4 * rag_eval.K  # top-6 must not cover most of the corpus
    assert list(rep["setups"]) == ["vector", "hybrid", "hybrid+rerank (deployed)"]
    for s in rep["setups"].values():
        for key in ("hit@1", "hit@3", "hit@6", "mrr"):
            assert 0.0 <= s[key] <= 1.0
        assert s["hit@1"] <= s["hit@3"] <= s["hit@6"]
    assert rag_eval.retrieval_ok(rep)
    assert "Cross-user leaks: **0**" in rag_eval.render_retrieval(rep)


def test_leak_fails_the_run(index):
    rep = {"setups": {"x": {"hit@6": 1.0}}, "unreachable": [],
           "leaks": [{"setup": "x", "question": "q", "file": "bluefin_memo.txt"}]}
    assert not rag_eval.retrieval_ok(rep)
    assert index.leaked({"file": "bluefin_memo.txt", "owner": index.owner})
    assert index.leaked({"file": "helio_handbook.txt", "owner": index.other})
    assert not index.leaked({"file": "helio_handbook.txt", "owner": index.owner})


def test_floor_fails_the_run():
    rep = {"setups": {"x": {"hit@6": 0.5}}, "unreachable": [], "leaks": []}
    assert rag_eval.retrieval_ok(rep, 0.5) and not rag_eval.retrieval_ok(rep, 0.6)


def test_deployed_ranking_matches_api_context(index):
    """The eval ranks exactly the excerpts api._retrieve_context sends to the model."""
    import api
    q = "What does error OS-2040 mean?"
    ranked = rag_eval.deployed_ranking(index.user_rag, q)
    context = api._retrieve_context(index.user_rag, q)
    shown = re.findall(r"--- Source: (.+?) \(chunk (\d+)\) ---", context)
    assert shown == [(c["file"], str(c["chunk_index"])) for c in ranked]


# ------------------------------------------------------------------ answer checks

def _q(qid):
    return next(q for q in QUESTIONS if q["id"] == qid)


def test_answer_check_answerable():
    q = _q("api-rate-team")
    good = rag_eval.check_answer(q, "The Team plan allows 600 requests per minute "
                                    "(orbit_api_spec.txt, chunk 1).", ["600 requests"])
    assert good["pass"] and good["correct"] and good["cites_file"] and not good["refused"]
    bad = rag_eval.check_answer(q, "The documents don't contain the Team plan's rate limit.", [])
    assert not bad["pass"] and bad["refused"]


def test_answer_check_unanswerable():
    q = _q("no-bluefin")
    refused = rag_eval.check_answer(q, "Your documents don't mention Project Bluefin or "
                                       "any acquisition price.", [])
    assert refused["pass"]
    leaked = rag_eval.check_answer(q, "The acquisition price is $310 million.", [])
    assert not leaked["pass"] and leaked["forbidden"] == ["310"]


@pytest.mark.parametrize("text,expected", [
    ("Your documents don't mention Project Bluefin.", True),
    ("The documents do not contain any information about a 401(k) match.", True),
    ("I couldn't find a dress code in the handbook.", True),
    ("A GraphQL endpoint is not mentioned in the specification.", True),
    ("There is no information about new stores in 2027.", True),
    ("The Team plan allows 600 requests per minute.", False),
    ("Remote work is allowed for up to three days per week; it does not require HR approval.", False),
])
def test_says_not_found(text, expected):
    assert rag_eval.says_not_found(text) is expected


def test_answer_summary_rates():
    rows = [{"type": "keyword", "latency_s": 1.0, "cache_hit": False, "usage": {},
             "checks": {"pass": True, "correct": True, "cites_file": True, "refused": False,
                        "error": False, "unverified": False, "grounding": 1.0}},
            {"type": "unanswerable", "latency_s": 3.0, "cache_hit": True, "usage": {},
             "checks": {"pass": False, "refused": False, "forbidden": [],
                        "error": False, "unverified": True, "grounding": 0.5}}]
    s = rag_eval.summarize_answers(rows)
    assert s["pass_rate"] == 0.5 and s["answer_accuracy"] == 1.0
    assert s["correct_refusal_rate"] == 0.0 and s["unverified_rate"] == 0.5
    assert s["cache_served"] == 1 and s["grounding_rate"] == 0.75
    assert "RAG answer eval" in rag_eval.render_answers({"summary": s, "results": [
        {**rows[0], "id": "a"}, {**rows[1], "id": "b"}]})


def test_questions_file_is_valid_jsonl():
    with open(rag_eval.QUESTIONS, encoding="utf-8") as f:
        for line in f:
            if line.strip() and not line.lstrip().startswith("//"):
                json.loads(line)


def test_run_answers_plumbing(index, monkeypatch):
    """answers mode end to end with the LLM answer stubbed (no Groq call)."""
    import api
    monkeypatch.setattr(api, "answer_from_documents",
                        lambda q, rag: "The Team plan allows 600 requests per minute "
                                       "(orbit_api_spec.txt, chunk 1).")
    qs = [_q("api-rate-team"), _q("no-graphql")]
    rep = rag_eval.run_answers(index, qs, pause=0)
    by_id = {r["id"]: r for r in rep["results"]}
    assert by_id["api-rate-team"]["checks"]["pass"]
    assert not by_id["no-graphql"]["checks"]["pass"]  # answered instead of "not found"
    assert rep["summary"]["questions"] == 2 and rep["summary"]["pass_rate"] == 0.5
