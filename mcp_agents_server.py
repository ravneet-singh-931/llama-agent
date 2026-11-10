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
from modules.security_hardening import detect_prompt_injection
import logging
from concurrent.futures import ThreadPoolExecutor

logging.basicConfig(level=logging.INFO)
_logger = logging.getLogger("mcp_agents_server")

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
            # Kubernetes service DNS (k8s/mcp-server.yaml)
            "llama-agent-mcp-server-service",
            "llama-agent-mcp-server-service:*",
        ],
        allowed_origins=[
            "http://localhost:*",
            "https://localhost:*",
            "http://127.0.0.1:*",
            "http://mcp-server:*",
        ],
    ),
)

# Fresh orchestrator per call: agent memory / task history must never be
# shared across MCP sessions (multi-tenant isolation). The previous module-level
# singleton leaked one user's task history and "remembered" state to others.
def _orchestrator():
    return MultiAgentOrchestrator()


# ============ INPUT VALIDATION + SAFE EXECUTION (Tier-1 hardening) ============

# Bound task size to contain token cost / abuse; reject empty input.
_MCP_TASK_MAX = int(os.getenv("MCP_TASK_MAX", "10000"))
# Backstop timeout so a hung upstream call can never wedge an MCP tool call.
_MCP_TOOL_TIMEOUT = float(os.getenv("MCP_TOOL_TIMEOUT", "120"))
_tool_executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="mcp-tool")


def _guard_task(task) -> str:
    """Validate and normalize an incoming task; raise ValueError if invalid."""
    if not isinstance(task, str) or not task.strip():
        raise ValueError("task must be a non-empty string")
    if len(task) > _MCP_TASK_MAX:
        raise ValueError(f"task is too long (max {_MCP_TASK_MAX} characters)")
    return task


def _audit_injection(task: str) -> None:
    """Defense-in-depth: log (do not block) detected injection attempts.

    The agents already delimit untrusted data and scan hand-offs, so this is
    purely an observability signal, matching api.py's audit-only posture.
    """
    if detect_prompt_injection(task):
        _logger.warning("Potential prompt-injection detected in MCP task input")


def _run_with_timeout(func):
    """Run func in a worker thread; return a safe message on timeout/error.

    The submitted task keeps running until the underlying call finishes (the
    Groq SDK has its own timeout), so this is a backstop, not a hard cancel.
    """
    fut = _tool_executor.submit(func)
    try:
        return fut.result(timeout=_MCP_TOOL_TIMEOUT)
    except TimeoutError:
        return ("⚠️ The request timed out. Please try a shorter or simpler task, "
                "or split it into smaller requests.")
    except Exception:
        return "⚠️ The agent could not complete that request right now. Please try again."


def _execute_agent(key: str, task: str) -> str:
    agent = _orchestrator().agents[key]
    result = agent.execute(task)
    return f"**{agent.name}** handled this task:\n\n{result}"


def _run_agent(key: str, task: str) -> str:
    """Execute one agent with validation, injection audit, and safe error masking."""
    try:
        _guard_task(task)
    except ValueError as e:
        return f"⚠️ {e}"
    _audit_injection(task)
    return _run_with_timeout(lambda: _execute_agent(key, task))


def _run_collaborate(task: str) -> str:
    """Run multi-agent collaboration with validation and safe error masking."""
    try:
        _guard_task(task)
    except ValueError as e:
        return f"⚠️ {e}"
    _audit_injection(task)
    return _run_with_timeout(lambda: _orchestrator().collaborate(task))


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
    return _run_agent("research", task)


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
    return _run_agent("coder", task)


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
    return _run_agent("writer", task)


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
    return _run_agent("analyzer", task)


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
    return _run_agent("planner", task)


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
    return _run_collaborate(task)


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
    try:
        return _orchestrator().get_team_status()
    except Exception:
        return "⚠️ Could not retrieve team status right now."


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
