"""Guardrails and observability added together: spend cap, stream redaction,
tool-output budget, SSRF, and the cost-ledger reconciliation."""

import pytest


# ---------------------------------------------------------------- spend budget

class TestSpendBudget:
    def test_budget_is_disabled_by_default(self, monkeypatch):
        monkeypatch.delenv("AGENT_USER_TOKEN_BUDGET", raising=False)
        from modules import llm_guard
        assert llm_guard.user_budget() == 0
        assert llm_guard.over_budget() is False

    def test_spend_accumulates_and_trips(self, monkeypatch):
        monkeypatch.setenv("AGENT_USER_TOKEN_BUDGET", "1000")
        monkeypatch.setenv("AGENT_USER_BUDGET_WINDOW", "86400")
        from modules import llm_guard

        budget = llm_guard.SpendBudget()      # fresh, in-process
        assert budget.used("u1") == 0
        budget.record("u1", 600)
        assert budget.used("u1") == 600
        budget.record("u1", 100)
        assert budget.used("u1") == 700
        # a different subject has its own window
        assert budget.used("u2") == 0

    def test_zero_spend_is_a_noop(self):
        from modules import llm_guard
        budget = llm_guard.SpendBudget()
        budget.record("u1", 0)
        budget.record("u1", -5)
        assert budget.used("u1") == 0

    def test_message_mentions_the_limit(self, monkeypatch):
        monkeypatch.setenv("AGENT_USER_TOKEN_BUDGET", "50000")
        from modules import llm_guard
        assert "50,000" in llm_guard.budget_message()


# ------------------------------------------------------------- stream redaction

class TestStreamRedactor:
    def test_a_card_number_split_across_chunks_is_still_redacted(self):
        from modules.pii import StreamRedactor
        r = StreamRedactor()
        out = "".join(r.feed(c) for c in ["Your card 4111 1111", " 1111 1111 is", " on file."]) + r.flush()
        assert "4111" not in out
        assert "[REDACTED]" in out

    def test_an_ssn_split_across_chunks_is_redacted(self):
        from modules.pii import StreamRedactor
        r = StreamRedactor()
        out = "".join(r.feed(c) for c in ["SSN is 123-45", "-6789 ok"]) + r.flush()
        assert "6789" not in out

    def test_clean_text_is_returned_verbatim(self):
        from modules.pii import StreamRedactor
        r = StreamRedactor()
        text = "Hello world, nothing sensitive here at all."
        assert "".join(r.feed(c) for c in [text[:7], text[7:]]) + r.flush() == text

    def test_a_non_card_digit_run_is_not_redacted(self):
        """Luhn still decides: a 16-digit order id is left alone."""
        from modules.pii import StreamRedactor
        r = StreamRedactor()
        out = "".join(r.feed(c) for c in ["order 1234567890123456 shipped"]) + r.flush()
        assert "1234567890123456" in out


# ------------------------------------------------------- tool-output budget

class TestToolTokenCeiling:
    def _client(self, always_tool=True):
        import types

        class Msg:
            content = "working"
            tool_calls = [types.SimpleNamespace(
                id="c", function=types.SimpleNamespace(name="web_search",
                                                       arguments='{"query":"x"}'))]

        class Resp:
            choices = [types.SimpleNamespace(message=Msg())]

        return types.SimpleNamespace(
            chat=types.SimpleNamespace(completions=types.SimpleNamespace(
                create=lambda **k: Resp())))

    def test_the_loop_stops_when_the_budget_is_spent(self, monkeypatch):
        import modules.agent_tools as at
        monkeypatch.setattr(at, "_MAX_TOOL_TOKENS", 300)
        monkeypatch.setattr(at, "dispatch_tool", lambda n, a: "result " * 300)

        msgs = [{"role": "user", "content": "go " * 200}]
        _m, events, final = at.run_tool_rounds(self._client(), "m", msgs,
                                               tools=[{"type": "function"}], max_rounds=50)
        assert any("budget" in e for e in events)
        assert final is None

    def test_a_normal_run_is_unaffected(self, monkeypatch):
        import types
        import modules.agent_tools as at

        calls = {"n": 0}

        class First:
            content = "thinking"
            tool_calls = [types.SimpleNamespace(
                id="c", function=types.SimpleNamespace(name="web_search",
                                                       arguments='{"query":"x"}'))]

        class Answer:
            content = "The answer."
            tool_calls = None

        def create(**k):
            calls["n"] += 1
            m = First() if calls["n"] == 1 else Answer()
            return types.SimpleNamespace(choices=[types.SimpleNamespace(message=m)])

        client = types.SimpleNamespace(chat=types.SimpleNamespace(
            completions=types.SimpleNamespace(create=create)))
        monkeypatch.setattr(at, "dispatch_tool", lambda n, a: "ok")

        _m, events, final = at.run_tool_rounds(client, "m", [{"role": "user", "content": "hi"}],
                                               tools=[{"type": "function"}])
        assert final == "The answer."
        assert not any("budget" in e for e in events)


# --------------------------------------------------------------------- SSRF

class TestSSRFGuard:
    @pytest.mark.parametrize("url", [
        "http://127.0.0.1/x",
        "http://localhost:8080/admin",
        "http://169.254.169.254/latest/meta-data/",
        "http://10.0.0.5/",
        "http://192.168.1.1/",
        "http://172.16.3.4/",
        "http://[::1]/",
        "http://metadata.google.internal/",
        "file:///etc/passwd",
    ])
    def test_internal_and_non_http_destinations_are_refused(self, url):
        from modules.web_tools import _assert_http
        with pytest.raises(ValueError):
            _assert_http(url)

    @pytest.mark.parametrize("url", [
        "https://api.tavily.com/search",
        "https://serpapi.com/search.json",
    ])
    def test_real_endpoints_are_allowed(self, url):
        from modules.web_tools import _assert_http
        _assert_http(url)


# ------------------------------------------------------- cost reconciliation

class TestCostLedgerIsSingle:
    def test_costtracker_is_gone(self):
        """Two ledgers was the bug; the class existing again would restore it."""
        import modules.advanced_monitoring as am
        assert not hasattr(am, "CostTracker")
        assert not hasattr(am, "cost_tracker")

    def test_the_dashboard_reads_the_prometheus_counter(self, monkeypatch):
        from modules import prometheus_metrics as pm
        from modules.advanced_monitoring import get_monitoring_dashboard

        pm.record_llm_usage("qwen/qwen3.8-27b", 1000, 500, component="research")
        costs = get_monitoring_dashboard()["costs"]
        # the same number the counter exposes, not a second calculation
        assert costs["total_cost"] == pytest.approx(pm.cost_summary()["total_cost"])
        assert costs["total_cost"] > 0

    def test_token_totals_are_not_swapped(self):
        """The label order is [model, kind, component]; unpacking it wrong
        silently reported every token as output."""
        from modules import prometheus_metrics as pm
        before = pm.cost_summary()
        pm.record_llm_usage("openai/gpt-oss-20b", 3000, 400, component="coder")
        after = pm.cost_summary()
        assert after["total_input_tokens"] - before["total_input_tokens"] == 3000
        assert after["total_output_tokens"] - before["total_output_tokens"] == 400


# ------------------------------------------------------- prompt attribution

class TestPromptFingerprint:
    def test_fingerprint_is_stable_and_short(self):
        from evals.run import prompt_fingerprint
        a, b = prompt_fingerprint(), prompt_fingerprint()
        assert a == b and len(a) == 12

    def test_the_label_changes_the_fingerprint(self, monkeypatch):
        from evals.run import prompt_fingerprint
        monkeypatch.delenv("PROMPT_VERSION", raising=False)
        before = prompt_fingerprint()
        monkeypatch.setenv("PROMPT_VERSION", "v-test")
        assert prompt_fingerprint() != before


class TestPricingTable:
    """cost_by_model was computed by a function that used `os` without
    importing it. The NameError was swallowed by `except Exception: return
    0.0`, so every call would have been priced at zero -- spend would read 0
    forever and nothing would fail."""

    def test_a_known_model_is_priced(self):
        from modules.prometheus_metrics import _price
        assert _price("qwen/qwen3.8-27b", 1_000_000, 1_000_000) == 4.80

    def test_groq_pricing_env_override_is_honoured(self, monkeypatch):
        from modules.prometheus_metrics import _price
        monkeypatch.setenv("GROQ_PRICING", '{"custom/model": {"input": 1.0, "output": 2.0}}')
        assert _price("custom/model", 1_000_000, 1_000_000) == 3.0

    def test_an_unknown_model_falls_back_to_the_default_rate(self, monkeypatch):
        from modules.prometheus_metrics import _price
        monkeypatch.delenv("GROQ_PRICING", raising=False)
        monkeypatch.delenv("GROQ_DEFAULT_PRICE_IN", raising=False)
        monkeypatch.delenv("GROQ_DEFAULT_PRICE_OUT", raising=False)
        assert _price("who/knows", 1_000_000, 1_000_000) == 0.75

    def test_recording_usage_returns_a_nonzero_cost(self):
        """The end-to-end guard: record_llm_usage must not silently return 0."""
        from modules.prometheus_metrics import record_llm_usage
        assert record_llm_usage("qwen/qwen3.8-27b", 5000, 500, component="research") > 0


class TestBudgetSubjectBinding:
    """over_budget() must be told who the user is.

    /chat/stream checks the budget before it binds the request owner (the
    binding happens inside the SSE generator), so a check that read
    get_owner() looked at whichever request bound it last. Measured: the
    refusal never fired, because the spend had been recorded against one
    subject and the check read another.
    """

    def test_an_explicit_subject_overrides_the_bound_owner(self, monkeypatch):
        monkeypatch.setenv("AGENT_USER_TOKEN_BUDGET", "100")
        from modules import llm_guard
        budget = llm_guard.SpendBudget()
        monkeypatch.setattr(llm_guard, "get_budget", lambda: budget)

        budget.record("guest-abc", 500)
        assert llm_guard.over_budget("guest-abc") is True
        assert llm_guard.over_budget("guest-other") is False

    def test_without_a_subject_it_falls_back_to_the_owner(self, monkeypatch):
        monkeypatch.setenv("AGENT_USER_TOKEN_BUDGET", "100")
        from modules import llm_guard
        budget = llm_guard.SpendBudget()
        monkeypatch.setattr(llm_guard, "get_budget", lambda: budget)
        monkeypatch.setattr(llm_guard, "budget_subject", lambda: "owner-x")

        budget.record("owner-x", 500)
        assert llm_guard.over_budget() is True

    def test_a_disabled_budget_ignores_any_subject(self, monkeypatch):
        monkeypatch.delenv("AGENT_USER_TOKEN_BUDGET", raising=False)
        from modules import llm_guard
        assert llm_guard.over_budget("anyone") is False
