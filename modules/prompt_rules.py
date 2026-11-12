"""
Shared prompt rules for every LLM entry point (specialist agents, the agent
team, and the general chat agent).

- Today's date, so "latest"/"this year" questions are anchored and the model
  never claims it cannot know the current date.
- English-only output: the Qwen strong tier otherwise sometimes slips into
  another language or script mid-answer (e.g. "$5/月").
"""

import os
from datetime import datetime, timezone


def today_str() -> str:
    """Today's date in APP_TIMEZONE (default Asia/Kolkata), e.g.
    'Friday, 23 October 2026 (Asia/Kolkata)'. Falls back to UTC when the
    zone database is unavailable in the container image.

    During an eval *replay* the date comes from the recording instead of the
    wall clock. Every system prompt carries this string, so a recording made
    on Tuesday replayed on Wednesday would hash differently and miss every
    single recorded reply. Pinning the clock is what makes replay possible at
    all, not a convenience."""
    try:
        from modules.recording import today_override
        pinned = today_override()
    except Exception:                                  # recording must never break a prompt
        pinned = None
    if pinned:
        return pinned
    tz_name = os.getenv("APP_TIMEZONE", "Asia/Kolkata")
    try:
        from zoneinfo import ZoneInfo
        now = datetime.now(ZoneInfo(tz_name))
    except Exception:
        now, tz_name = datetime.now(timezone.utc), "UTC"
    return now.strftime("%A, %d %B %Y") + f" ({tz_name})"


def common_rules() -> str:
    """Date + language rules appended to every system prompt."""
    return (
        f"Today's date is {today_str()}. Use it for anything time-related; "
        "do not say you cannot know the current date.\n"
        "Always respond in English only - never switch to another language or "
        "script (write 'per month', not '/月')."
    )


def chat_max_tokens() -> int:
    """Answer budget for the general chat agent (CHAT_MAX_TOKENS)."""
    try:
        return max(256, int(os.getenv("CHAT_MAX_TOKENS", "4096")))
    except ValueError:
        return 4096
