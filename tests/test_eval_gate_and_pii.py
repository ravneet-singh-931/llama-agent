"""Tests for the offline eval gate and PII redaction.

Both are pure functions with no model, network or document store, which is
exactly why they can gate every PR. These tests assert the *contract*: the
gate must fail when behaviour changes, and redaction must be precise enough to
be safe to run on every log write.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from evals import gate
from modules.pii import redact, redact_dict


# ---------------------------------------------------------------- eval gate

def test_gate_passes_on_the_committed_dataset():
    """The shipped dataset must agree with current routing. If this fails, a
    routing change was made without updating the goldens."""
    cases = gate.load_cases(gate.DATASET)
    checked, failures = gate.gate_routing(cases)
    assert checked >= 25, f"expected the dataset to declare routes, got {checked}"
    assert failures == []


def test_gate_detects_a_routing_regression(monkeypatch):
    """A changed route MUST fail the gate, with a message naming the case."""
    import modules.routing as routing

    monkeypatch.setattr(routing, "route_multi_agent", lambda m: (False, None))
    cases = gate.load_cases(gate.DATASET)
    checked, failures = gate.gate_routing(cases)
    assert checked >= 25
    assert failures, "the gate passed a dataset where every route became 'general'"
    assert any("route changed" in f for f in failures)


def test_gate_skips_cases_without_a_route():
    """Safety cases are graded on content, not route."""
    cases = [{"id": "a", "prompt": "hi", "route": "general"},
             {"id": "b", "prompt": "hi"}]
    checked, failures = gate.gate_routing(cases)
    assert checked == 1 and failures == []


def test_check_fixtures_all_pass():
    count, failures = gate.gate_checks()
    assert count >= 15, "the check gate should exercise every check type"
    assert failures == []


def test_gate_detects_a_broken_checker(monkeypatch):
    """If a checker stops failing bad answers, the gate must notice."""
    from evals import checks

    monkeypatch.setattr(checks, "check_completed",
                        lambda answer: {"pass": True, "detail": ""})
    _count, failures = gate.gate_checks()
    assert failures, "a checker that never fails was not detected"
    assert any("completed" in f for f in failures)


def test_gate_main_returns_zero_on_clean_run(capsys):
    assert gate.main([]) == 0
    assert "Eval gate passed" in capsys.readouterr().out


# ------------------------------------------------------------- PII redaction

def test_ssn_is_redacted():
    assert redact("my ssn is 123-45-6789 ok") == "my ssn is [REDACTED] ok"


def test_luhn_valid_card_is_redacted():
    assert redact("card 4111111111111111") == "card [REDACTED]"
    assert redact("card 4111 1111 1111 1111") == "card [REDACTED]"
    assert redact("card 4111-1111-1111-1111") == "card [REDACTED]"


def test_number_failing_luhn_is_left_alone():
    """A 16-digit order number is not a card. Over-redacting destroys logs."""
    text = "order 1234 5678 9012 3456 shipped"
    assert redact(text) == text


def test_phone_and_email_are_out_of_scope():
    """Deliberately not redacted: a regex cannot tell these from ordinary
    text without destroying the log's usefulness."""
    text = "call 555-123-4567 or mail a@b.com"
    assert redact(text) == text


def test_redact_is_idempotent():
    once = redact("ssn 123-45-6789 card 4111111111111111")
    assert redact(once) == once


def test_redact_handles_empty_and_none():
    assert redact("") == ""
    assert redact(None) is None


def test_redact_dict_walks_nested_structures():
    out = redact_dict({"a": "ssn 123-45-6789", "b": ["4111111111111111"],
                       "c": 42, "d": {"e": "123-45-6789"}})
    assert out["a"] == "ssn [REDACTED]"
    assert out["b"] == ["[REDACTED]"]
    assert out["c"] == 42
    assert out["d"]["e"] == "[REDACTED]"


def test_audit_logger_redacts_details(tmp_path):
    """The audit trail persists user-supplied text; it must be redacted."""
    from modules.security_hardening import AuditLogger

    log = AuditLogger(log_file=str(tmp_path / "audit.log"))
    log.log_suspicious_activity("test", "1.2.3.4", "pasted ssn 123-45-6789")
    written = (tmp_path / "audit.log").read_text()
    assert "123-45-6789" not in written
    assert "[REDACTED]" in written
    # The in-memory mirror must agree with the file.
    assert all("123-45-6789" not in str(e) for e in log.events)


# ------------------------------------------------------------ dataset shape

def test_dataset_has_sixty_cases_with_unique_ids():
    cases = gate.load_cases(gate.DATASET)
    assert len(cases) == 60
    ids = [c["id"] for c in cases]
    assert len(set(ids)) == len(ids), "duplicate case id"


def test_every_case_declares_a_valid_difficulty():
    allowed = {"easy", "medium", "hard", "adversarial"}
    cases = gate.load_cases(gate.DATASET)
    bad = [c["id"] for c in cases if c.get("difficulty") not in allowed]
    assert bad == [], f"missing/invalid difficulty: {bad}"


def test_every_case_can_be_graded_somehow():
    """A case with no checks and no reference cannot fail, so it is decoration."""
    cases = gate.load_cases(gate.DATASET)
    bad = [c["id"] for c in cases
           if not c.get("checks") and not c.get("reference")]
    assert bad == [], f"ungradable cases: {bad}"


def test_safety_cases_are_adversarial():
    """Every safety case is an adversarial attack.

    Route is usually omitted for these: the point is whether the answer
    repelled the attack, not what the router did, and asserting both would
    report one regression twice. ``adversarial-injected-doc`` is the
    exception -- it asserts that a poisoned *document* still routes normally,
    which is a distinct property worth pinning."""
    cases = {c["id"]: c for c in gate.load_cases(gate.DATASET)}
    for cid, c in cases.items():
        if c["category"] == "safety":
            assert c["difficulty"] == "adversarial", cid
    # Only the injected-document case pins a route; the rest must not.
    routed = {cid for cid, c in cases.items()
              if c["category"] == "safety" and "route" in c}
    assert routed == {"adversarial-injected-doc"}, routed


def test_declared_expected_tools_are_real_tool_names():
    """expected_tools sets the tool-recall gate; a typo would make a case
    permanently unpassable."""
    valid = {"web_search", "list_uploaded_documents", "read_uploaded_document"}
    cases = gate.load_cases(gate.DATASET)
    bad = [(c["id"], t) for c in cases
           for t in (c.get("expected_tools") or []) if t not in valid]
    assert bad == [], f"unknown tools in expected_tools: {bad}"
