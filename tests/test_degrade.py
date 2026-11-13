"""Tests for modules/silent.py and for the fallbacks that now go through it.

The point of `degrade()` is that a considered fallback must be *observable* and
must *name what it loses*. A helper that is never called would be decoration,
so these tests do three things:

  1. pin the helper's own contract (log at DEBUG, `what` is required,
     STRICT raises and does not swallow the original exception);
  2. fire real degraded paths in this codebase and prove they emit a record,
     so the conversion is not just cosmetic;
  3. guard the census: no new `except: pass` may appear without going through
     `degrade()`, so the work cannot silently regress.
"""

import ast
import logging
import pathlib

import pytest

from modules import silent


# --------------------------------------------------------------------------
# 1. The helper's own contract
# --------------------------------------------------------------------------

def test_degrade_logs_at_debug_and_does_not_raise(caplog):
    with caplog.at_level(logging.DEBUG, logger="modules.silent"):
        silent.degrade("a made-up fallback for the test", ValueError("boom"))
    assert any(
        "degraded: a made-up fallback for the test" in rec.getMessage()
        for rec in caplog.records
    ), caplog.text
    # The exception type and message travel with the record, so a DEBUG log
    # tells you *why* the fallback fired, not just that it did.
    assert "ValueError" in caplog.text
    assert "boom" in caplog.text


def test_degrade_without_exception_is_still_recorded(caplog):
    with caplog.at_level(logging.DEBUG, logger="modules.silent"):
        silent.degrade("fallback with no exception attached")
    assert "fallback with no exception attached" in caplog.text


def test_what_is_a_required_argument():
    # `degrade()` with no argument must be a TypeError, not a silent no-op:
    # the whole design is that naming the loss cannot be forgotten.
    with pytest.raises(TypeError):
        silent.degrade()  # type: ignore[call-arg]


def test_strict_mode_raises_and_chains_the_original(monkeypatch):
    original = ValueError("the real cause")
    monkeypatch.setattr(silent, "STRICT", True)
    with pytest.raises(silent.DegradedError) as info:
        silent.degrade("strict-mode fallback", original)
    assert "strict-mode fallback" in str(info.value)
    # The original exception must remain reachable, otherwise STRICT mode
    # would hide the cause it exists to expose.
    assert info.value.__cause__ is original


def test_strict_defaults_to_off():
    # Tests that do not opt in must not start failing on fallbacks.
    assert silent.STRICT is False


# --------------------------------------------------------------------------
# 2. Real degraded paths emit a record
# --------------------------------------------------------------------------

def test_unreadable_metrics_file_degrades(tmp_path, caplog):
    """monitoring.MetricsCollector._load_metrics with a corrupt file."""
    from modules.monitoring import AgentLogger

    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    (log_dir / "metrics.json").write_text("{not json", encoding="utf-8")
    with caplog.at_level(logging.DEBUG, logger="modules.silent"):
        logger = AgentLogger(log_dir=str(log_dir))
    assert any("metrics file missing or corrupt" in r.getMessage()
               for r in caplog.records), caplog.text
    # And the documented fallback still holds: empty defaults, no exception.
    assert logger.metrics["total_queries"] == 0


def test_torn_recording_line_degrades(tmp_path, caplog):
    """recording._load skips a half-written last line from a killed run."""
    from modules.recording import Recording

    directory = tmp_path / "run"
    directory.mkdir()
    good = '{"key": "a", "value": 1}'
    (directory / "llm.jsonl").write_text(good + "\n{\"key\": \"b\", \"va",
                                         encoding="utf-8")
    rec = Recording(str(directory), "replay")
    with caplog.at_level(logging.DEBUG, logger="modules.silent"):
        rec._load()
    assert any("torn last line" in r.getMessage() for r in caplog.records), \
        caplog.text
    # The intact line before the tear still loads.
    assert rec._llm.get("a") == 1


def test_strict_mode_makes_a_real_path_fail(monkeypatch, tmp_path):
    """STRICT turns one real fallback into an exception, proving wiring."""
    from modules.monitoring import AgentLogger

    log_dir = tmp_path / "strict"
    log_dir.mkdir()
    (log_dir / "metrics.json").write_text("{not json", encoding="utf-8")
    monkeypatch.setattr(silent, "STRICT", True)
    with pytest.raises(silent.DegradedError):
        AgentLogger(log_dir=str(log_dir))


# --------------------------------------------------------------------------
# 3. The census must not regress
# --------------------------------------------------------------------------

# The one handler that is allowed to keep a bare body: a queue timeout is
# normal control flow, not a degraded capability.
_ALLOWED_SILENT = {("evals/run.py", "queue.Empty")}


def _silent_handlers():
    root = pathlib.Path(__file__).resolve().parent.parent
    found = []
    for path in sorted(root.rglob("*.py")):
        rel = path.relative_to(root)
        if rel.parts[0] in {"tests", ".git", "node_modules", ".venv"}:
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8", errors="ignore"))
        except SyntaxError:  # pragma: no cover - not expected
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.ExceptHandler):
                continue
            body = [n for n in node.body if not isinstance(n, ast.Expr)
                    or not isinstance(getattr(n, "value", None), ast.Constant)]
            if len(body) != 1 or not isinstance(body[0], (ast.Pass, ast.Continue)):
                continue
            caught = ast.unparse(node.type) if node.type else "bare"
            found.append((rel.as_posix(), caught, node.lineno))
    return found


def test_no_undocumented_except_pass():
    """Every fallback handler must call degrade() or be on the allow-list."""
    offenders = []
    for path, caught, lineno in _silent_handlers():
        if any(path == p and caught == c for p, c in _ALLOWED_SILENT):
            continue
        offenders.append(f"{path}:{lineno} (except {caught})")
    assert not offenders, (
        "these handlers swallow an exception with no record of what was lost; "
        "route them through modules.silent.degrade() or add a documented "
        "exception to _ALLOWED_SILENT:\n  " + "\n  ".join(offenders)
    )


def test_degrade_is_actually_used_across_the_codebase():
    """A guard that nothing calls is a comment; the conversion must be real."""
    root = pathlib.Path(__file__).resolve().parent.parent
    files = set()
    for path in root.rglob("*.py"):
        if path.relative_to(root).parts[0] in {"tests", ".git", "node_modules"}:
            continue
        if path.name == "silent.py":
            continue
        if "degrade(" in path.read_text(encoding="utf-8", errors="ignore"):
            files.add(path.relative_to(root).as_posix())
    # The census found 60 such handlers across 19 files. Assert a floor well
    # above "a handful" so a partial revert is caught, without pinning an
    # exact count that would break every time a fallback is added.
    assert len(files) >= 15, f"degrade() only reached {sorted(files)}"
