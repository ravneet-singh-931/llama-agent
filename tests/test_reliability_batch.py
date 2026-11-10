"""Tests for the four "built but never wired" fixes.

Each of these covers something that already existed in the repo and did
nothing, which is the failure mode that has cost this project most:
``resolve_provider_chain``, ``compare()``, the eval suite and ``_is_supported``
were all shipped, all tested, and all called by nothing.
"""

import os
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))


# ============================================================ citation checks

class TestCitations:
    CONTEXT = (
        "Relevant document excerpts:\n\n"
        "--- Source: CRM-3.docx (chunk 0) ---\n"
        "Pricing starts at 29 USD per seat.\n\n"
        "--- Source: CRM-3.docx (chunk 2) ---\n"
        "Enterprise adds SSO and audit logs.\n\n"
        "--- Source: budget-q4.xlsx (chunk 1) ---\n"
        "Marketing spend was 12000.\n"
    )

    def test_parses_the_source_labels_the_retriever_writes(self):
        from modules.citations import parse_excerpt_sources
        assert parse_excerpt_sources(self.CONTEXT) == [
            ("CRM-3.docx", 0), ("CRM-3.docx", 2), ("budget-q4.xlsx", 1)]

    def test_valid_citation_passes(self):
        from modules.citations import check_citations
        r = check_citations("Pricing is 29 USD (CRM-3.docx, chunk 0).", self.CONTEXT)
        assert r["ok"] is True
        assert r["invalid"] == []

    def test_invented_file_is_flagged(self):
        from modules.citations import check_citations
        r = check_citations("Pricing is 29 USD (price-list.pdf, chunk 4).", self.CONTEXT)
        assert r["ok"] is False
        assert r["invented_files"] == [("price-list.pdf", 4)]

    def test_wrong_chunk_for_a_real_file_is_flagged(self):
        """Citing a document that WAS shown but a chunk that was not is still
        a fabricated citation, just a subtler one."""
        from modules.citations import check_citations
        r = check_citations("Pricing is 29 USD (CRM-3.docx, chunk 7).", self.CONTEXT)
        assert r["ok"] is False
        assert r["invalid"] == [("CRM-3.docx", 7)]
        assert r["invented_files"] == [], "the file does exist, only the chunk is wrong"

    def test_uncited_claims_are_reported(self):
        from modules.citations import check_citations
        r = check_citations("Pricing starts at 29 USD per seat.", self.CONTEXT)
        assert r["unsourced"] is True
        assert r["ok"] is False

    def test_file_name_matching_tolerates_case_and_punctuation(self):
        from modules.citations import check_citations
        r = check_citations("Cost is 29 (crm-3.DOCX, chunk 0).", self.CONTEXT)
        assert r["ok"] is True

    def test_no_context_is_not_a_failure(self):
        """Without excerpts there is nothing to check against; inventing a
        failure would flag every non-document answer."""
        from modules.citations import check_citations
        r = check_citations("An answer with no citations.", "")
        assert r["ok"] is True


# ============================================================ tool-call audit

class TestToolCallAudit:
    def _logger(self, tmpdir):
        import modules.security_hardening as sh
        log = os.path.join(tmpdir, "audit.log")

        class L(sh.AuditLogger):
            def __init__(self):
                super().__init__(log_file=log)
        return L, log

    def test_unknown_tool_is_audited_as_a_failure(self, tmp_path):
        """_dispatch returns error STRINGS rather than raising, so a naive
        audit recorded SUCCESS for a tool that never ran."""
        imp, log = self._logger(str(tmp_path))
        import modules.security_hardening as sh
        original = sh.AuditLogger
        sh.AuditLogger = imp
        try:
            sys.modules.pop("modules.agent_tools", None)
            import modules.agent_tools as at
            at.dispatch_tool("teleport", {})
            text = open(log).read()
        finally:
            sh.AuditLogger = original
        assert "TOOL_CALL_FAILURE" in text
        assert "tool=teleport" in text

    def test_successful_call_is_audited_with_duration(self, tmp_path):
        imp, log = self._logger(str(tmp_path))
        import modules.security_hardening as sh
        original = sh.AuditLogger
        sh.AuditLogger = imp
        try:
            sys.modules.pop("modules.agent_tools", None)
            import modules.agent_tools as at
            at.dispatch_tool("list_uploaded_documents", {})
            text = open(log).read()
        finally:
            sh.AuditLogger = original
        assert "TOOL_CALL_SUCCESS" in text
        assert "duration_ms=" in text

    def test_audit_user_uses_the_request_context(self):
        from modules.request_context import bind_owner, reset_owner
        import modules.agent_tools as at
        token = bind_owner("guest-abc123")
        try:
            assert at._audit_user() == "guest-abc123"
        finally:
            reset_owner(token)

    def test_audit_user_falls_back_without_context(self):
        from modules.request_context import bind_owner, reset_owner
        import modules.agent_tools as at
        token = bind_owner(None)
        try:
            assert at._audit_user() == "unknown"
        finally:
            reset_owner(token)

    def test_log_tool_call_redacts_identifiers(self, tmp_path):
        """A detail string built from a user's message can contain an SSN."""
        from modules.security_hardening import AuditLogger
        log = os.path.join(str(tmp_path), "a.log")
        AuditLogger(log_file=log).log_tool_call(
            tool="web_search", user_id="u1", success=True,
            duration_ms=12.0, detail="query=my ssn is 123-45-6789")
        text = open(log).read()
        assert "123-45-6789" not in text
        assert "[REDACTED]" in text


# ========================================================= FactMemory history

class TestFactHistory:
    def _fm(self, tmp_path):
        os.environ.pop("REDIS_URL", None)
        from modules.memory import FactMemory
        return FactMemory(facts_file=str(tmp_path / "facts.json"))

    def test_first_write_records_nothing_to_compare(self, tmp_path):
        f = self._fm(tmp_path)
        f.remember("name", "Alice", user_id="u1")
        assert f.history(key="name", user_id="u1") == []

    def test_overwrite_records_old_and_new(self, tmp_path):
        f = self._fm(tmp_path)
        f.remember("name", "Alice", user_id="u1")
        f.remember("name", "Bob", user_id="u1")
        h = f.history(key="name", user_id="u1")
        assert len(h) == 1
        assert h[0]["old"] == "Alice" and h[0]["new"] == "Bob"

    def test_rewriting_the_same_value_is_not_a_change(self, tmp_path):
        f = self._fm(tmp_path)
        f.remember("name", "Alice", user_id="u1")
        f.remember("name", "Alice", user_id="u1")
        assert f.history(key="name", user_id="u1") == []

    def test_forget_records_the_removal(self, tmp_path):
        f = self._fm(tmp_path)
        f.remember("name", "Alice", user_id="u1")
        f.forget("name", user_id="u1")
        h = f.history(key="name", user_id="u1")
        assert h[0]["action"] == "forget"
        assert h[0]["old"] == "Alice" and h[0]["new"] is None

    def test_history_is_tenant_scoped(self, tmp_path):
        f = self._fm(tmp_path)
        f.remember("name", "Alice", user_id="u1")
        f.remember("name", "Bob", user_id="u1")
        assert f.history(key="name", user_id="u2") == []

    def test_history_is_bounded(self, tmp_path):
        f = self._fm(tmp_path)
        for i in range(f.HISTORY_MAX + 100):
            f.remember("k", f"v{i}", user_id="u1")
        assert len(f._history) <= f.HISTORY_MAX

    def test_history_never_breaks_a_write(self, tmp_path, monkeypatch):
        """A broken history must not stop the fact being saved."""
        f = self._fm(tmp_path)

        def boom(*a, **k):
            raise RuntimeError("history unavailable")
        monkeypatch.setattr(f, "_record_change", boom)
        f.remember("name", "Alice", user_id="u1")
        f.remember("name", "Bob", user_id="u1")   # would call _record_change
        assert f.recall("name", user_id="u1") == "Bob"


# =================================================== per-step cost attribution

class TestCostAttribution:
    def test_component_lands_in_cost_by_query_type(self):
        """record_llm_usage took a component label but never passed it to
        CostTracker, so cost_by_query_type was empty in production."""
        from modules.prometheus_metrics import record_llm_usage
        from modules.advanced_monitoring import CostTracker
        tracker = CostTracker()
        import modules.advanced_monitoring as am
        original = am.cost_tracker
        am.cost_tracker = tracker
        try:
            record_llm_usage("qwen/qwen3.8-27b", 1000, 500, component="research")
            record_llm_usage("openai/gpt-oss-20b", 2000, 800, component="coder")
        finally:
            am.cost_tracker = original
        buckets = dict(tracker.usage["cost_by_query_type"])
        assert "research" in buckets and "coder" in buckets
        assert buckets["research"] > buckets["coder"], "the strong-tier step costs more"

    def test_unknown_component_is_normalised_not_dropped(self):
        from modules.prometheus_metrics import record_llm_usage
        from modules.advanced_monitoring import CostTracker
        tracker = CostTracker()
        import modules.advanced_monitoring as am
        original = am.cost_tracker
        am.cost_tracker = tracker
        try:
            record_llm_usage("openai/gpt-oss-20b", 100, 10, component="never-heard-of-it")
        finally:
            am.cost_tracker = original
        assert dict(tracker.usage["cost_by_query_type"])
