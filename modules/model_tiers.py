"""
Model tiering for the Llama Agent.

Two model tiers are used to balance quality vs. latency/cost:
  - "strong": one-shot answers (document summaries, multi-agent tasks,
    non-streaming agent runs). Default: llama-3.3-70b-versatile.
  - "fast": streaming chat and cheap helper calls (query reformulation).
    Default: llama-3.1-8b-instant.

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

DEFAULT_STRONG_MODEL = "llama-3.3-70b-versatile"
DEFAULT_FAST_MODEL = "llama-3.1-8b-instant"

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
)


def should_fallback(error, tier: str) -> bool:
    """True if a strong-tier call failed and should be retried on the fast tier.

    Non-strong tiers never fall back (they are already the cheapest tier).
    """
    if tier != "strong":
        return False
    text = str(error).lower()
    return any(signal in text for signal in _FALLBACK_SIGNALS)
