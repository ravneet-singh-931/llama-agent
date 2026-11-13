"""
RAG evaluation - measures document retrieval and document answers on a fixed,
committed sample corpus (evals/rag/), through the same code users hit.

    # Retrieval (no LLM, free, deterministic - also runs in CI)
    python -m evals.rag_eval retrieval

    # Answers (Groq; on demand, inside the API pod)
    kubectl -n llama-agent exec deploy/llama-agent-api -- \\
        python -m evals.rag_eval answers --out /tmp/rag-answers.json

Everything is indexed into a throw-away in-memory ChromaDB, so the shared
document store is never touched.

Retrieval compares three setups on the 6 excerpts the answer model sees
(api._retrieve_context): vector search only, hybrid (BM25 + vector), and
hybrid + reranker (what is deployed). It reports hit@1/3/6 and MRR, split
by question type, plus per-query latency. A second user's document is
indexed too: any chunk of it in the first user's results is a leak and
fails the run.

Answers runs api.answer_from_documents (retrieval, answer, Corrective-RAG
check, retry) and checks each answer deterministically: expected fact
present, source file cited, "not found" for unanswerable questions, figures
found in the documents, CRAG "unverified" note, served from the semantic
cache.
"""

import argparse
import glob
import json
import os
import re
import statistics
import sys
import tempfile
import time
import uuid
from collections import defaultdict
from typing import Callable, Dict, List, Optional

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

HERE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "rag")
QUESTIONS = os.path.join(HERE, "questions.jsonl")
CORPUS = sorted(glob.glob(os.path.join(HERE, "corpus", "*.txt")))
OTHER_CORPUS = sorted(glob.glob(os.path.join(HERE, "corpus_other_user", "*.txt")))
K = 6  # excerpts the answer model sees (api._retrieve_context top_k)


class NullLLM:
    """No LLM: chunks are indexed without contextual summaries."""

    def complete(self, system_prompt: str, user_prompt: str, max_tokens: int = 500) -> str:
        return ""


def load_questions(path: str = QUESTIONS) -> List[Dict]:
    with open(path, encoding="utf-8") as f:
        return [json.loads(line) for line in f
                if line.strip() and not line.lstrip().startswith("//")]


def _norm(text: str) -> str:
    return " ".join((text or "").lower().split())


def is_relevant(chunk: Dict, question: Dict) -> bool:
    """A chunk is relevant when it comes from the question's file and
    contains every labelled passage."""
    if not question.get("file") or chunk.get("file") != question["file"]:
        return False
    content = _norm(chunk.get("content", ""))
    return all(_norm(r) in content for r in question.get("relevant", []))


# ------------------------------------------------------------------ index

class Index:
    """In-memory ChromaDB with the sample corpus for `owner` and the other
    user's document for `other`."""

    def __init__(self, embedding_function=None, llm=None):
        import chromadb
        from chromadb.config import Settings
        from modules.doc_scope import scoped
        from modules.rag_optimizations import OptimizedRAG
        from modules.vector_store import VectorStore

        run = uuid.uuid4().hex[:8]
        self.owner, self.other = f"rag-eval-{run}", f"rag-eval-other-{run}"
        client = chromadb.EphemeralClient(settings=Settings(anonymized_telemetry=False))
        for name in ("documents", "query_cache"):
            try:
                client.delete_collection(name)  # ephemeral clients share state per process
            except Exception:
                pass
        self.store = VectorStore(client=client)
        if embedding_function is not None:
            client.delete_collection("documents")
            self.store.collection = client.get_or_create_collection(
                "documents", embedding_function=embedding_function,
                metadata={"hnsw:space": "cosine"})
        self.rag = OptimizedRAG(self.store, llm_client=llm or NullLLM())
        self.add_results = [self.rag.add_document(p, owner=self.owner) for p in CORPUS]
        self.add_results += [self.rag.add_document(p, owner=self.other) for p in OTHER_CORPUS]
        self.user_rag = scoped(self.rag, self.owner)
        self.other_files = {os.path.basename(p) for p in OTHER_CORPUS}

    def owner_chunks(self) -> List[Dict]:
        data = self.store.collection.get(where={"owner": self.owner},
                                         include=["documents", "metadatas"])
        return [{"content": d, **(m or {})}
                for d, m in zip(data.get("documents") or [], data.get("metadatas") or [])]

    def leaked(self, chunk: Dict) -> bool:
        return (chunk.get("file") in self.other_files
                or (chunk.get("owner") or self.owner) != self.owner)


# ------------------------------------------------------------------ retrieval

def deployed_ranking(user_rag, query: str) -> List[Dict]:
    """The excerpts api._retrieve_context hands to the answer model, in order."""
    from api import chunk_score, heap_top_k
    retrieve = getattr(user_rag, "retrieve", None)
    chunks = retrieve(query, history="", top_k=K * 3) if callable(retrieve) \
        else user_rag.search(query, K * 3)
    return heap_top_k(chunks or [], key=chunk_score, k=K)


def setups(index: Index) -> Dict[str, Callable[[str], List[Dict]]]:
    from modules.rag_optimizations import HybridSearcher
    hybrid = HybridSearcher(index.store, reranker=None)
    return {
        "vector": lambda q: index.store.search(q, top_k=K, owner=index.owner),
        "hybrid": lambda q: hybrid.search(q, top_k=K, owner=index.owner),
        "hybrid+rerank (deployed)": lambda q: deployed_ranking(index.user_rag, q),
    }


def first_hit_rank(ranked: List[Dict], question: Dict) -> Optional[int]:
    for i, chunk in enumerate(ranked[:K], 1):
        if is_relevant(chunk, question):
            return i
    return None


def _scores(ranks: List[Optional[int]]) -> Dict:
    n = len(ranks)
    if not n:
        return {"n": 0}
    return {"n": n,
            "hit@1": round(sum(1 for r in ranks if r and r <= 1) / n, 3),
            "hit@3": round(sum(1 for r in ranks if r and r <= 3) / n, 3),
            "hit@6": round(sum(1 for r in ranks if r) / n, 3),
            "mrr": round(sum(1.0 / r for r in ranks if r) / n, 3)}


def unreachable_labels(index: Index, questions: List[Dict]) -> List[str]:
    """Questions whose labelled passage is in no single indexed chunk (e.g. split
    by the chunker) - a dataset error, not a retrieval miss."""
    chunks = index.owner_chunks()
    return [q["id"] for q in questions if q.get("relevant")
            and not any(is_relevant(c, q) for c in chunks)]


def run_retrieval(index: Index, questions: List[Dict]) -> Dict:
    answerable = [q for q in questions if q.get("relevant")]
    report = {"k": K, "questions": len(answerable), "chunks": len(index.owner_chunks()),
              "documents": len(CORPUS), "setups": {}, "leaks": [],
              "unreachable": unreachable_labels(index, questions),
              "reranker": index.rag.reranker_backend(), "per_question": {}}
    for name, fn in setups(index).items():
        ranks, by_type, lat = [], defaultdict(list), []
        for q in questions:
            t0 = time.perf_counter()
            ranked = fn(q["question"]) or []
            lat.append((time.perf_counter() - t0) * 1000)
            report["leaks"] += [{"setup": name, "question": q["id"], "file": c.get("file")}
                                for c in ranked if index.leaked(c)]
            if not q.get("relevant"):
                continue
            r = first_hit_rank(ranked, q)
            ranks.append(r)
            by_type[q["type"]].append(r)
            report["per_question"].setdefault(q["id"], {})[name] = r
        lat.sort()
        report["setups"][name] = {
            **_scores(ranks),
            "by_type": {t: _scores(v) for t, v in sorted(by_type.items())},
            "latency_ms_p50": round(statistics.median(lat), 1) if lat else None,
            "latency_ms_p95": round(lat[min(len(lat) - 1, int(0.95 * len(lat)))], 1) if lat else None,
        }
    return report


def retrieval_ok(rep: Dict, min_hit6: float = 0.0, min_hit1: float = 0.0,
                 min_mrr: float = 0.0, require_reranker: bool = False) -> bool:
    """Pass/fail for CI: no cross-user leak, no broken label, and the deployed
    setup at or above each floor.

    v3.6.0: three floors, because one was not enough. hit@6 cannot see the
    reranker at all -- all three setups score 35/38 (README, 2026-10-25) -- so a
    hit@6-only gate passed just as happily with the reranker switched off, which
    is precisely the failure R1 in docs/RAG_FINDINGS.md describes. hit@1 and MRR
    are the numbers the reranker moves (27/38 -> 31/38, 0.79 -> 0.86), so they
    are what a reranker gate has to be made of. hit@6 stays as the separate
    question "is the answer reachable at all".
    """
    if rep["leaks"] or rep["unreachable"]:
        return False
    deployed = list(rep["setups"].values())[-1] if rep["setups"] else {}
    if deployed.get("hit@6", 0) < min_hit6:
        return False
    if deployed.get("hit@1", 0) < min_hit1:
        return False
    if deployed.get("mrr", 0) < min_mrr:
        return False
    if require_reranker and not str(rep.get("reranker", "")).startswith("onnx"):
        return False
    return True


def gate_verdicts(rep: Dict, **floors) -> Dict[str, bool]:
    """Would this gate pass if each setup were the deployed one?

    The gate is itself a measuring instrument, so it gets the same suspicion as
    one. A gate that passes the vector-only setup is blind to the reranker: it
    would stay green on the day the reranker silently fell back to keyword
    overlap, which is the exact bug R1 records. `blind_to_reranker` below turns
    that into a number instead of a paragraph.
    """
    out = {}
    for name, scores in rep.get("setups", {}).items():
        out[name] = retrieval_ok(
            {"setups": {"deployed": scores}, "leaks": [], "unreachable": [],
             "reranker": rep.get("reranker", "")}, **floors)
    return out


def gate_sensitivity(rep: Dict, **floors) -> Dict:
    """The gate's own blind spot, measured.

    `discriminates` is True when the gate rejects a setup that has no reranker
    (vector or hybrid) and accepts the deployed one. False means the gate cannot
    see the component it is supposed to protect.
    """
    verdicts = gate_verdicts(rep, **floors)
    names = list(verdicts)
    deployed = names[-1] if names else None
    without = [n for n in names if n != deployed]
    passed_without = [n for n in without if verdicts.get(n)]
    return {
        "verdicts": verdicts,
        "deployed": deployed,
        "would_pass_without_reranker": passed_without,
        "discriminates": bool(deployed and verdicts.get(deployed) and not passed_without),
    }


def render_retrieval(rep: Dict) -> str:
    lines = ["# RAG retrieval eval", "",
             f"{rep['questions']} answerable questions over {rep['documents']} documents "
             f"({rep['chunks']} chunks), top-{rep['k']} excerpts; "
             f"reranker: `{rep['reranker']}`", "",
             "| Setup | hit@1 | hit@3 | hit@6 | MRR | keyword hit@6 | paraphrase hit@6 | p50 ms |",
             "|---|---|---|---|---|---|---|---|"]
    for name, s in rep["setups"].items():
        bt = s.get("by_type", {})
        lines.append(f"| {name} | {s.get('hit@1')} | {s.get('hit@3')} | {s.get('hit@6')} | "
                     f"{s.get('mrr')} | {bt.get('keyword', {}).get('hit@6')} | "
                     f"{bt.get('paraphrase', {}).get('hit@6')} | {s.get('latency_ms_p50')} |")
    deployed = list(rep["setups"])[-1]
    missed = sorted(q for q, v in rep["per_question"].items() if not v.get(deployed))
    lines += ["", f"Missed by the deployed setup (not in top {rep['k']}): {', '.join(missed) or 'none'}",
              f"Cross-user leaks: **{len(rep['leaks'])}**",
              f"Unreachable labels (dataset errors): {', '.join(rep['unreachable']) or 'none'}"]

    # The gate's own blind spot, printed. If "without reranker" rows say PASS,
    # the gate cannot detect the reranker disappearing (R1, docs/RAG_FINDINGS.md).
    verdicts = rep.get("gate", {}).get("verdicts") or {}
    if verdicts:
        sens = rep["gate"]
        lines += ["", "**Gate sensitivity** — would the gate pass this setup alone?", "",
                  "| Setup | Gate verdict |", "|---|---|"]
        for name, ok in verdicts.items():
            beat = " ← deployed" if name == sens.get("deployed") else ""
            if name != sens.get("deployed"):
                beat = " ← PASSES WITHOUT THE RERANKER" if ok else " ← correctly rejected"
            lines.append(f"| {name}{beat} | {'PASS' if ok else 'FAIL'} |")
        lines += ["",
                  (f"Gate discriminates the reranker: **YES** — a setup without it is rejected."
                   if sens.get("discriminates") else
                   f"Gate discriminates the reranker: **NO** — `"
                   f"{', '.join(sens.get('would_pass_without_reranker') or [])}` passes with no "
                   f"reranker, so this gate cannot detect R1 recurring.")]
    return "\n".join(lines)


# ------------------------------------------------------------------ answers

_REFUSAL_RE = re.compile(
    r"\b(?:don't|do not|doesn't|does not|didn't|did not)\b[^.\n]{0,60}?\b(?:mention|contain|include|"
    r"say|specify|provide|cover|state|list|discuss|have)"
    r"|\bnot (?:found|mentioned|specified|available|provided|stated|included|covered|given)\b"
    r"|\bno (?:information|mention|details|data|reference)\b"
    r"|\b(?:cannot|can't|could not|couldn't|unable to) (?:find|locate|determine|answer)\b",
    re.IGNORECASE)


def says_not_found(answer: str) -> bool:
    """The answer says the documents don't contain the information. Broader than
    api.is_thin_answer's phrase list (and without its length rule)."""
    return bool(_REFUSAL_RE.search(answer or ""))


def check_answer(question: Dict, answer: str, sources: List[str]) -> Dict:
    from api import _DOC_UNVERIFIED_NOTE
    from modules.grounding import check as grounding_check
    low = (answer or "").lower()
    out = {"error": (answer or "").strip().startswith("⚠️") or not (answer or "").strip(),
           "unverified": _DOC_UNVERIFIED_NOTE in (answer or ""),
           "grounding": round(grounding_check(answer or "", sources).rate, 3)}
    if question["type"] == "unanswerable":
        out["refused"] = says_not_found(answer)
        out["forbidden"] = [f for f in question.get("forbidden", []) if f.lower() in low]
        out["pass"] = out["refused"] and not out["forbidden"] and not out["error"]
    else:
        out["correct"] = any(a.lower() in low for a in question["answer_any"])
        out["cites_file"] = question["file"].lower() in low
        out["refused"] = says_not_found(answer)
        out["pass"] = out["correct"] and not out["error"]
    return out


def _rate(rows: List[Dict], key: str) -> Optional[float]:
    vals = [r["checks"][key] for r in rows if key in r["checks"]]
    return round(sum(1 for v in vals if v) / len(vals), 3) if vals else None


def summarize_answers(rows: List[Dict]) -> Dict:
    ans = [r for r in rows if r["type"] != "unanswerable"]
    una = [r for r in rows if r["type"] == "unanswerable"]
    lat = sorted(r["latency_s"] for r in rows)
    usage: Dict[str, float] = defaultdict(float)
    for r in rows:
        for k, v in r["usage"].items():
            usage[k] += v
    return {
        "questions": len(rows),
        "pass_rate": round(sum(r["checks"]["pass"] for r in rows) / len(rows), 3) if rows else None,
        "answer_accuracy": _rate(ans, "correct"),
        "citation_rate": _rate(ans, "cites_file"),
        "false_refusal_rate": _rate(ans, "refused"),
        "correct_refusal_rate": _rate(una, "refused"),
        "unverified_rate": _rate(rows, "unverified"),
        "error_rate": _rate(rows, "error"),
        "cache_served": sum(1 for r in rows if r["cache_hit"]),
        "grounding_rate": round(statistics.mean(r["checks"]["grounding"] for r in rows), 3) if rows else None,
        "latency_p50_s": round(statistics.median(lat), 1) if lat else None,
        "latency_p95_s": round(lat[min(len(lat) - 1, int(0.95 * len(lat)))], 1) if lat else None,
        "usage": {k: round(v, 4) for k, v in sorted(usage.items())},
    }


def run_answers(index: Index, questions: List[Dict], pause: float = 20.0) -> Dict:
    from api import answer_from_documents
    from evals.run import _delta, metric_totals
    sources = [open(p, encoding="utf-8").read() for p in CORPUS]
    rows = []
    for i, q in enumerate(questions, 1):
        cache_hit = bool(index.user_rag.get_cached_answer(q["question"]))
        before, t0 = metric_totals(), time.monotonic()
        try:
            answer = answer_from_documents(q["question"], index.user_rag)
        except Exception as e:
            answer = f"⚠️ {type(e).__name__}: {e}"[:300]
        row = {"id": q["id"], "type": q["type"], "question": q["question"],
               "latency_s": round(time.monotonic() - t0, 2), "cache_hit": cache_hit,
               "usage": _delta(metric_totals(), before), "answer": answer,
               "checks": check_answer(q, answer, sources)}
        rows.append(row)
        print(f"[{i}/{len(questions)}] {q['id']:<20} {'PASS' if row['checks']['pass'] else 'FAIL'}"
              f"  {row['latency_s']}s{'  (cache)' if cache_hit else ''}", flush=True)
        if i < len(questions) and pause > 0:
            time.sleep(pause)
    return {"summary": summarize_answers(rows), "results": rows}


def render_answers(rep: Dict) -> str:
    s = rep["summary"]
    lines = ["# RAG answer eval", "",
             f"- Questions: **{s['questions']}** - pass rate **{s['pass_rate']}**",
             f"- Answer accuracy (expected fact present): **{s['answer_accuracy']}**",
             f"- Citation rate (source file named): {s['citation_rate']}",
             f"- Correct refusals on unanswerable questions: **{s['correct_refusal_rate']}**",
             f"- False refusals on answerable questions: {s['false_refusal_rate']}",
             f"- Grounding rate (figures found in the documents): {s['grounding_rate']}",
             f"- CRAG 'unverified' note: {s['unverified_rate']} - errors: {s['error_rate']}"
             f" - served from semantic cache: {s['cache_served']}",
             f"- Latency p50 / p95: {s['latency_p50_s']}s / {s['latency_p95_s']}s",
             f"- Usage: {json.dumps(s['usage'])}", "",
             "| Question | Type | Pass | Failed checks |", "|---|---|---|---|"]
    for r in rep["results"]:
        c = r["checks"]
        if r["type"] == "unanswerable":
            bad = [] if c["refused"] else ["did not say 'not found'"]
        else:
            bad = ([] if c["correct"] else ["expected fact missing"]) + \
                  ([] if c["cites_file"] else ["file not cited"]) + (["refused"] if c["refused"] else [])
        bad += [k for k in ("error", "unverified") if c.get(k)]
        bad += [f"forbidden: {', '.join(c['forbidden'])}"] if c.get("forbidden") else []
        lines.append(f"| {r['id']} | {r['type']} | {'✅' if c['pass'] else '❌'} | {', '.join(bad)} |")
    return "\n".join(lines)


# ------------------------------------------------------------------ CLI

def _write(out: str, report: Dict, markdown: str) -> None:
    os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, ensure_ascii=False)
    print("\n" + markdown + f"\n\nFull report: {out}")
    summary = os.getenv("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as f:
            f.write(markdown + "\n")


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="RAG evaluation on the sample corpus.")
    ap.add_argument("mode", choices=["retrieval", "answers"])
    ap.add_argument("--ids", nargs="*")
    ap.add_argument("--limit", type=int)
    ap.add_argument("--pause", type=float, default=20.0,
                    help="answers: seconds between questions (free-tier token window)")
    ap.add_argument("--min-hit6", type=float, default=0.0,
                    help="retrieval: fail if the deployed setup's hit@6 is below this "
                         "(reachability: can the answer be found at all)")
    ap.add_argument("--min-hit1", type=float, default=0.0,
                    help="retrieval: fail if the deployed setup's hit@1 is below this "
                         "(ranking quality: this, not hit@6, is what the reranker moves)")
    ap.add_argument("--min-mrr", type=float, default=0.0,
                    help="retrieval: fail if the deployed setup's MRR is below this")
    ap.add_argument("--require-reranker", action="store_true",
                    help="retrieval: fail unless the live reranker backend is ONNX. "
                         "Without this, the fallback chain can go to keyword overlap "
                         "and hit@6 will not notice (docs/RAG_FINDINGS.md, R1)")
    ap.add_argument("--out", default=None)
    args = ap.parse_args(argv)

    questions = load_questions()
    if args.ids:
        questions = [q for q in questions if q["id"] in args.ids]
    questions = questions[:args.limit] if args.limit else questions
    sha = os.getenv("GIT_SHA", "")

    if args.mode == "retrieval":
        index = Index()
        rep = run_retrieval(index, questions)
        floors = dict(min_hit6=args.min_hit6, min_hit1=args.min_hit1,
                      min_mrr=args.min_mrr, require_reranker=args.require_reranker)
        rep["gate"] = gate_sensitivity(rep, **floors)
        rep.update(created=time.strftime("%Y-%m-%dT%H:%M:%S%z"), git_sha=sha,
                   thresholds={k: v for k, v in floors.items() if v})
        _write(args.out or os.path.join(tempfile.gettempdir(), "rag-retrieval.json"),
               rep, render_retrieval(rep))
        return 0 if retrieval_ok(rep, **floors) else 1

    from modules.rag_optimizations import GroqLLMClient
    llm = GroqLLMClient()
    if not llm._get_client():
        print("answers mode needs GROQ_API_KEY (run it inside the API pod).")
        return 2
    index = Index(llm=llm)
    print("Indexed:", "; ".join(index.add_results), flush=True)
    rep = run_answers(index, questions, pause=args.pause)
    rep.update(created=time.strftime("%Y-%m-%dT%H:%M:%S%z"), git_sha=sha,
               reranker=index.rag.reranker_backend())
    _write(args.out or os.path.join(tempfile.gettempdir(), "rag-answers.json"),
           rep, render_answers(rep))
    return 0


if __name__ == "__main__":
    sys.exit(main())
