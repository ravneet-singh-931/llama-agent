"""The MCP client's own logic, which no test had ever executed.

`tests/test_mcp_tool_call.py` (16 tests) checks the `/mcp/tools` **route**, and
does it well - but it patches `api.call_mcp_tool` wholesale, so the function that
actually speaks MCP was never run. Measured: 18 of the 28 statements in
`modules/mcp_client.py` were unexecuted, and they are the entire bodies of
`get_mcp_status` and `call_mcp_tool`.

What lives in those bodies is not boilerplate. It is the translation between an
MCP response and this project's contract, and every field is reached through
`getattr(..., default)` because the MCP spec leaves them optional:

    status  ->  {"connected", "server": {name, version, protocolVersion}, "tools"}
    call    ->  {"tool", "isError", "result"}

A wrong shape here is invisible to the route tests, because they replace this
function entirely. A fake session is used instead of a server: that tests the
translation, not the transport.
"""
import asyncio
from types import SimpleNamespace

import pytest

from modules import mcp_client


# ------------------------------------------------------------- fake session --

class _Omit:
    """Marker: the attribute is absent, not empty."""


OMIT = _Omit()


class _FakeSession:
    def __init__(self, init=None, tools=None, result=None):
        self._init = init if init is not None else SimpleNamespace(
            serverInfo=SimpleNamespace(name="mcp-agents", version="1.2.3"),
            protocolVersion="2024-11-05",
        )
        self._tools = tools or []
        self._result = result
        self.called_with = None

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def initialize(self):
        return self._init

    async def list_tools(self):
        if self._tools is OMIT:
            return SimpleNamespace()          # a server that sends no `tools` key
        return SimpleNamespace(tools=self._tools)

    async def call_tool(self, name, arguments):
        self.called_with = (name, arguments)
        return self._result


@pytest.fixture
def fake_transport(monkeypatch):
    """Replace the SSE transport and ClientSession; keep every line of the body."""
    def _install(session):
        class _Streams:
            async def __aenter__(self):
                return (object(), object())

            async def __aexit__(self, *a):
                return False

        async def _open():
            return _Streams()

        monkeypatch.setattr(mcp_client, "_open_session", _open)
        monkeypatch.setattr(mcp_client, "ClientSession",
                            lambda *a, **k: session)
        return session
    return _install


def _run(coro):
    return asyncio.run(coro)


# --------------------------------------------------------------- status path --

def test_status_reports_the_server_and_its_tools(fake_transport):
    tool = SimpleNamespace(name="coder_agent", description="writes code",
                           inputSchema={"type": "object"})
    fake_transport(_FakeSession(tools=[tool]))

    status = _run(mcp_client.get_mcp_status())

    assert status["connected"] is True
    assert status["server"] == {
        "name": "mcp-agents", "version": "1.2.3",
        "protocolVersion": "2024-11-05",
    }
    assert status["tools"] == [{
        "name": "coder_agent", "description": "writes code",
        "inputSchema": {"type": "object"},
    }]


def test_status_survives_a_server_that_reports_no_info(fake_transport):
    """`serverInfo` is optional in the MCP spec, so every field is a getattr.

    Without the defaults this raises `AttributeError` on `None` and `/mcp/status`
    returns 500 - for a server that is actually working.
    """
    fake_transport(_FakeSession(init=SimpleNamespace()))

    status = _run(mcp_client.get_mcp_status())

    assert status["connected"] is True
    assert status["server"]["name"] == "unknown"
    assert status["server"]["version"] == "unknown"
    assert status["server"]["protocolVersion"] == "unknown"


def test_status_survives_a_tool_with_no_description(fake_transport):
    """A missing description must become "" - `None` would render as "None" in
    the /tools payload and in the UI."""
    fake_transport(_FakeSession(tools=[SimpleNamespace(name="t", description=None)]))

    status = _run(mcp_client.get_mcp_status())

    assert status["tools"][0]["description"] == ""


def test_status_reports_no_tools_as_an_empty_list(fake_transport):
    fake_transport(_FakeSession())
    assert _run(mcp_client.get_mcp_status())["tools"] == []


def test_a_server_that_sends_no_tools_key_reports_no_tools(fake_transport):
    """`getattr(tools_result, "tools", [])` exists for a server that omits the
    key entirely - an empty list and an absent list are different responses, and
    only the absent one exercises the default.

    Found by sabotage: my first fake always set `.tools`, so the default could
    never fire and removing it changed nothing.
    """
    fake_transport(_FakeSession(tools=OMIT))
    assert _run(mcp_client.get_mcp_status())["tools"] == []


# ----------------------------------------------------------------- call path --

def test_a_tool_result_is_joined_into_one_string(fake_transport):
    """MCP returns content as a list of blocks; this project's contract is one
    string, which is what `api.py` puts in the response."""
    result = SimpleNamespace(isError=False, content=[
        SimpleNamespace(text="part one"),
        SimpleNamespace(text="part two"),
    ])
    fake_transport(_FakeSession(result=result))

    out = _run(mcp_client.call_mcp_tool("coder_agent", {"task": "write x"}))

    assert out == {"tool": "coder_agent", "isError": False,
                   "result": "part one\npart two"}


def test_an_image_block_is_described_rather_than_dropped(fake_transport):
    """A binary block has no `.text`. Dropping it would make a tool that returned
    only an image look like it returned nothing."""
    result = SimpleNamespace(isError=False, content=[
        SimpleNamespace(mimeType="image/png"),
    ])
    fake_transport(_FakeSession(result=result))

    out = _run(mcp_client.call_mcp_tool("t", {}))

    assert out["result"] == "[image: image/png]"


def test_an_error_result_keeps_its_text_and_sets_isError(fake_transport):
    """A tool that fails still returns readable content; the flag is what the
    caller branches on, so the text must not be swallowed."""
    result = SimpleNamespace(isError=True, content=[SimpleNamespace(text="boom")])
    fake_transport(_FakeSession(result=result))

    out = _run(mcp_client.call_mcp_tool("t", {}))

    assert out["isError"] is True
    assert out["result"] == "boom"


def test_an_empty_or_missing_content_list_yields_an_empty_string(fake_transport):
    fake_transport(_FakeSession(result=SimpleNamespace(isError=False, content=None)))
    assert _run(mcp_client.call_mcp_tool("t", {}))["result"] == ""

    fake_transport(_FakeSession(result=SimpleNamespace(isError=False)))
    assert _run(mcp_client.call_mcp_tool("t", {}))["result"] == ""


def test_a_result_with_no_isError_is_treated_as_success(fake_transport):
    """`getattr(result, "isError", False)` - an absent flag means success.

    Found by sabotage for the same reason as the tool-list default: every fake
    set the attribute, so the fallback was never exercised. A malformed or older
    server that omits `isError` must not turn a good result into an error.
    """
    fake_transport(_FakeSession(
        result=SimpleNamespace(content=[SimpleNamespace(text="fine")])))

    out = _run(mcp_client.call_mcp_tool("t", {}))

    assert out["isError"] is False
    assert out["result"] == "fine"


def test_the_tool_name_and_arguments_are_passed_through_untouched(fake_transport):
    """The client is a transport, not a validator - it must not reshape args."""
    result = SimpleNamespace(isError=False, content=[])
    session = fake_transport(_FakeSession(result=result))
    args = {"task": "x", "nested": {"a": [1, 2]}}

    out = _run(mcp_client.call_mcp_tool("research_agent", args))

    assert session.called_with == ("research_agent", args)
    assert out["tool"] == "research_agent"


def test_the_transport_timeouts_come_from_the_environment(monkeypatch):
    """A hard-coded 5s connect timeout would time out a cold Docker network;
    a hard-coded 300s read timeout would hold a request open for five minutes
    after the MCP server died. Both are configurable, which is the point.
    """
    src = open("modules/mcp_client.py").read()
    assert 'os.getenv("MCP_SSE_CONNECT_TIMEOUT"' in src
    assert 'os.getenv("MCP_SSE_READ_TIMEOUT"' in src
    assert 'os.getenv("MCP_SERVER_URL"' in src
    # and the defaults are what a fresh deploy gets
    assert mcp_client.SSE_CONNECT_TIMEOUT > 0
    assert mcp_client.SSE_READ_TIMEOUT > mcp_client.SSE_CONNECT_TIMEOUT, (
        "the read timeout must exceed the connect timeout, or a slow tool call "
        "is cut off before the connection is even established"
    )
