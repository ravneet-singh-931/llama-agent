"""One place that decides which vendor SDK to talk to, and how.

Why this exists
---------------
``GroqLLM`` (llama_agent1.py) hardcoded::

    self._client = Groq(api_key=..., base_url=...)

so every model in ``modules.model_tiers.PROVIDER_MODELS`` — gpt-4o,
claude-3-5-sonnet — was sent to api.groq.com and 404'd. The registry, the
chain resolver, ``should_fallback_to_next_provider`` and their tests all
existed and production called none of it: the "build, test, never wire" pattern
in its purest form.

Design note: why this does NOT lose the Phoenix spans
-----------------------------------------------------
The obvious worry was that swapping the client underneath ``GroqLLM`` would
break tracing, because the OpenInference llama-index instrumentor is what
produces the spans. Measured, not assumed:

  * ``LLM.chat`` in llama-index is decorated ``@dispatcher.span``, and the
    dispatcher wraps a subclass's *override* at class-creation time.
  * So the span comes from llama-index's dispatcher, not from the Groq SDK.
    ``InstrumentedGroqClient`` routes through ``GroqLLM`` to get that span;
    the *vendor* is invisible to the instrumentor.

Verified with an in-memory exporter: a call through the factory produces a
``GroqLLM.chat`` span for both ``qwen/qwen3.8-27b`` (Groq) and ``gpt-4o``
(OpenAI). ``tests/test_provider_client.py`` keeps that true by asserting it
directly rather than trusting this comment.

What is shared and what differs
-------------------------------
Groq, OpenAI and (via the OpenAI SDK) most compatible endpoints expose the
same ``chat.completions.create`` surface and the same response shape, so one
code path serves all of them: only the constructor differs. Anthropic does
not, and is deliberately NOT supported here — see ``SUPPORTED_PROVIDERS``.
"""

from __future__ import annotations

import os
from typing import Optional

from modules.model_tiers import (
    DEFAULT_PROVIDER,
    PROVIDER_MODELS,
    provider_api_key_env,
    resolve_model_for_provider,
)

__all__ = [
    "SUPPORTED_PROVIDERS",
    "build_client",
    "provider_for_model",
    "resolve_target",
    "ProviderConfigError",
]

# Vendors whose Python SDK exposes the Groq/OpenAI ``chat.completions.create``
# shape this codebase is written against, so a single adapter serves both.
# Anthropic's SDK is a different shape (``client.messages.create`` with
# ``system`` lifted out of the message list and a different response object);
# pretending it is the same would fail at the first call, so it is refused
# loudly here rather than at runtime deep inside a tool loop.
SUPPORTED_PROVIDERS = ("groq", "openai")


class ProviderConfigError(RuntimeError):
    """Raised when a provider is requested that we cannot actually serve."""


def build_client(provider: str, api_key: str, timeout: Optional[float] = None,
                 max_retries: Optional[int] = None):
    """Return a vendor SDK client for ``provider``.

    Both supported SDKs expose ``.chat.completions.create(...)``, so callers
    never need to know which one they got.
    """
    if provider not in SUPPORTED_PROVIDERS:
        raise ProviderConfigError(
            f"Provider {provider!r} is not supported by the shared client "
            f"adapter (supported: {', '.join(SUPPORTED_PROVIDERS)}). "
            f"Anthropic uses a different request/response shape "
            f"(messages.create, system lifted out of the message list) and "
            f"needs its own adapter, not a copy of this one."
        )
    kwargs = {}
    if timeout is not None:
        kwargs["timeout"] = timeout
    if max_retries is not None:
        kwargs["max_retries"] = max_retries

    if provider == "groq":
        from groq import Groq
        return Groq(api_key=api_key, **kwargs)
    if provider == "openai":
        from openai import OpenAI
        return OpenAI(api_key=api_key, **kwargs)
    raise ProviderConfigError(f"unreachable: {provider}")  # pragma: no cover


def provider_for_model(model: str) -> str:
    """Which provider actually serves ``model``.

    Matched against the configured model name for each provider and tier, so
    an env override (GROQ_MODEL_STRONG=gpt-4o, technically possible but odd)
    still resolves to the provider that will accept it. Unknown model names
    fall back to the default provider, which preserves the pre-existing
    behaviour for every caller that has not opted into multi-provider.
    """
    for provider in PROVIDER_MODELS:
        for tier in ("strong", "fast"):
            if model == resolve_model_for_provider(provider, tier):
                return provider
    return DEFAULT_PROVIDER


def resolve_target(tier: str = "strong", provider: Optional[str] = None) -> dict:
    """Resolve {provider, model, api_key, api_key_env} for a call.

    ``provider`` pins the vendor; otherwise the provider is inferred from the
    configured model so nothing changes for existing Groq-only deployments.
    """
    if provider is None:
        model = resolve_model_for_provider(DEFAULT_PROVIDER, tier)
        provider = provider_for_model(model)
    else:
        model = resolve_model_for_provider(provider, tier)
    env_name = provider_api_key_env(provider)
    return {
        "provider": provider,
        "model": model,
        "api_key": os.getenv(env_name, ""),
        "api_key_env": env_name,
    }
