"""
MCP Client Helper

Provides a simple async interface for Streamlit to connect to the MCP Agent Server
and call specialized agent tools.

Usage:
    from mcp_client_helper import MCPAgentClient

    client = MCPAgentClient()
    await client.connect()
    result = await client.call_agent("coder", "write a Python hello world")
    await client.disconnect()
"""

import os
import sys
import asyncio
from typing import Optional, Dict, Any

sys.path.insert(0, os.path.dirname(__file__))

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


class MCPAgentClient:
    """
    MCP Client that connects to our Agent Server via stdio transport.

    The server runs as a subprocess (launched automatically).
    The client discovers tools and calls them via the MCP protocol.
    """

    def __init__(self):
        self.session: Optional[ClientSession] = None
        self._read = None
        self._write = None
        self._cm = None  # context manager
        self._tools_cache: Optional[list] = None

    async def connect(self) -> list:
        """
        Connect to the MCP Agent Server.

        Launches mcp_agents_server.py as a subprocess and connects via stdio.

        Returns:
            List of available tool names
        """
        server_script = os.path.join(os.path.dirname(__file__), "mcp_agents_server.py")

        server_params = StdioServerParameters(
            command=sys.executable,  # Use the same Python that's running this code
            args=[server_script],
            env={k: v for k, v in os.environ.items() if v},  # Pass env vars to subprocess
        )

        # Start the server subprocess and connect
        self._cm = stdio_client(server_params)
        self._read, self._write = await self._cm.__aenter__()

        # Create the client session
        self.session = ClientSession(self._read, self._write)
        await self.session.__aenter__()

        # Initialize the connection (handshake)
        await self.session.initialize()

        # Discover available tools
        tools_result = await self.session.list_tools()
        self._tools_cache = tools_result.tools

        return [tool.name for tool in self._tools_cache]

    async def get_tools_info(self) -> list:
        """
        Get detailed info about all available tools.

        Returns:
            List of dicts with name, description for each tool
        """
        if not self._tools_cache:
            tools_result = await self.session.list_tools()
            self._tools_cache = tools_result.tools

        return [
            {
                "name": tool.name,
                "description": tool.description,
                "input_schema": tool.inputSchema,
            }
            for tool in self._tools_cache
        ]

    async def call_agent(self, agent_type: str, task: str) -> str:
        """
        Call a specific agent tool.

        Args:
            agent_type: One of "research", "coder", "writer", "analyzer",
                       "planner", "collaborate", "team_status"
            task: The task/question to send to the agent

        Returns:
            The agent's response as a string
        """
        if not self.session:
            raise RuntimeError("Not connected. Call connect() first.")

        # Map friendly names to MCP tool names
        tool_mapping = {
            "research": "research_agent",
            "coder": "coder_agent",
            "writer": "writer_agent",
            "analyzer": "analyzer_agent",
            "planner": "planner_agent",
            "collaborate": "collaborate",
            "team_status": "team_status",
        }

        tool_name = tool_mapping.get(agent_type, agent_type)

        # team_status takes no arguments
        if tool_name == "team_status":
            result = await self.session.call_tool(tool_name, {})
        else:
            result = await self.session.call_tool(tool_name, {"task": task})

        # Extract text from result content
        if result.content:
            return "\n".join(
                item.text for item in result.content if hasattr(item, "text")
            )
        return "No response from agent."

    async def route_and_call(self, task: str) -> str:
        """
        Route a task to the best agent based on keywords, then call it.

        This is the main method Streamlit will use.

        Args:
            task: The user's question or request

        Returns:
            The selected agent's response
        """
        task_lower = task.lower()

        # Keyword-based routing (fast, no extra API call)
        keyword_map = {
            "coder": [
                "write code", "function", "class", "debug", "fix bug",
                "programming", "python", "javascript", "code review",
                "refactor", "implement", "algorithm", "script", "api",
                "database", "sql", "html", "css", "react", "django",
                "flask", "fastapi", "code for", "program to",
            ],
            "writer": [
                "write an article", "write a blog", "write content",
                "draft", "compose", "write an essay", "write a story",
                "creative writing", "copywriting", "write a report",
                "write documentation", "write a post", "write a letter",
            ],
            "planner": [
                "plan", "strategy", "roadmap", "steps to",
                "how to start", "create a plan", "project plan",
                "action plan", "timeline", "milestones", "phases",
            ],
            "analyzer": [
                "analyze", "analysis", "statistics", "trends",
                "patterns", "data analysis", "evaluate", "assess",
                "break down", "metrics", "insights",
            ],
            "research": [
                "research", "explain", "what is", "how does",
                "tell me about", "compare", "difference between",
                "pros and cons", "investigate", "explore",
            ],
        }

        # Check each agent type in priority order
        for agent_type, keywords in keyword_map.items():
            if any(kw in task_lower for kw in keywords):
                return await self.call_agent(agent_type, task)

        # Default: use research agent for unmatched queries
        return await self.call_agent("research", task)

    async def disconnect(self):
        """Disconnect from the MCP server and clean up."""
        if self.session:
            await self.session.__aexit__(None, None, None)
            self.session = None
        if self._cm:
            await self._cm.__aexit__(None, None, None)
            self._cm = None
        self._tools_cache = None


# ============ SYNCHRONOUS WRAPPER ============
# Streamlit uses asyncio.run() which creates a new event loop.
# This helper provides sync functions that Streamlit can call directly.

def connect_mcp_sync() -> tuple:
    """
    Synchronously connect to the MCP server.

    Returns:
        (client, tool_names) tuple
    """
    client = MCPAgentClient()
    tools = asyncio.run(client.connect())
    return client, tools


def call_agent_sync(client: MCPAgentClient, agent_type: str, task: str) -> str:
    """Synchronously call an agent via MCP."""
    return asyncio.run(client.call_agent(agent_type, task))


def route_and_call_sync(client: MCPAgentClient, task: str) -> str:
    """Synchronously route and call the best agent via MCP."""
    return asyncio.run(client.route_and_call(task))


def disconnect_mcp_sync(client: MCPAgentClient):
    """Synchronously disconnect from the MCP server."""
    asyncio.run(client.disconnect())
