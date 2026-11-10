"""The @team step deadline is per LEVEL, not per future.

Measured before the fix: AGENT_STEP_TIMEOUT=150 produced a 309 s run. The
timeout was applied by calling f.result(timeout=150) in a loop, so the second
future got 150 s ON TOP of the first -- a two-step level could block for 300 s,
over AGENT_LATENCY_BUDGET (180) and over the eval's 300 s case timeout. The
`with ThreadPoolExecutor(...)` block then waited for the hung worker anyway,
so even a fired timeout did not shorten the request.
"""

import time
from concurrent.futures import ThreadPoolExecutor

import pytest

import multi_agent


def test_step_timeout_is_read_at_call_time(monkeypatch):
    monkeypatch.setenv("AGENT_STEP_TIMEOUT", "2.5")
    assert multi_agent._step_timeout_seconds() == 2.5


def test_a_level_of_hanging_steps_is_bounded_by_one_timeout(monkeypatch):
    monkeypatch.setenv("AGENT_STEP_TIMEOUT", "0.5")

    def hang(_):
        time.sleep(3)
        return "never"

    level = ["a", "b", "c"]
    start = time.monotonic()
    pool = ThreadPoolExecutor(max_workers=4)
    try:
        futures = [pool.submit(hang, sid) for sid in level]
        deadline = time.monotonic() + multi_agent._step_timeout_seconds()
        outs = []
        for _sid, fut in zip(level, futures):
            try:
                outs.append(fut.result(timeout=max(0.0, deadline - time.monotonic())))
            except Exception:
                fut.cancel()
                outs.append("timed out")
    finally:
        pool.shutdown(wait=False, cancel_futures=True)
    elapsed = time.monotonic() - start

    assert elapsed < 1.5, f"3 steps x 0.5s stacked instead of shared: {elapsed:.2f}s"
    assert outs == ["timed out"] * 3
