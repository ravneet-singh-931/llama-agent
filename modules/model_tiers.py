"""
Model tiering for the Llama Agent.

Two model tiers are used to balance quality vs. latency/cost:
  - "strong": one-shot answers (document summaries, multi-agent tasks,
    non-streaming agent runs). Default: qwen/qwen3.8-27b.
  - "fast": streaming chat and cheap helper calls (query reformulation).
    Default: openai/gpt-oss-20b.

Configuration (env vars, resolved at call time so no restart is needed
between environments):
  LLM_MODEL_STRONG - overrides the strong tier
  LLM_MODEL_FAST   - overrides the fast tier
  LLM_MODEL        - legacy single-model override; when set it is used as
                     the fallback for BOTH tiers (so existing deployments
                     keep working unchanged).

Because the free tier of the 70b model is rate-limited (429s observed),
strong-model calls should be wrapped with should_fallback() so they can
retry once on the fast model instead of failing.
"""

import os

DEFAULT_STRONG_MODEL = "qwen/qwen3.8-27b"
DEFAULT_FAST_MODEL = "openai/gpt-oss-20b"

_TIER_ENV = {
    "strong": "LLM_MODEL_STRONG",
    "fast": "LLM_MODEL_FAST",
}


def resolve_model(tier: str) -> str:
    """Return the configured model for a tier.

    tier must be "strong" or "fast". Falls back to the legacy LLM_MODEL
    variable, then to the tier's built-in default.
    """
    if tier not in _TIER_ENV:
        raise ValueError(f"Unknown model tier: {tier!r} (expected 'strong' or 'fast')")
    default = DEFAULT_STRONG_MODEL if tier == "strong" else DEFAULT_FAST_MODEL
    return os.getenv(_TIER_ENV[tier], os.getenv("LLM_MODEL", default))


# Error signals that mean "the strong model is not usable right now"
# (rate limit / quota / model name rejected / connectivity). String based
# so it works with any Groq client error without importing groq error
# classes (keeps the module importable offline for tests).
_FALLBACK_SIGNALS = (
    "429",
    "rate limit",
    "rate_limit",
    "quota",
    "does not exist",
    "not found",
    "connection",
    "connecterror",
    "timeout",
    # Request bigger than the tier's per-minute input budget (HTTP 413):
    # the fast tier has its own, separate budget.
    "request too large",
    "413",
)


def should_fallback(error, tier: str) -> bool:
    """True if a strong-tier call failed and should be retried on the fast tier.

    Non-strong tiers never fall back (they are already the cheapest tier).
    """
    if tier != "strong":
        return False
    text = str(error).lower()
    return any(signal in text for signal in _FALLBACK_SIGNALS)


# ============================================================
# Point 9: multi-provider config + fallback chain
# ============================================================
# The app is instrumented for Groq via llama-index's GroqLLM (Phoenix spans).
# To add a PAID / multi-provider setup without losing tracing, register the
# provider here and wire its client at the call sites (see README
# "Cloud Hardening" > Multi-provider LLM). The chain + fallback logic below is
# provider-agnostic and fully testable offline.
#
# Activate by setting (needs the provider's paid key in the env/secret):
#   LLM_PROVIDER_CHAIN=groq,openai,anthropic
# Each provider falls through to the next on rate-limit / quota / auth errors.

DEFAULT_PROVIDER = "groq"

# Per-provider model tiers. Override per provider via env:
#   GROQ_MODEL_STRONG / GROQ_MODEL_FAST
#   OPENAI_MODEL_STRONG / OPENAI_MODEL_FAST
#   ANTHROPIC_MODEL_STRONG / ANTHROPIC_MODEL_FAST
PROVIDER_MODELS = {
    "groq":      {"strong": "qwen/qwen3.8-27b", "fast": "openai/gpt-oss-20b"},
    "openai":    {"strong": "gpt-4o",                 "fast": "gpt-4o-mini"},
    "anthropic": {"strong": "claude-3-5-sonnet-latest", "fast": "claude-3-5-haiku-latest"},
}

_PROVIDER_ENV = {
    "groq":      ("GROQ_MODEL_STRONG", "GROQ_MODEL_FAST"),
    "openai":    ("OPENAI_MODEL_STRONG", "OPENAI_MODEL_FAST"),
    "anthropic": ("ANTHROPIC_MODEL_STRONG", "ANTHROPIC_MODEL_FAST"),
}

_PROVIDER_API_KEY_ENV = {
    "groq": "GROQ_API_KEY",
    "openai": "OPENAI_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
}


def resolve_provider_chain() -> list:
    """Ordered providers to try, from LLM_PROVIDER_CHAIN (comma-separated).

    Groq is always appended as the guaranteed final fallback if not already
    present, and unknown providers are dropped. De-duplicated, order preserved.
    """
    raw = os.getenv("LLM_PROVIDER_CHAIN", DEFAULT_PROVIDER)
    chain = [p.strip().lower() for p in raw.split(",") if p.strip()]
    if DEFAULT_PROVIDER not in chain:
        chain.append(DEFAULT_PROVIDER)
    seen, out = set(), []
    for p in chain:
        if p not in seen and p in PROVIDER_MODELS:
            seen.add(p)
            out.append(p)
    return out or [DEFAULT_PROVIDER]


def resolve_model_for_provider(provider: str, tier: str) -> str:
    """Return the configured model for a provider + tier (strong/fast)."""
    if tier not in ("strong", "fast"):
        raise ValueError(f"Unknown model tier: {tier!r} (expected 'strong' or 'fast')")
    default = PROVIDER_MODELS.get(provider, PROVIDER_MODELS[DEFAULT_PROVIDER])[tier]
    env_strong, env_fast = _PROVIDER_ENV.get(provider, (None, None))
    if tier == "strong" and env_strong:
        return os.getenv(env_strong, default)
    if tier == "fast" and env_fast:
        return os.getenv(env_fast, default)
    return default


def provider_api_key_env(provider: str) -> str:
    """Env var that holds the API key for a provider."""
    return _PROVIDER_API_KEY_ENV.get(provider, "GROQ_API_KEY")


def should_fallback_to_next_provider(error, idx: int, chain: list) -> bool:
    """True if the failure on chain[idx] means we should try chain[idx+1]."""
    if idx >= len(chain) - 1:
        return False
    text = str(error).lower()
    return any(signal in text for signal in _FALLBACK_SIGNALS)
