"""
MCP Server - Exposes Specialized Agents as Tools

This is an MCP (Model Context Protocol) server that makes our 5 specialized
agents available as tools that any MCP client can discover and use.

Agents exposed as tools:
  - research_agent: Gathers and analyzes information
  - coder_agent: Writes, reviews, and debugs code
  - writer_agent: Creates clear, engaging content
  - analyzer_agent: Interprets data and finds patterns
  - planner_agent: Creates actionable plans and strategies
  - collaborate: Runs multiple agents together on a complex task
  - team_status: Shows status of all agents

Run: python mcp_agents_server.py
"""

import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(__file__))

try:
    from dotenv import load_dotenv
except ImportError:
    def load_dotenv(*_args, **_kwargs):
        return False

load_dotenv()

from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings

# Import our multi-agent system
from multi_agent import MultiAgentOrchestrator
from modules.secrets import get_secret

# Load GROQ_API_KEY from Docker secret file or env var
_groq_key = get_secret("GROQ_API_KEY", default=os.getenv("GROQ_API_KEY", ""))
if _groq_key:
    os.environ["GROQ_API_KEY"] = _groq_key

# Create the MCP server.
# NOTE: FastMCP auto-enables DNS rebinding protection (Host header validation)
# when constructed with a loopback host. We bind to 0.0.0.0 for Docker, so we
# pass explicit TransportSecuritySettings to keep the protection ON while
# allowing the real Host values that reach this server:
#   - "localhost" / "localhost:*"  -> external clients via Nginx reverse proxy
#   - "mcp-server:*"               -> internal clients on the Docker network
# Without this, Nginx proxies (Host: localhost) get rejected with HTTP 421.
mcp = FastMCP(
    "Llama Agent Multi-Agent Team",
    host="0.0.0.0",
    port=8001,
    transport_security=TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=[
            "localhost",
            "localhost:*",
            "127.0.0.1",
            "127.0.0.1:*",
            "[::1]:*",
            "mcp-server",
            "mcp-server:*",
        ],
        allowed_origins=[
            "http://localhost:*",
            "https://localhost:*",
            "http://127.0.0.1:*",
            "http://mcp-server:*",
        ],
    ),
)

# Create a shared orchestrator (initialized once when server starts)
orchestrator = MultiAgentOrchestrator()


@mcp.tool()
def research_agent(task: str) -> str:
    """
    Research agent: Gathers and analyzes information on any topic.

    Use this tool when the user needs:
    - In-depth research on a topic
    - Facts, explanations, or "what is" questions
    - Comparisons between concepts
    - Investigating or exploring a subject

    Args:
        task: The research task or question to investigate

    Returns:
        Comprehensive research findings
    """
    agent = orchestrator.agents["research"]
    result = agent.execute(task)
    return f"**Research Agent** handled this task:\n\n{result}"


@mcp.tool()
def coder_agent(task: str) -> str:
    """
    Coder agent: Writes, reviews, and debugs code in any programming language.

    Use this tool when the user needs:
    - Write code, functions, classes, scripts
    - Debug or fix bugs
    - Code review or refactoring
    - Programming explanations or algorithms
    - API endpoints, database queries, HTML/CSS/JS

    Args:
        task: The coding task (e.g., "write a Python function to sort a list")

    Returns:
        Working code with comments and examples
    """
    agent = orchestrator.agents["coder"]
    result = agent.execute(task)
    return f"**Coder Agent** handled this task:\n\n{result}"


@mcp.tool()
def writer_agent(task: str) -> str:
    """
    Writer agent: Creates clear, engaging written content.

    Use this tool when the user needs:
    - Articles, blog posts, essays
    - Reports, documentation, summaries
    - Creative writing (stories, poems)
    - Professional letters or emails
    - Any content creation task

    Args:
        task: The writing task (e.g., "write a blog post about AI trends")

    Returns:
        Well-structured, engaging content
    """
    agent = orchestrator.agents["writer"]
    result = agent.execute(task)
    return f"**Writer Agent** handled this task:\n\n{result}"


@mcp.tool()
def analyzer_agent(task: str) -> str:
    """
    Analyzer agent: Interprets data and finds patterns and insights.

    Use this tool when the user needs:
    - Data analysis or trend identification
    - Pattern recognition
    - Evaluate pros and cons
    - Assess performance or metrics
    - Break down complex information

    Args:
        task: The analysis task (e.g., "analyze the pros and cons of microservices")

    Returns:
        Thorough analysis with insights and recommendations
    """
    agent = orchestrator.agents["analyzer"]
    result = agent.execute(task)
    return f"**Analyzer Agent** handled this task:\n\n{result}"


@mcp.tool()
def planner_agent(task: str) -> str:
    """
    Planner agent: Creates actionable plans and strategies.

    Use this tool when the user needs:
    - Project plans or roadmaps
    - Step-by-step guides
    - Strategy or action plans
    - Timeline and milestones
    - How to start something

    Args:
        task: The planning task (e.g., "create a plan to learn Python in 3 months")

    Returns:
        Detailed, actionable plan with steps and timeline
    """
    agent = orchestrator.agents["planner"]
    result = agent.execute(task)
    return f"**Planner Agent** handled this task:\n\n{result}"


@mcp.tool()
def collaborate(task: str) -> str:
    """
    Multi-agent collaboration: Runs multiple specialized agents together on a complex task.

    Use this tool when the user needs:
    - A comprehensive solution combining research, code, and planning
    - A complex project that requires multiple perspectives
    - Something that needs both analysis and content creation

    Args:
        task: The complex task that benefits from multiple agent perspectives

    Returns:
        Combined results from Planner, Research, Coder, Writer, and Analyzer agents
    """
    result = orchestrator.collaborate(task)
    return result


@mcp.tool()
def team_status() -> str:
    """
    Shows the current status of all agents in the team, including how many tasks each has handled.

    Use this tool when the user asks:
    - "What agents do you have?"
    - "Show team status"
    - "How many tasks has each agent handled?"

    Returns:
        Status of all 5 agents with task counts
    """
    return orchestrator.get_team_status()


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="MCP Agent Server")
    parser.add_argument(
        "--transport",
        choices=["stdio", "sse"],
        default="stdio",
        help="Transport protocol (stdio for subprocess, sse for HTTP server)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=8001,
        help="Port for SSE transport (default: 8001)",
    )
    parser.add_argument(
        "--host",
        type=str,
        default="0.0.0.0",
        help="Host for SSE transport (default: 0.0.0.0)",
    )
    args = parser.parse_args()

    print("=" * 60)
    print("  MCP Agent Server - Starting...")
    print(f"  Agents: Research, Coder, Writer, Analyzer, Planner")
    print(f"  Transport: {args.transport}")
    if args.transport == "sse":
        print(f"  Endpoint: http://{args.host}:{args.port}/sse")
        print(f"  Messages: http://{args.host}:{args.port}/messages/")
    print("=" * 60)

    # Run the MCP server with SSE transport for Docker or stdio for subprocess
    if args.transport == "sse":
        # Set host and port via settings object
        mcp.settings.host = args.host
        mcp.settings.port = args.port
    mcp.run(transport=args.transport)
