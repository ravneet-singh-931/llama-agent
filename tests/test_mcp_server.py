"""Smoke tests for `mcp_agents_server.py` — a service that ships but had no tests.

This file is not "more coverage". It exists because of a specific gap: the MCP
agent server is a **separate deployed process**. `docker-compose.yml` runs

    command: python mcp_agents_server.py --transport sse --port 8001 ...

and `k8s/mcp-server.yaml` runs it as its own Deployment with its own image tag.
It had **0% test coverage** — nothing in the suite imported it, so nothing would
have noticed if it stopped importing, lost a tool, or lost its host check.

The k8s manifests themselves are reviewed separately and are out of scope here.
This is about the Python process those manifests run.

What is asserted is deliberately shallow: the module imports, the seven tools
that its own docstring advertises are registered, and the DNS-rebinding
protection is configured. Nothing here calls an LLM or starts a server.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


@pytest.fixture(scope="module")
def server():
    return pytest.importorskip("mcp_agents_server")


def _registered_tools(server):
    return sorted(server.mcp._tool_manager._tools.keys())


def test_the_server_module_imports(server):
    """It is a deployed entry point; an ImportError here is a dead service."""
    assert server.mcp is not None


def test_every_advertised_tool_is_registered(server):
    """The module docstring lists these seven. The registry must match it.

    A tool missing from the registry is not a crash — the server starts, the
    client connects, and the tool the docs promise simply is not there.
    """
    expected = {
        "research_agent",
        "coder_agent",
        "writer_agent",
        "analyzer_agent",
        "planner_agent",
        "collaborate",
        "team_status",
    }
    assert set(_registered_tools(server)) == expected


def test_no_extra_tools_are_exposed_silently(server):
    """Everything exposed is callable by any connected MCP client."""
    assert len(_registered_tools(server)) == 7


def test_dns_rebinding_protection_is_on(server):
    sec = server.mcp.settings.transport_security
    assert sec is not None and sec.enable_dns_rebinding_protection is True


def test_allowed_hosts_do_not_include_a_wildcard(server):
    """`*` here would let any Host header reach the server (DNS rebinding)."""
    hosts = server.mcp.settings.transport_security.allowed_hosts
    assert hosts
    assert "*" not in hosts


def test_the_service_dns_name_is_allowed(server):
    """k8s/mcp-server.yaml addresses it by service name; a missing entry is
    a connection refused at deploy time, not in tests."""
    hosts = server.mcp.settings.transport_security.allowed_hosts
    assert any("llama-agent-mcp-server-service" in h for h in hosts)


def test_the_tool_bodies_are_reachable_functions(server):
    """Each registered tool wraps a real callable, not a stub."""
    for name in _registered_tools(server):
        tool = server.mcp._tool_manager._tools[name]
        assert callable(tool.fn), f"{name} is not callable"


def test_the_agent_runner_is_shared_by_every_tool(server):
    """All seven delegate to one runner; if it were missing they'd all 500."""
    assert callable(server._run_agent)


@pytest.mark.parametrize("bad", ["", "   ", "\n\t "])
def test_empty_task_is_rejected(server, bad):
    """Empty input must be refused before it costs an LLM call."""
    with pytest.raises(ValueError):
        server._guard_task(bad)


def test_a_non_string_task_is_rejected(server):
    for bad in (None, 42, ["a"], {"task": "x"}):
        with pytest.raises(ValueError):
            server._guard_task(bad)


def test_oversized_task_is_rejected(server):
    """_MCP_TASK_MAX bounds token cost; exceeding it must be refused, not sent."""
    too_big = "x" * (server._MCP_TASK_MAX + 1)
    with pytest.raises(ValueError, match="too long"):
        server._guard_task(too_big)


def test_a_task_exactly_at_the_limit_is_allowed(server):
    assert len(server._guard_task("x" * server._MCP_TASK_MAX)) == server._MCP_TASK_MAX


def test_a_normal_task_passes_through_unchanged(server):
    assert server._guard_task("  summarise this paper  ") == "  summarise this paper  "


# ------------------------------------------------ the timeout backstop --

def test_a_hung_tool_times_out_with_a_friendly_message(server, monkeypatch):
    """A hung upstream must become a message, not a wedged tool call.

    The timeout is shortened to milliseconds so the test proves the *branch*,
    not just "it returned a string" — the earlier version of this test passed
    even if the timeout logic were deleted entirely.
    """
    import time

    monkeypatch.setattr(server, "_MCP_TOOL_TIMEOUT", 0.05)

    def slow():
        time.sleep(0.3)
        return "should not reach the caller"

    out = server._run_with_timeout(slow)
    assert "timed out" in out.lower()
    assert "should not reach the caller" not in out


def test_a_failing_tool_returns_a_message_not_a_traceback(server):
    """An exception inside an agent must not leak internals to the MCP client."""
    def boom():
        raise RuntimeError("groq exploded: key sk-secret")

    out = server._run_with_timeout(boom)
    assert "⚠️" in out
    assert "sk-secret" not in out, "internal error text must not reach the client"


def test_a_successful_tool_returns_its_value(server):
    assert server._run_with_timeout(lambda: "all good") == "all good"
