"""
v3.4.1 regression tests:
- coding-intent routing (pattern, not phrase list) without stealing writer/
  planner requests;
- eval route check;
- single-rollout deploy (no `kubectl set image`, one apply with the commit
  image) and per-app rollout strategy sized for the single 4-CPU node.
"""

import json
import os
import re
import subprocess  # nosec B404 - runs the repo's own sed expression in a test
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

ROOT = os.path.join(os.path.dirname(__file__), "..")


def _read(rel):
    with open(os.path.join(ROOT, rel), encoding="utf-8") as f:
        return f.read()


# ---------------------------------------------------------------- routing --

@pytest.mark.parametrize("msg", [
    "Write a Python function that merges overlapping intervals.",
    "Write a JavaScript debounce function and show how to use it.",
    "Write SQL to find the top 5 customers by order value.",
    "Write me a bash script to back up a folder",
    "Create a React component for a login form",
    "Give me a SQL query that finds duplicate emails",
    "Generate unit tests for this function",
    "Write a Python class implementing an LRU cache",
])
def test_coding_requests_route_to_coder(msg):
    from api import route_multi_agent
    assert route_multi_agent(msg) == (True, "coder")


@pytest.mark.parametrize("msg,expected", [
    ("Write documentation for a CLI tool called imgshrink", (True, "writer")),
    ("write an essay on functional programming", (True, "writer")),
    ("Write a story about a class of students", (True, "writer")),
    ("Create a plan to migrate our Python services", (True, "planner")),
    ("what is a python function", (False, "research")),
    ("Explain what a hash map is in simple terms", (False, "research")),
    ("Convert 72 degrees Fahrenheit to Celsius.", (False, "research")),
])
def test_code_pattern_does_not_steal_other_intents(msg, expected):
    from api import route_multi_agent
    assert route_multi_agent(msg) == expected


def test_dataset_routes_match_router():
    """Every non-team case with an expected route is routed that way."""
    from api import route_multi_agent
    for line in _read("evals/dataset.jsonl").splitlines():
        case = json.loads(line)
        exp = case.get("route")
        if not exp or exp == "team":
            continue
        use, agent = route_multi_agent(case["prompt"])
        got = f"agent:{agent}" if use else "general"
        assert got == exp, case["id"]


# ------------------------------------------------------------ eval checks --

def test_route_check_in_evaluate():
    from evals import checks
    case = {"id": "x", "prompt": "q", "route": "agent:coder"}
    bad = checks.evaluate(case, "answer", [], route="general")
    assert bad["route"]["pass"] is False
    assert "expected agent:coder" in bad["route"]["detail"]
    assert checks.evaluate(case, "answer", [], route="agent:coder")["route"]["pass"]
    # no expectation, or no route given -> no check
    assert "route" not in checks.evaluate({"id": "y", "prompt": "q"}, "a", [], route="general")
    assert "route" not in checks.evaluate(case, "a", [])


# ----------------------------------------------------------------- deploy --

def test_workflow_rolls_out_once_per_deploy():
    wf = _read(".github/workflows/main.yml")
    assert "set image" not in wf
    assert wf.count('bash k8s/apply-app.sh "${{ github.sha }}" llama-agent') == 2
    for name in ("api", "mcp-server", "frontend"):
        assert f"kubectl apply -f k8s/{name}.yaml" not in wf


def test_apply_app_pins_every_app_image():
    script = _read("k8s/apply-app.sh")
    expr = re.search(r'sed -E "([^"]+)"', script).group(1).replace("${TAG}", "abc123")
    for name, repo in (("api", "llama-agent"),
                       ("mcp-server", "llama-agent-mcp-server"),
                       ("frontend", "llama-agent-frontend")):
        out = subprocess.run(  # nosec B603 B607
            ["sed", "-E", expr, os.path.join(ROOT, "k8s", f"{name}.yaml")],
            capture_output=True, text=True, check=True).stdout
        images = re.findall(r"^\s+image: (\S+)", out, re.M)
        assert images == [f"ravneet231/{repo}:abc123"], (name, images)


@pytest.mark.parametrize("name,surge,unavailable", [
    ("api", 0, 1), ("frontend", 0, 1), ("mcp-server", 1, 0),
])
def test_rollout_strategy_fits_single_node(name, surge, unavailable):
    yaml = pytest.importorskip("yaml")
    docs = [d for d in yaml.safe_load_all(_read(f"k8s/{name}.yaml")) if d]
    dep = next(d for d in docs if d.get("kind") == "Deployment")
    spec = dep["spec"]
    ru = spec["strategy"]["rollingUpdate"]
    assert (ru["maxSurge"], ru["maxUnavailable"]) == (surge, unavailable)
    assert spec["progressDeadlineSeconds"] == 1200


def test_wait_budget_matches_progress_deadline():
    assert '"llama-agent-api:1200"' in _read("k8s/wait-rollout.sh")


# ============ CI'S OWN PYTHON SNIPPETS ============

def test_inline_ci_python_snippets_actually_run():
    """The Integration job's inline `python -c` blocks must execute as written.

    These snippets are the one part of the repo no pytest test exercises: they
    live inside the workflow YAML and only run in CI. That is not theoretical --
    a change removed `PerformanceMonitor.track_request()` and the Integration
    job broke while the full local suite stayed green, because the only caller
    of that method was one of these blocks.

    The block is a `python -c "..."` wrapped in double quotes, so shell
    quoting is part of the contract. Escaped double quotes inside it were
    stripped by bash before python saw them, producing
    `NameError: name 'status' is not defined` -- invisible to a local run that
    used single quotes. This test runs the extracted script verbatim through
    the same shell, so a quoting mistake fails here instead of in CI.
    """
    import yaml

    wf = yaml.safe_load(_read(".github/workflows/main.yml"))
    steps = wf["jobs"]["integration"]["steps"]
    blocks = [s["run"] for s in steps
              if "python -c" in s.get("run", "") and "Test integration" in s.get("name", "")]
    assert blocks, "the integration smoke-test step is gone"

    env = dict(os.environ, GROQ_API_KEY="test_key_for_ci")
    # Absolute path: CI runs bandit over the whole tree including tests, and
    # B607 flags a partial executable name. (My first version used "bash" and
    # passed locally only because my local bandit excluded ./tests.)
    proc = subprocess.run(  # nosec B603 - fixed argv, repo's own script
        ["/bin/bash", "-e", "-c", blocks[0]],
        cwd=ROOT, env=env, capture_output=True, text=True, timeout=300,
    )
    assert proc.returncode == 0, (
        "the Integration job's inline python snippet fails when run directly, "
        "so the CI job will fail:\n"
        f"--- stdout ---\n{proc.stdout}\n--- stderr ---\n{proc.stderr}"
    )
    assert "All integrations passed!" in proc.stdout
