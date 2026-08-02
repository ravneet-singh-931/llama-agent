"""
Tests for Tier-2 helpers: model tiering, fallback decision, doc follow-up
detection, and thin-answer detection.
All tests are offline (no Groq API calls).
Run with: pytest tests/test_model_tiers.py -v
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from modules.model_tiers import (
    resolve_model,
    should_fallback,
    DEFAULT_STRONG_MODEL,
    DEFAULT_FAST_MODEL,
)


# ============ MODEL TIERING ============

def test_resolve_strong_default(monkeypatch):
    monkeypatch.delenv("LLM_MODEL_STRONG", raising=False)
    monkeypatch.delenv("LLM_MODEL", raising=False)
    assert resolve_model("strong") == DEFAULT_STRONG_MODEL


def test_resolve_fast_default(monkeypatch):
    monkeypatch.delenv("LLM_MODEL_FAST", raising=False)
    monkeypatch.delenv("LLM_MODEL", raising=False)
    assert resolve_model("fast") == DEFAULT_FAST_MODEL


def test_resolve_strong_override(monkeypatch):
    monkeypatch.setenv("LLM_MODEL_STRONG", "custom-strong")
    assert resolve_model("strong") == "custom-strong"


def test_resolve_fast_override(monkeypatch):
    monkeypatch.setenv("LLM_MODEL_FAST", "custom-fast")
    assert resolve_model("fast") == "custom-fast"


def test_resolve_legacy_llm_model_fallback(monkeypatch):
    """Existing deployments that only set LLM_MODEL keep working: it becomes
    the fallback for BOTH tiers."""
    monkeypatch.delenv("LLM_MODEL_STRONG", raising=False)
    monkeypatch.delenv("LLM_MODEL_FAST", raising=False)
    monkeypatch.setenv("LLM_MODEL", "legacy-model")
    assert resolve_model("strong") == "legacy-model"
    assert resolve_model("fast") == "legacy-model"


def test_resolve_unknown_tier_raises():
    try:
        resolve_model("ultra")
    except ValueError:
        pass
    else:
        raise AssertionError("expected ValueError for unknown tier")


# ============ FALLBACK DECISION ============

def test_should_fallback_rate_limit_on_strong():
    assert should_fallback(Exception("Error code: 429 - rate limit reached"), "strong")


def test_should_fallback_model_does_not_exist_on_strong():
    assert should_fallback(Exception("model llama-x does not exist"), "strong")


def test_should_fallback_generic_error_on_strong():
    assert not should_fallback(Exception("some unrelated error"), "strong")


def test_should_fallback_never_on_fast():
    # The fast tier is already the cheapest - it must never fall back.
    assert not should_fallback(Exception("Error code: 429 - rate limit"), "fast")


# ============ DOC QUESTION DETECTION ============

def test_doc_question_summarize_crm_file():
    """"summarize the CRM file" must be detected even though it does not
    contain the exact phrase "summarize the file"."""
    from api import is_document_question
    assert is_document_question("summarize the CRM file")


def test_doc_question_captainassistant_py_file():
    from api import is_document_question
    assert is_document_question("what is the Captainassistant.py file?")


def test_doc_question_plain_file_reference():
    from api import is_document_question
    assert is_document_question("describe the uploaded document")


def test_doc_question_does_not_hijack_followups():
    from api import is_document_question
    assert not is_document_question("what about the pricing column?")


def test_doc_question_does_not_match_greeting():
    from api import is_document_question
    assert not is_document_question("hello, how are you?")


# ============ DOC FOLLOW-UP DETECTION ============

def test_doc_follow_up_what_about():
    from api import is_doc_follow_up
    assert is_doc_follow_up("what about the pricing column?", "summarize the CRM file")


def test_doc_follow_up_needs_prior_doc_question():
    from api import is_doc_follow_up
    assert not is_doc_follow_up("what about the pricing column?", "")


def test_doc_follow_up_off_topic_without_anchor_phrase():
    from api import is_doc_follow_up
    assert not is_doc_follow_up("hello, how are you?", "summarize the CRM file")


def test_doc_follow_up_file_reference():
    from api import is_doc_follow_up
    assert is_doc_follow_up("and the file's summary table?", "summarize the CRM file")


def test_doc_follow_up_empty_message():
    from api import is_doc_follow_up
    assert not is_doc_follow_up("", "summarize the CRM file")


# ============ THIN-ANSWER DETECTION ============

def test_thin_answer_empty():
    from api import is_thin_answer
    assert is_thin_answer("")


def test_thin_answer_short():
    from api import is_thin_answer
    assert is_thin_answer("No.")


def test_thin_answer_says_not_in_document():
    from api import is_thin_answer
    assert is_thin_answer(
        "This document does not contain any information about pricing."
    )


def test_thin_answer_normal_is_fine():
    from api import is_thin_answer
    assert not is_thin_answer(
        "This is a CSV dataset of CRM records. It contains columns for "
        "customer names, deal values, and close dates. The pricing column "
        "lists the amount each customer agreed to pay."
    )
