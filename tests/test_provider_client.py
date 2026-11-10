"""Tests for the provider-aware client, including the tracing guarantee.

The risk this class was deferred for: ``InstrumentedGroqClient`` routes calls
through llama-index's ``GroqLLM`` specifically so the OpenInference instrumentor
emits an LLM span. Swapping the vendor SDK underneath was assumed to break that.

It does not, and these tests prove it rather than assert it in a comment: one of
them installs a real in-memory span exporter, instruments llama-index exactly
the way api.py does, makes a call for each provider's model, and checks an LLM
span came out. If a future change routes calls around llama-index (say, straight
to the vendor SDK), that test fails.

The second half is the actual bug: ``GroqLLM.__init__`` hardcoded ``Groq(...)``,
so ``gpt-4o`` was sent to api.groq.com and 404'd.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

os.environ.setdefault("GROQ_API_KEY", "test_key")

from modules import model_tiers
from modules.provider_client import (SUPPORTED_PROVIDERS, ProviderConfigError,
                                     build_client, provider_for_model,
                                     resolve_target)


# ------------------------------------------------------------ model -> vendor

def test_groq_models_resolve_to_groq():
    assert provider_for_model("qwen/qwen3.8-27b") == "groq"
    assert provider_for_model("openai/gpt-oss-20b") == "groq"


def test_openai_models_resolve_to_openai():
    assert provider_for_model("gpt-4o") == "openai"
    assert provider_for_model("gpt-4o-mini") == "openai"


def test_unknown_model_falls_back_to_the_default_provider(monkeypatch):
    """A custom model name must not crash; it keeps the old behaviour."""
    monkeypatch.setenv("GROQ_MODEL_STRONG", "some/custom-model")
    assert provider_for_model("some/custom-model") == "groq"


def test_env_override_moves_the_model_with_its_provider(monkeypatch):
    """If GROQ_MODEL_STRONG is set to gpt-4o, that name is served by OpenAI.

    Resolving by *provider first* would miss this; resolving against every
    provider's configured names catches it.
    """
    monkeypatch.setenv("GROQ_MODEL_STRONG", "gpt-4o")
    assert provider_for_model("gpt-4o") == "groq"


# ---------------------------------------------------------------- the factory

def test_build_client_returns_the_right_sdk():
    groq_client = build_client("groq", "k")
    assert type(groq_client).__module__.startswith("groq")
    openai_client = build_client("openai", "k")
    assert type(openai_client).__module__.startswith("openai")
    # Same surface, so callers never branch on vendor.
    for c in (groq_client, openai_client):
        assert hasattr(c, "chat") and hasattr(c.chat, "completions")
        assert callable(c.chat.completions.create)


def test_both_supported_clients_share_the_call_shape():
    groq_client, openai_client = build_client("groq", "k"), build_client("openai", "k")
    import inspect
    a = inspect.signature(groq_client.chat.completions.create)
    b = inspect.signature(openai_client.chat.completions.create)
    # Both must accept the kwargs this codebase passes.
    for name in ("model", "messages", "max_tokens", "stream", "tools"):
        assert name in a.parameters, f"groq client lacks {name}"
        assert name in b.parameters, f"openai client lacks {name}"


def test_anthropic_is_refused_loudly():
    """Anthropic's SDK has a different shape. Failing at construction beats
    failing mid-tool-loop with an AttributeError."""
    with pytest.raises(ProviderConfigError) as exc:
        build_client("anthropic", "k")
    assert "different request/response shape" in str(exc.value)
    assert "anthropic" not in SUPPORTED_PROVIDERS


def test_resolve_target_picks_the_matching_key(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-openai")
    t = resolve_target("strong", provider="openai")
    assert t["provider"] == "openai"
    assert t["model"] == "gpt-4o"
    assert t["api_key"] == "sk-openai"
    assert t["api_key_env"] == "OPENAI_API_KEY"


def test_resolve_target_without_provider_keeps_groq(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "gsk-x")
    t = resolve_target("strong")
    assert t["provider"] == "groq"
    assert t["api_key"] == "gsk-x"


# ---------------------------------------------------- the bug: SDK vs model

def test_groqllm_builds_the_sdk_that_matches_the_model(monkeypatch):
    """GroqLLM('gpt-4o') used to build a Groq client -> 404 at api.groq.com."""
    from llama_agent1 import GroqLLM

    built = {}

    def fake_build(provider, api_key, **kw):
        built["provider"] = provider
        return type("FakeClient", (), {"chat": None})()

    monkeypatch.setattr("modules.provider_client.build_client", fake_build)
    GroqLLM(model="gpt-4o", api_key="k")
    assert built["provider"] == "openai", "gpt-4o must not go to Groq's SDK"


def test_groqllm_still_uses_groq_for_groq_models(monkeypatch):
    from llama_agent1 import GroqLLM

    built = {}

    def fake_build(provider, api_key, **kw):
        built["provider"] = provider
        return type("FakeClient", (), {"chat": None})()

    monkeypatch.setattr("modules.provider_client.build_client", fake_build)
    GroqLLM(model="qwen/qwen3.8-27b", api_key="k")
    assert built["provider"] == "groq"


def test_explicit_provider_argument_wins(monkeypatch):
    from llama_agent1 import GroqLLM

    built = {}
    monkeypatch.setattr("modules.provider_client.build_client",
                        lambda p, k, **kw: built.update(provider=p) or type("C", (), {})())
    GroqLLM(model="anything", api_key="k", provider="openai")
    assert built["provider"] == "openai"


# ------------------------------------------- TRACING: the acceptance criterion

def test_swapping_the_vendor_keeps_the_llm_span():
    """The whole reason this was deferred.

    Installs a real in-memory exporter, instruments llama-index like api.py
    does, then calls GroqLLM.chat for a Groq model and an OpenAI model. Both
    must produce an LLM span. If someone later bypasses llama-index and calls
    the vendor SDK directly, this fails.
    """
    from opentelemetry import trace
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    trace.set_tracer_provider(provider)

    from openinference.instrumentation.llama_index import LlamaIndexInstrumentor
    LlamaIndexInstrumentor().instrument(tracer_provider=provider,
                                        use_legacy_callback_handler=False)

    from llama_agent1 import GroqLLM
    from llama_index.core.llms import ChatMessage

    class _Msg:
        def __init__(self, t):
            self.content = t
            self.tool_calls = None

    class _Choice:
        def __init__(self, t):
            self.message = _Msg(t)
            self.finish_reason = "stop"

    class _Resp:
        def __init__(self, t):
            self.choices = [_Choice(t)]
            self.usage = None

    class _Completions:
        def __init__(self):
            self.seen = []

        def create(self, **kw):
            self.seen.append(kw.get("model"))
            return _Resp("ok")

    class _Chat:
        def __init__(self):
            self.completions = _Completions()

    class _Client:
        def __init__(self):
            self.chat = _Chat()

    for model in ("qwen/qwen3.8-27b", "gpt-4o"):
        llm = GroqLLM(model=model, api_key="k")
        fake = _Client()
        llm._client = fake          # stand in for the vendor SDK
        exporter.clear()
        llm.chat([ChatMessage(role="user", content="hi")])
        spans = [s.name for s in exporter.get_finished_spans()]
        assert spans, f"no span at all for {model}"
        assert any("LLM" in n or "llm" in n for n in spans), \
            f"no LLM span for {model}; got {spans}"
        # And the request really went to the fake SDK, not the network.
        assert fake.chat.completions.seen == [model]
