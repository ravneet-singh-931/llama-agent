"""
Offline tests for the Point 9 multi-provider LLM config + fallback chain.

No network / API keys required.
"""
import importlib

import modules.model_tiers as mt


def test_resolve_provider_chain_default(monkeypatch):
    monkeypatch.delenv("LLM_PROVIDER_CHAIN", raising=False)
    assert mt.resolve_provider_chain() == ["groq"]


def test_resolve_provider_chain_appends_groq(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER_CHAIN", "openai")
    # groq must be appended as the guaranteed fallback
    assert mt.resolve_provider_chain() == ["openai", "groq"]


def test_resolve_provider_chain_dedupes_and_drops_unknown(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER_CHAIN", "openai,groq,openai,notarealprovider")
    assert mt.resolve_provider_chain() == ["openai", "groq"]


def test_resolve_model_for_provider_defaults():
    assert mt.resolve_model_for_provider("groq", "strong") == "qwen/qwen3.8-27b"
    assert mt.resolve_model_for_provider("openai", "fast") == "gpt-4o-mini"
    # unknown provider falls back to groq defaults
    assert mt.resolve_model_for_provider("bogus", "strong") == "qwen/qwen3.8-27b"


def test_resolve_model_for_provider_env_override(monkeypatch):
    monkeypatch.setenv("OPENAI_MODEL_STRONG", "gpt-4-turbo")
    assert mt.resolve_model_for_provider("openai", "strong") == "gpt-4-turbo"


def test_provider_api_key_env():
    assert mt.provider_api_key_env("groq") == "GROQ_API_KEY"
    assert mt.provider_api_key_env("openai") == "OPENAI_API_KEY"
    assert mt.provider_api_key_env("anthropic") == "ANTHROPIC_API_KEY"
    assert mt.provider_api_key_env("bogus") == "GROQ_API_KEY"


def test_should_fallback_to_next_provider():
    chain = ["openai", "groq"]
    # rate-limit on the first provider -> try next
    assert mt.should_fallback_to_next_provider("429 rate limit", 0, chain) is True
    # last provider in chain -> no further fallback
    assert mt.should_fallback_to_next_provider("429 rate limit", 1, chain) is False
    # non-retryable error on a non-last provider still falls through
    assert mt.should_fallback_to_next_provider("quota exceeded", 0, chain) is True


def test_config_provider_chain(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER_CHAIN", "groq,openai")
    from modules.config import LLMConfig
    cfg = LLMConfig.from_env()
    assert cfg.provider_chain == ["groq", "openai"]
