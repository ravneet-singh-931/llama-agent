"""
MCP Client - Real Model Context Protocol (SSE) client.

This module connects to the MCP Agent Server (mcp-server:8001) over the
official MCP SSE transport and exposes its tools to the REST API.

Protocol flow (per the MCP specification):
  1. GET  http://mcp-server:8001/sse        -> open SSE event stream
  2. POST /messages/?session_id=<id>        -> JSON-RPC requests:
       - initialize                (handshake)
       - notifications/initialized (start session)
       - tools/list                (discover tools)
       - tools/call                (invoke a tool)
  3. JSON-RPC responses arrive back on the SSE stream

Why this exists: the frontend -> REST API -> MCP server (SSE) -> agents
chain means the MCP protocol is a real integration technology in this
project, not just a standalone service.
"""

import os
from typing import Any, Dict, List

from mcp import ClientSession
from mcp.client.sse import sse_client

# The MCP server's SSE endpoint on the internal Docker network.
# Overridable via env for local testing (e.g. http://localhost:8001/sse).
MCP_SERVER_URL = os.getenv("MCP_SERVER_URL", "http://mcp-server:8001/sse")

# Connection timeout for opening the SSE stream.
SSE_CONNECT_TIMEOUT = float(os.getenv("MCP_SSE_CONNECT_TIMEOUT", "5"))
# Max silence on the SSE stream while waiting for a tool result.
# Tool calls run real LLM agents (Groq) and can take 30-60+ seconds.
SSE_READ_TIMEOUT = float(os.getenv("MCP_SSE_READ_TIMEOUT", "300"))


async def _open_session():
    """Open an MCP session over SSE (context manager)."""
    return sse_client(
        MCP_SERVER_URL,
        timeout=SSE_CONNECT_TIMEOUT,
        sse_read_timeout=SSE_READ_TIMEOUT,
    )


async def get_mcp_status() -> Dict[str, Any]:
    """Connect to the MCP server, run the initialize handshake, and list tools.

    Returns:
        {
            "connected": True,
            "server": {"name": ..., "version": ..., "protocolVersion": ...},
            "tools": [{"name": ..., "description": ...}, ...],
        }
    """
    async with await _open_session() as (read_stream, write_stream):
        async with ClientSession(read_stream, write_stream) as session:
            init = await session.initialize()

            server_info = getattr(init, "serverInfo", None)
            tools_result = await session.list_tools()

            return {
                "connected": True,
                "server": {
                    "name": getattr(server_info, "name", "unknown") if server_info else "unknown",
                    "version": getattr(server_info, "version", "unknown") if server_info else "unknown",
                    "protocolVersion": getattr(init, "protocolVersion", "unknown"),
                },
                "tools": [
                    {
                        "name": tool.name,
                        "description": getattr(tool, "description", "") or "",
                        "inputSchema": getattr(tool, "inputSchema", None),
                    }
                    for tool in getattr(tools_result, "tools", [])
                ],
            }


async def call_mcp_tool(name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
    """Invoke an MCP tool over the SSE transport and return its text result.

    Args:
        name: Tool name (e.g. "coder_agent", "research_agent").
        arguments: JSON-serializable tool arguments (e.g. {"task": "..."}).

    Returns:
        {"tool": name, "isError": bool, "result": str}
    """
    async with await _open_session() as (read_stream, write_stream):
        async with ClientSession(read_stream, write_stream) as session:
            await session.initialize()
            result = await session.call_tool(name, arguments)

            text_parts: List[str] = []
            for block in getattr(result, "content", []) or []:
                if hasattr(block, "text"):
                    text_parts.append(block.text)
                elif hasattr(block, "mimeType"):
                    text_parts.append(f"[image: {block.mimeType}]")

            return {
                "tool": name,
                "isError": bool(getattr(result, "isError", False)),
                "result": "\n".join(text_parts),
            }
