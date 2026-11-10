"""
Offline tests for the evaluation suite (evals/): dataset integrity,
deterministic checks, judge parsing, runner, summary and comparison.

Run with: pytest tests/test_evals.py -v
"""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
os.environ.setdefault("GROQ_API_KEY", "test-key")

from evals import checks, judge, run  # noqa: E402


# ============ DATASET ============

def test_dataset_is_valid_and_varied():
    cases = run.load_cases()
    ids = [c["id"] for c in cases]
    assert len(ids) == len(set(ids)) >= 25
    cats = {c["category"] for c in cases}
    assert len(cats) >= 7 and {"team", "coding", "research_news", "safety"} <= cats
    allowed = {"must_include_any", "must_not_include", "needs_sources",
               "code", "max_words", "min_words"}
    for c in cases:
        assert c["prompt"].strip()
        assert set(c.get("checks", {})) <= allowed, c["id"]
    smoke = run.load_cases(tags=["smoke"])
    assert 5 <= len(smoke) < len(cases)
    assert len({c["category"] for c in smoke}) >= 6     # smoke still covers most areas


def test_dataset_filters():
    assert [c["id"] for c in run.load_cases(ids=["general-percent"])] == ["general-percent"]
    assert all(c["category"] == "coding" for c in run.load_cases(categories=["coding"]))
    assert len(run.load_cases(limit=3)) == 3


# ============ CHECKS ============

def test_english_check():
    assert checks.check_english("Plain English answer.")["pass"]
    assert not checks.check_english("Price is $5/月 per user 每月 计划 价格")["pass"]
    assert checks.check_english("text\n```\n# 中文 comment in code\n```")["pass"]


def test_meta_talk_check_is_generic():
    assert not checks.check_no_meta("My step is only research; other agents will code.")["pass"]
    assert not checks.check_no_meta("Based on Source material 2, the price is...")["pass"]
    ok = "### Coder Agent\n\nHere is the page. The user agent string is logged."
    assert checks.check_no_meta(ok)["pass"]


def test_code_checks():
    good = "```python\ndef f(x):\n    return x\n```"
    bad = "```python\ndef f(x)\n    return x\n```"
    assert checks.check_code(good, "python")["pass"]
    assert "syntax error" in checks.check_code(bad, "python")["detail"]
    assert not checks.check_code("no code here", "python")["pass"]
    html_bad = "```html\n<html><body><div><p>x</p></body></html>\n```"
    assert "div" in checks.check_code(html_bad, "html")["detail"]
    html_ok = "```html\n<html><body><div><img src=a><br></div></body></html>\n```"
    assert checks.check_code(html_ok, "html")["pass"]
    assert not checks.check_fences("```python\nx=1\n")["pass"]


def test_includes_supports_today_tokens():
    year = checks._today_tokens()["year"]
    assert checks.check_includes(f"Today is in {year}.", ["{year}"])["pass"]
    assert not checks.check_includes("no date", ["{year}"])["pass"]


def test_completed_and_sources_checks():
    assert not checks.check_completed("I couldn't complete that task right now.")["pass"]
    assert not checks.check_completed("  ")["pass"]
    assert checks.check_sources([{"name": "web_search", "result": "r"}])["pass"]
    assert not checks.check_sources([{"name": "list_uploaded_documents", "result": "r"}])["pass"]


def test_evaluate_includes_grounding_only_with_sources():
    case = {"id": "x", "prompt": "q", "checks": {"needs_sources": True}}
    src = [{"name": "web_search", "result": "The market is worth 1.27 billion dollars. " * 10}]
    res = checks.evaluate(case, "It is 1.27 billion; 55% growth.", src)
    assert res["grounding"]["rate"] == 0.5 and res["grounding"]["unsupported"] == ["55%"]
    assert not res["grounding"]["pass"] and res["used_search"]["pass"]
    assert "grounding" not in checks.evaluate({"id": "y", "prompt": "q"}, "55%", [])


# ============ JUDGE ============

def test_parse_verdict():
    v = judge.parse_verdict('```json\n{"correctness": 4, "completeness": 5, "relevance": 5, '
                            '"clarity": 4, "fabrication": false, "comment": "none"}\n```')
    assert v["overall"] == 4.5 and v["fabrication"] is False
    assert judge.parse_verdict("no json") is None
    assert judge.parse_verdict('{"correctness": "x"}') is None
    assert judge.parse_verdict('{"correctness": 9, "completeness": 0, "relevance": 3, '
                               '"clarity": 3}')["correctness"] == 5


def test_judge_uses_rubric_reference_and_sources():
    seen = {}

    class _Msg:
        content = ('{"correctness": 2, "completeness": 3, "relevance": 4, "clarity": 4, '
                   '"fabrication": true, "comment": "invented winner"}')

    class _Client:
        class chat:  # noqa: N801
            class completions:  # noqa: N801
                @staticmethod
                def create(**kw):
                    seen.update(kw)
                    return type("R", (), {"choices": [type("C", (), {"message": _Msg})]})

    case = {"prompt": "who won X", "reference": "There is no prize X."}
    v = judge.judge(case, "Alice won.", ["source text"], client=_Client, model="m")
    assert v["overall"] == 3.25 and v["fabrication"] and v["model"] == "m"
    user = seen["messages"][1]["content"]
    assert "REFERENCE NOTE" in user and "source text" in user and "TODAY:" in user


def test_judge_never_raises():
    class _Boom:
        class chat:  # noqa: N801
            class completions:  # noqa: N801
                @staticmethod
                def create(**kw):
                    raise RuntimeError("429")
    assert "error" in judge.judge({"prompt": "p"}, "a", [], client=_Boom, model="m")


# ============ RUNNER ============

def test_route_matches_api_routing():
    assert run.route("@team build x")["route"] == "team"
    assert run.route("@Coder Agent write a function")["route"] == "agent:coder"
    assert run.route("What is 2+2?")["route"] == "general"


def test_run_case_records_tools_and_scores(monkeypatch):
    from modules import agent_tools

    def fake_executor(prompt, dsa=True, timeout_s=300):
        agent_tools.dispatch_tool("teleport", {})      # recorded via ToolRecorder
        return {"route": "agent:research", "answer": "Done in English.", "reasoning": []}

    case = {"id": "c1", "category": "research_facts", "prompt": "p",
            "checks": {"needs_sources": True}}
    r = run.run_case(case, executor=fake_executor, use_judge=True,
                     judge_fn=lambda c, a, s: {"overall": 4.0})
    assert r["tools"][0]["name"] == "teleport"
    assert not r["checks"]["used_search"]["pass"] and r["passed"] is False
    assert agent_tools.dispatch_tool.__name__ == "dispatch_tool"   # restored


def test_run_case_survives_executor_crash():
    def boom(prompt, **kw):
        raise RuntimeError("down")
    r = run.run_case({"id": "c", "category": "general", "prompt": "p"}, executor=boom,
                     use_judge=False)
    assert r["route"] == "error" and not r["passed"] and "down" in r["error"]


def _result(i, cat, passed, judge_score, ground=None, lat=1.0):
    res = {"id": i, "category": cat, "passed": passed, "latency_s": lat,
           "checks": {"completed": {"pass": passed}}, "judge": {"overall": judge_score},
           "usage": {"tokens_input": 100}}
    if ground is not None:
        res["checks"]["grounding"] = {"pass": True, "rate": ground}
    return res


def test_summary_compare_and_markdown(tmp_path):
    old = [_result("a", "x", True, 4.0, 0.9), _result("b", "y", False, 2.0)]
    new = [_result("a", "x", False, 3.0, 0.5), _result("b", "y", True, 4.5)]
    so, sn = run.summarize(old), run.summarize(new)
    assert so["pass_rate"] == 50.0 and so["grounding_rate"] == 0.9
    assert so["usage"]["tokens_input"] == 200
    diff = run.compare({"summary": so, "results": old}, {"summary": sn, "results": new})
    assert diff["regressed_cases"] == ["a"] and diff["fixed_cases"] == ["b"]
    assert diff["regression"]
    md = run.render_markdown(sn, new)
    assert "| a |" in md and "Pass" in md
    # CLI compare + exit code
    p_old, p_new = tmp_path / "o.json", tmp_path / "n.json"
    p_old.write_text(json.dumps({"summary": so, "results": old}))
    p_new.write_text(json.dumps({"summary": sn, "results": new}))
    assert run.main(["--compare", str(p_old), str(p_new), "--fail-on-regression"]) == 1
    assert run.main(["--compare", str(p_old), str(p_old), "--fail-on-regression"]) == 0


# ============ JUDGE FAILURES + AUDITABLE SOURCES (v3.4.9) ============
# The v3.4.8 agent eval hit the daily token limit: the judge returned a 429
# error for 16 cases, and 6 of them still counted as passes.

def _answering(prompt, dsa=True, timeout_s=300):
    from modules import agent_tools
    agent_tools.dispatch_tool("teleport", {})  # unknown tool: no network
    return {"route": "agent:writer", "answer": "A complete answer in English.", "reasoning": []}


def test_a_failed_judge_means_not_graded_not_passed():
    case = {"id": "w1", "category": "writing", "prompt": "p"}
    r = run.run_case(case, executor=_answering, use_judge=True,
                     judge_fn=lambda c, a, s: {"error": "RateLimitError: 429 tokens per day"})
    assert r["graded"] is False and r["passed"] is False
    s = run.summarize([r, {**_result("ok", "coding", True, 5.0), "graded": True}])
    assert s["not_graded"] == ["w1"] and s["pass_rate"] == 50.0
    md = run.render_markdown(s, [r])
    assert "not graded" in md and "| w1 |" in md and "⚠️ not graded" in md


def test_judged_and_no_judge_runs_are_graded():
    case = {"id": "w2", "category": "writing", "prompt": "p"}
    r = run.run_case(case, executor=_answering, use_judge=True,
                     judge_fn=lambda c, a, s: {"overall": 4.5})
    assert r["graded"] is True and r["passed"] is True
    r = run.run_case(case, executor=_answering, use_judge=False)
    assert r["graded"] is True and r["passed"] is True and r["judge_sources"] == ""
    assert run.summarize([r])["not_graded"] == []


def test_report_keeps_the_sources_the_judge_saw(monkeypatch):
    from modules import agent_tools
    monkeypatch.setattr(agent_tools, "dispatch_tool",
                        lambda name, args: "Singapore GP: 11 Oct 2026, won by Verstappen.")
    seen = {}

    def fake_judge(case, answer, sources):
        seen["msg"] = judge.build_messages(case, answer, sources, "2026-10-25")[1]["content"]
        return {"overall": 4.0}

    r = run.run_case({"id": "f1", "category": "research_news", "prompt": "p"},
                     executor=_answering, use_judge=True, judge_fn=fake_judge)
    assert "11 Oct 2026" in r["judge_sources"]
    assert r["judge_sources"] in seen["msg"]  # exactly the text the judge was shown


def test_compare_ignores_cases_that_were_not_graded():
    old = [_result("a", "x", True, 4.0)]
    new = [{**_result("a", "x", False, 4.0), "graded": False, "judge": {"error": "429"}}]
    diff = run.compare({"summary": run.summarize(old), "results": old},
                       {"summary": run.summarize(new), "results": new})
    assert diff["regressed_cases"] == []
