"""
Evaluation runner - scores the real agents on a fixed, varied dataset.

Run inside the API pod (it has the Groq/Tavily keys and the exact code that
serves users):

    kubectl -n llama-agent exec deploy/llama-agent-api -- \\
        python -m evals.run --tags smoke --out /tmp/eval.json

Each case is routed exactly like /chat/stream routes a message (@team ->
agent team, keyword/@Agent routing -> a specialist agent, otherwise the
general agent). For every case the runner records the answer, every tool
call and its raw result, the reasoning events, latency, LLM tokens/cost and
LLMOps guard actions (from this process's Prometheus counters), then runs
the deterministic checks (evals/checks.py) and the LLM judge
(evals/judge.py). A case passes when every applicable check passes and the
judge's overall score is >= --judge-threshold. If the judge itself fails
(e.g. the daily token limit), the case is NOT GRADED: it does not count as a
pass, and the report lists it separately. The report also keeps the source
text the judge saw, so every verdict can be checked by hand.

Compare two runs (exit code 1 on regression with --fail-on-regression):

    python -m evals.run --compare baseline.json new.json
"""

import argparse
import json
import os
import statistics
import sys
import tempfile
import threading
import time
import uuid
from collections import defaultdict
from typing import Callable, Dict, List, Optional

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from evals import checks  # noqa: E402

DATASET = os.path.join(os.path.dirname(os.path.abspath(__file__)), "dataset.jsonl")


# ------------------------------------------------------------------ dataset

def load_cases(path: str = DATASET, ids: Optional[List[str]] = None,
               categories: Optional[List[str]] = None, tags: Optional[List[str]] = None,
               limit: Optional[int] = None) -> List[Dict]:
    cases = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("//"):
                cases.append(json.loads(line))
    if ids:
        cases = [c for c in cases if c["id"] in ids]
    if categories:
        cases = [c for c in cases if c["category"] in categories]
    if tags:
        cases = [c for c in cases if set(tags) & set(c.get("tags", []))]
    return cases[:limit] if limit else cases


# ------------------------------------------------------------------ instrumentation

class ToolRecorder:
    """Wraps modules.agent_tools.dispatch_tool to capture every tool call
    (name, arguments, raw result) made while a case runs."""

    def __init__(self):
        self.calls: List[Dict] = []
        self._lock = threading.Lock()
        self._orig = None

    def __enter__(self):
        from modules import agent_tools
        self._orig = agent_tools.dispatch_tool
        orig = self._orig

        def recording(name, arguments):
            result = orig(name, arguments)
            with self._lock:
                self.calls.append({"name": name, "arguments": arguments, "result": result})
            return result

        agent_tools.dispatch_tool = recording
        return self

    def __exit__(self, *exc):
        from modules import agent_tools
        agent_tools.dispatch_tool = self._orig
        return False


_COUNTERS = {
    "llama_agent_llm_tokens": "tokens",
    "llama_agent_llm_cost_usd": "cost_usd",
    "llama_agent_llm_guard_events": "guard",
    "llama_agent_grounding_claims": "grounding_claims",
}


def metric_totals() -> Dict[str, float]:
    """Flattened totals of the LLM / guard counters in this process."""
    out: Dict[str, float] = defaultdict(float)
    try:
        from prometheus_client import REGISTRY
    except Exception:
        return out
    for fam in REGISTRY.collect():
        key = _COUNTERS.get(fam.name)
        if not key:
            continue
        for s in fam.samples:
            if not s.name.endswith("_total"):
                continue
            if key == "tokens":
                out[f"tokens_{s.labels.get('kind', 'unknown')}"] += s.value
            elif key == "guard":
                out[f"guard_{s.labels.get('action', 'unknown')}"] += s.value
            elif key == "grounding_claims":
                out[f"claims_{s.labels.get('result', 'unknown')}"] += s.value
            else:
                out[key] += s.value
    return out


def _delta(after: Dict[str, float], before: Dict[str, float]) -> Dict[str, float]:
    return {k: round(after.get(k, 0) - before.get(k, 0), 6)
            for k in set(after) | set(before) if after.get(k, 0) - before.get(k, 0)}


# ------------------------------------------------------------------ execution

def route(message: str) -> Dict:
    """Same routing decision as /chat/stream (api.py)."""
    from api import detect_agent_prefix, detect_team_prefix, route_multi_agent
    team, team_task = detect_team_prefix(message)
    if team:
        return {"route": "team", "task": team_task}
    use_multi, agent_type = route_multi_agent(message)
    if use_multi:
        _, clean = detect_agent_prefix(message)
        return {"route": f"agent:{agent_type}", "agent": agent_type, "task": clean}
    return {"route": "general", "task": message}


def execute(message: str, dsa: bool = True, timeout_s: float = 300.0) -> Dict:
    """Run one message through the real pipeline; returns answer + events."""
    from multi_agent import MultiAgentOrchestrator
    r = route(message)
    answer, reasoning = [], []
    critique = reflect = parallel = None if dsa else False
    start = time.monotonic()

    def timed_out():
        return time.monotonic() - start > timeout_s

    if r["route"] == "team":
        orch = MultiAgentOrchestrator()
        for ev in orch.collaborate_stream(r["task"], critique=critique, reflect=reflect,
                                          parallel=parallel):
            t = ev.get("type")
            if t == "token":
                answer.append(ev.get("content", ""))
            elif t == "agent_start":
                answer.append(f"\n\n### {ev.get('agent', 'Agent')}\n\n")
            elif t in ("reasoning", "tool"):
                reasoning.append({"stage": ev.get("stage", t), "content": ev.get("content", "")})
            if timed_out():
                answer.append("\n⚠️ Stream timed out.")
                break
    elif r["route"].startswith("agent:"):
        agent = MultiAgentOrchestrator().agents[r["agent"]]
        crit = os.getenv("AGENT_CRITIQUE", "1") == "1" if dsa else False
        refl = os.getenv("AGENT_REFLECT", "1") == "1" if dsa else False
        for ev in agent.execute_stream(r["task"], critique=crit, reflect=refl):
            t = ev.get("type")
            if t == "token":
                answer.append(ev.get("content", ""))
            elif t in ("reasoning", "tool"):
                reasoning.append({"stage": ev.get("stage", t), "content": ev.get("content", "")})
            if timed_out():
                answer.append("\n⚠️ Stream timed out.")
                break
    else:
        from llama_agent1 import run_agent_stream
        for tok in run_agent_stream(message, user_id="eval",
                                    conversation_id=f"eval-{uuid.uuid4().hex[:8]}"):
            answer.append(tok)
            if timed_out():
                answer.append("\n⚠️ Stream timed out.")
                break
    return {"route": r["route"], "answer": "".join(answer).strip(), "reasoning": reasoning}


def run_case(case: Dict, executor: Callable[..., Dict] = execute, use_judge: bool = True,
             judge_fn: Optional[Callable] = None, dsa: bool = True,
             grounding_threshold: float = 0.7, judge_threshold: float = 3.5,
             timeout_s: float = 300.0) -> Dict:
    before = metric_totals()
    t0 = time.monotonic()
    with ToolRecorder() as rec:
        try:
            out = executor(case["prompt"], dsa=dsa, timeout_s=timeout_s)
        except Exception as e:
            out = {"route": "error", "answer": "", "reasoning": [],
                   "error": f"{type(e).__name__}: {e}"[:300]}
    latency = round(time.monotonic() - t0, 2)
    usage = _delta(metric_totals(), before)

    results = checks.evaluate(case, out["answer"], rec.calls, grounding_threshold,
                              route=out.get("route"))
    verdict, graded, judge_src = None, True, ""
    if use_judge and out["answer"]:
        from evals.judge import judge, judge_sources
        sources = [c["result"] for c in rec.calls]
        judge_src = judge_sources(sources, out["answer"])
        verdict = (judge_fn or judge)(case, out["answer"], sources)
        # A judge failure (429, no JSON) is not a pass: the case is not graded.
        graded = bool(verdict) and "overall" in verdict
    passed = all(r["pass"] for r in results.values())
    if verdict and "overall" in verdict:
        passed = passed and verdict["overall"] >= judge_threshold
    passed = passed and graded
    return {
        "id": case["id"], "category": case["category"], "prompt": case["prompt"],
        "route": out["route"], "passed": passed, "graded": graded, "latency_s": latency,
        "checks": results, "judge": verdict, "judge_sources": judge_src, "usage": usage,
        "tools": [{"name": c["name"], "arguments": c["arguments"],
                   "result_chars": len(c["result"] or "")} for c in rec.calls],
        "reasoning": out["reasoning"], "answer": out["answer"],
        **({"error": out["error"]} if out.get("error") else {}),
    }


# ------------------------------------------------------------------ reporting

def _pct(n: int, d: int) -> float:
    return round(100.0 * n / d, 1) if d else 0.0


def summarize(results: List[Dict]) -> Dict:
    n = len(results)
    by_cat: Dict[str, List[Dict]] = defaultdict(list)
    for r in results:
        by_cat[r["category"]].append(r)
    check_fail: Dict[str, int] = defaultdict(int)
    for r in results:
        for name, c in r["checks"].items():
            if not c["pass"]:
                check_fail[name] += 1
    judged = [r["judge"]["overall"] for r in results if r.get("judge") and "overall" in r["judge"]]
    fabric = [r for r in results if r.get("judge") and r["judge"].get("fabrication")]
    ground = [r["checks"]["grounding"]["rate"] for r in results if "grounding" in r["checks"]]
    lat = sorted(r["latency_s"] for r in results)
    usage: Dict[str, float] = defaultdict(float)
    for r in results:
        for k, v in r["usage"].items():
            usage[k] += v
    not_graded = [r["id"] for r in results if not r.get("graded", True)]
    return {
        "cases": n,
        # Not-graded cases count as not passed; see "not_graded".
        "pass_rate": _pct(sum(r["passed"] for r in results), n),
        "not_graded": not_graded,
        "by_category": {c: {"cases": len(rs), "pass_rate": _pct(sum(r["passed"] for r in rs), len(rs))}
                        for c, rs in sorted(by_cat.items())},
        "check_failures": dict(sorted(check_fail.items())),
        "judge_overall": round(statistics.mean(judged), 2) if judged else None,
        "fabrication_rate": _pct(len(fabric), len(judged)) if judged else None,
        "grounding_rate": round(statistics.mean(ground), 3) if ground else None,
        "latency_p50_s": round(statistics.median(lat), 1) if lat else None,
        "latency_p95_s": round(lat[min(len(lat) - 1, int(0.95 * len(lat)))], 1) if lat else None,
        "usage": {k: round(v, 4) for k, v in sorted(usage.items())},
    }


def render_markdown(summary: Dict, results: List[Dict]) -> str:
    s = summary
    lines = [
        "# Evaluation report", "",
        f"- Cases: **{s['cases']}** - pass rate **{s['pass_rate']}%**"
        + (f" - **not graded** (judge failed, counted as not passed): {len(s['not_graded'])}"
           if s.get("not_graded") else ""),
        f"- Judge overall (1-5): **{s['judge_overall']}** - fabrication flagged: {s['fabrication_rate']}%",
        f"- Grounding rate (figures found in sources): **{s['grounding_rate']}**",
        f"- Latency p50 / p95: {s['latency_p50_s']}s / {s['latency_p95_s']}s",
        f"- Usage: {json.dumps(s['usage'])}", "",
        "| Category | Cases | Pass % |", "|---|---|---|",
    ]
    lines += [f"| {c} | {v['cases']} | {v['pass_rate']} |" for c, v in s["by_category"].items()]
    if s["check_failures"]:
        lines += ["", "Check failures: " + ", ".join(f"{k}={v}" for k, v in s["check_failures"].items())]
    lines += ["", "| Case | Route | Pass | Judge | Latency | Failed checks |", "|---|---|---|---|---|---|"]
    for r in results:
        failed = [f"{k} ({c.get('detail', '')})" if c.get("detail") else k
                  for k, c in r["checks"].items() if not c["pass"]]
        j = r.get("judge") or {}
        score = j.get("overall", j.get("error", "-"))
        status = "⚠️ not graded" if not r.get("graded", True) else ("✅" if r["passed"] else "❌")
        lines.append(f"| {r['id']} | {r.get('route', '-')} | {status} | {str(score)[:120]} "
                     f"| {r['latency_s']}s | {'; '.join(failed)[:160]} |")
    return "\n".join(lines)


def compare(old: Dict, new: Dict) -> Dict:
    """Metric deltas + cases that flipped between two report files."""
    so, sn = old["summary"], new["summary"]
    metrics = {}
    for k in ("pass_rate", "judge_overall", "grounding_rate", "fabrication_rate",
              "latency_p50_s", "latency_p95_s"):
        a, b = so.get(k), sn.get(k)
        metrics[k] = {"old": a, "new": b,
                      "delta": None if a is None or b is None else round(b - a, 3)}
    # Only cases graded in both runs can regress or be fixed.
    old_pass = {r["id"]: r["passed"] for r in old["results"] if r.get("graded", True)}
    new_pass = {r["id"]: r["passed"] for r in new["results"] if r.get("graded", True)}
    common = old_pass.keys() & new_pass.keys()
    regressed = sorted(i for i in common if old_pass[i] and not new_pass[i])
    fixed = sorted(i for i in common if not old_pass[i] and new_pass[i])
    cats = {}
    for c in set(so["by_category"]) & set(sn["by_category"]):
        cats[c] = round(sn["by_category"][c]["pass_rate"] - so["by_category"][c]["pass_rate"], 1)
    # Lower is better for latency / fabrication.
    worse = [k for k, v in metrics.items() if v["delta"] is not None and (
        (v["delta"] < 0 and k in ("pass_rate", "judge_overall", "grounding_rate"))
        or (v["delta"] > 0 and k == "fabrication_rate"))]
    return {"metrics": metrics, "category_pass_delta": cats, "regressed_cases": regressed,
            "fixed_cases": fixed,
            "regression": bool(regressed) or bool(worse) or any(v < 0 for v in cats.values())}


def render_compare(diff: Dict) -> str:
    lines = ["# Comparison", "", "| Metric | Old | New | Delta |", "|---|---|---|---|"]
    for k, v in diff["metrics"].items():
        lines.append(f"| {k} | {v['old']} | {v['new']} | {v['delta']} |")
    lines += ["", "Category pass-rate delta: " + json.dumps(diff["category_pass_delta"]),
              f"Fixed: {diff['fixed_cases'] or 'none'}",
              f"Regressed: {diff['regressed_cases'] or 'none'}",
              f"Verdict: {'REGRESSION' if diff['regression'] else 'no regression'}"]
    return "\n".join(lines)


# ------------------------------------------------------------------ CLI

def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Run the Llama Agent evaluation suite.")
    ap.add_argument("--dataset", default=DATASET)
    ap.add_argument("--ids", nargs="*")
    ap.add_argument("--category", nargs="*", dest="categories")
    ap.add_argument("--tags", nargs="*")
    ap.add_argument("--limit", type=int)
    ap.add_argument("--no-judge", action="store_true", help="deterministic checks only (cheaper)")
    ap.add_argument("--no-dsa", action="store_true", help="disable critic/reflection (cheaper)")
    ap.add_argument("--pause", type=float, default=10.0,
                    help="seconds between cases, lets the free-tier token window refill")
    ap.add_argument("--timeout", type=float, default=300.0, help="per-case limit (s)")
    ap.add_argument("--grounding-threshold", type=float, default=0.7)
    ap.add_argument("--judge-threshold", type=float, default=3.5)
    ap.add_argument("--out", default=os.getenv(
        "EVAL_OUT", os.path.join(tempfile.gettempdir(), "eval-report.json")))
    ap.add_argument("--compare", nargs=2, metavar=("OLD", "NEW"),
                    help="compare two report files instead of running")
    ap.add_argument("--fail-on-regression", action="store_true")
    args = ap.parse_args(argv)

    if args.compare:
        with open(args.compare[0]) as f:
            old = json.load(f)
        with open(args.compare[1]) as f:
            new = json.load(f)
        diff = compare(old, new)
        print(render_compare(diff))
        return 1 if (args.fail_on_regression and diff["regression"]) else 0

    cases = load_cases(args.dataset, args.ids, args.categories, args.tags, args.limit)
    if not cases:
        print("No cases selected.")
        return 2
    print(f"Running {len(cases)} case(s)...", flush=True)
    results = []
    for i, case in enumerate(cases, 1):
        r = run_case(case, use_judge=not args.no_judge, dsa=not args.no_dsa,
                     grounding_threshold=args.grounding_threshold,
                     judge_threshold=args.judge_threshold, timeout_s=args.timeout)
        results.append(r)
        j = (r.get("judge") or {}).get("overall", "-")
        print(f"[{i}/{len(cases)}] {r['id']:<22} {r['route']:<16} "
              f"{'PASS' if r['passed'] else 'FAIL'}  judge={j}  {r['latency_s']}s", flush=True)
        if i < len(cases) and args.pause > 0:
            time.sleep(args.pause)

    summary = summarize(results)
    report = {"created": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
              "git_sha": os.getenv("GIT_SHA", ""), "args": vars(args),
              "summary": summary, "results": results}
    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, ensure_ascii=False)
    print("\n" + render_markdown(summary, results))
    print(f"\nFull report: {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
