"""
Multi-Agent System
Multiple agents working together like a TEAM!
"""

import asyncio
import os
import sys
from typing import List, Dict, Any, Optional, Iterator
from datetime import datetime

sys.path.insert(0, os.path.dirname(__file__))

try:
    from dotenv import load_dotenv
except ImportError:
    def load_dotenv(*_args, **_kwargs):
        return False

from groq import Groq
from modules.secrets import get_secret
from modules.model_tiers import resolve_model, should_fallback

load_dotenv()

# Load GROQ_API_KEY from Docker secret file or env var
_groq_key = get_secret("GROQ_API_KEY", default=os.getenv("GROQ_API_KEY", ""))
if _groq_key:
    os.environ["GROQ_API_KEY"] = _groq_key

# Bind an (initially empty) document store so agent document tools work even
# when multi_agent is used standalone (CLI / MCP). The API rebinds this to
# the real RAG store (app.state.rag) at startup.
try:
    from modules.rag import SimpleRAG
    from modules.agent_tools import set_document_store
    set_document_store(SimpleRAG())
except Exception:
    pass


# ============ BASE AGENT ============

class BaseAgent:
    """Base class for all agents"""
    
    def __init__(self, name: str, role: str, model: str = ""):
        self.name = name
        self.role = role
        # Agents produce one-shot answers -> strong model tier (configurable
        # via LLM_MODEL_STRONG / LLM_MODEL / defaults in modules.model_tiers).
        self.model = model or resolve_model("strong")
        # Use env var (already set by module-level code or docker-compose)
        api_key = os.environ.get("GROQ_API_KEY", "")
        if not api_key:
            # If not set, try reading from Docker secret file directly
            from modules.secrets import get_secret
            api_key = get_secret("GROQ_API_KEY", default="")
            if api_key:
                os.environ["GROQ_API_KEY"] = api_key
        self.client = Groq(api_key=api_key)
        self.memory: List[Dict] = []

    def _chat(self, messages, max_tokens: int = 2048):
        """Run a chat completion on this agent's model; if the strong tier is
        rate-limited or unavailable, retry once on the fast tier."""
        try:
            return self.client.chat.completions.create(
                model=self.model,
                messages=messages,
                max_tokens=max_tokens
            )
        except Exception as e:
            if should_fallback(e, "strong"):
                return self.client.chat.completions.create(
                    model=resolve_model("fast"),
                    messages=messages,
                    max_tokens=max_tokens
                )
            raise

    def _run_tools(self, messages, max_tokens: int = 2048):
        """Run the real tool loop (web search + document tools) on this
        agent's model; if the strong tier is rate-limited or unavailable,
        retry once on the fast tier."""
        from modules.agent_tools import run_tool_loop, AGENT_TOOL_SCHEMAS
        try:
            return run_tool_loop(
                self.client, self.model, messages, AGENT_TOOL_SCHEMAS,
                max_tokens=max_tokens
            )
        except Exception as e:
            if should_fallback(e, "strong"):
                return run_tool_loop(
                    self.client, resolve_model("fast"), messages,
                    AGENT_TOOL_SCHEMAS, max_tokens=max_tokens
                )
            raise

    @staticmethod
    def _last_assistant_content(messages) -> str:
        """The text of the most recent assistant message, or ''."""
        for m in reversed(messages):
            if m.get("role") == "assistant" and m.get("content"):
                return m["content"]
        return ""

    def _execute_with_tools(self, task: str) -> str:
        """Run a task through the real tool loop and return the final answer."""
        messages = [
            {"role": "system", "content": self._system},
            {"role": "user", "content": self._task_prompt.format(task=task)},
        ]
        final_messages, _events = self._run_tools(messages)
        result = self._last_assistant_content(final_messages)
        self.remember(task, result)
        return result

    def _stream_with_tools(self, task: str) -> Iterator[Dict]:
        """Run a task through the real tool loop with streaming.

        Yields dicts: {"type": "tool", "content": ...} for each tool call as
        it happens, then {"type": "token", "content": ...} for answer tokens.
        """
        from modules.agent_tools import run_tool_rounds, AGENT_TOOL_SCHEMAS

        def _initial_messages():
            return [
                {"role": "system", "content": self._system},
                {"role": "user", "content": self._task_prompt.format(task=task)},
            ]

        model = self.model
        try:
            messages, events, final_content = run_tool_rounds(
                self.client, model, _initial_messages(), AGENT_TOOL_SCHEMAS,
                max_tokens=2048
            )
        except Exception as e:
            if should_fallback(e, "strong"):
                model = resolve_model("fast")
                messages, events, final_content = run_tool_rounds(
                    self.client, model, _initial_messages(), AGENT_TOOL_SCHEMAS,
                    max_tokens=2048
                )
            else:
                raise

        for ev in events:
            yield {"type": "tool", "content": ev}

        # Stream the final answer fresh. When the model already answered
        # (non-streamed), drop that answer and regenerate from the same
        # prompt/tool context so the user sees the tokens arrive.
        if final_content is not None and messages and messages[-1].get("role") == "assistant":
            stream_messages = messages[:-1]
        else:
            stream_messages = messages + [
                {"role": "user", "content": (
                    "You have used all your tool calls. Give your best answer "
                    "now based on what you have."
                )}
            ]
        stream = self.client.chat.completions.create(
            model=model, messages=stream_messages, stream=True, max_tokens=2048
        )
        for chunk in stream:
            choices = getattr(chunk, "choices", None) or []
            delta = getattr(choices[0], "delta", None) if choices else None
            text = getattr(delta, "content", None) if delta else None
            if text:
                yield {"type": "token", "content": text}
    
    def think(self, task: str) -> str:
        """Think about a task"""
        messages = [
            {"role": "system", "content": f"You are {self.name}, a {self.role}. Think step by step."},
            {"role": "user", "content": task}
        ]
        
        response = self._chat(messages)
        
        return response.choices[0].message.content
    
    def execute(self, task: str) -> str:
        """Execute a task"""
        raise NotImplementedError
    
    def remember(self, key: str, value: str):
        """Remember something"""
        self.memory.append({"key": key, "value": value, "timestamp": datetime.now().isoformat()})
    
    def recall(self, key: str) -> Optional[str]:
        """Recall something"""
        for item in reversed(self.memory):
            if item["key"] == key:
                return item["value"]
        return None


# ============ SPECIALIZED AGENTS ============

class ResearchAgent(BaseAgent):
    """Agent specialized in research and information gathering"""

    _system = (
        "You are Research Agent. research specialist who gathers and analyzes "
        "information. Be concise and direct."
    )
    _task_prompt = (
        "Research task: {task}\n\n"
        "Answer the question directly and concisely. Give the key facts and "
        "the most useful examples. Use the web_search tool for recent events "
        "or anything outside the uploaded documents, and the "
        "read_uploaded_document tool when the task refers to an uploaded "
        "file (call list_uploaded_documents first to see the file names). "
        "Do NOT analyze the user's intent, add meta-commentary, or use "
        "verbose numbered sections. Be accurate and to the point."
    )

    def __init__(self):
        super().__init__(
            name="Research Agent",
            role="research specialist who gathers and analyzes information"
        )

    def execute(self, task: str) -> str:
        """Research a topic with real tool calling (web search + document reads)."""
        return self._execute_with_tools(task)

    def execute_stream(self, task: str) -> Iterator[Dict]:
        """Research a topic with streaming (tool activity, then answer tokens)."""
        yield from self._stream_with_tools(task)


class CoderAgent(BaseAgent):
    """Agent specialized in writing and reviewing code"""

    _system = (
        "You are Coder Agent. programming expert who writes, reviews, and "
        "debugs code. Be concise and direct."
    )
    _task_prompt = (
        "Programming task: {task}\n\n"
        "Write clean, working code. Put the code first, then at most 2-3 "
        "lines of explanation. Only include comments that actually help. "
        "Skip generic warnings and notes unless they are truly needed for "
        "this specific code. You may read uploaded documents when the task "
        "references them."
    )

    def __init__(self):
        super().__init__(
            name="Coder Agent",
            role="programming expert who writes, reviews, and debugs code"
        )

    def execute(self, task: str) -> str:
        """Write or review code"""
        return self._execute_with_tools(task)

    def execute_stream(self, task: str) -> Iterator[Dict]:
        """Write/review code with streaming (tool activity, then tokens)."""
        yield from self._stream_with_tools(task)


class WriterAgent(BaseAgent):
    """Agent specialized in writing content"""

    _system = (
        "You are Writer Agent. content writer who creates clear, engaging "
        "text. Be concise and direct."
    )
    _task_prompt = (
        "Writing task: {task}\n\n"
        "Write the content. Keep it clear and direct, well-structured only "
        "where it helps readability. Match the requested tone. No filler or "
        "generic padding."
    )

    def __init__(self):
        super().__init__(
            name="Writer Agent",
            role="content writer who creates clear, engaging text"
        )

    def execute(self, task: str) -> str:
        """Write content"""
        return self._execute_with_tools(task)

    def execute_stream(self, task: str) -> Iterator[Dict]:
        """Write content with streaming (tool activity, then tokens)."""
        yield from self._stream_with_tools(task)


class AnalyzerAgent(BaseAgent):
    """Agent specialized in data analysis"""

    _system = (
        "You are Analyzer Agent. data analyst who interprets and analyzes "
        "information. Be concise and direct."
    )
    _task_prompt = (
        "Analysis task: {task}\n\n"
        "Analyze the topic directly and give the key insights as concise "
        "bullets or a short paragraph. Do NOT analyze the user's intent or "
        "add meta-commentary. Be clear and to the point. You may read "
        "uploaded documents when the task references them."
    )

    def __init__(self):
        super().__init__(
            name="Analyzer Agent",
            role="data analyst who interprets and analyzes information"
        )

    def execute(self, task: str) -> str:
        """Analyze data or information"""
        return self._execute_with_tools(task)

    def execute_stream(self, task: str) -> Iterator[Dict]:
        """Analyze with streaming (tool activity, then tokens)."""
        yield from self._stream_with_tools(task)


class PlannerAgent(BaseAgent):
    """Agent specialized in planning and strategy"""

    _system = (
        "You are Planner Agent. strategic planner who creates actionable "
        "plans. Be concise and direct."
    )
    _task_prompt = (
        "Planning task: {task}\n\n"
        "Create a concise, actionable plan: clear objectives, step-by-step "
        "actions, and a timeline if relevant. Keep it short and specific. "
        "No filler."
    )

    def __init__(self):
        super().__init__(
            name="Planner Agent",
            role="strategic planner who creates actionable plans"
        )

    def execute(self, task: str) -> str:
        """Create a plan"""
        return self._execute_with_tools(task)

    def execute_stream(self, task: str) -> Iterator[Dict]:
        """Create a plan with streaming (tool activity, then tokens)."""
        yield from self._stream_with_tools(task)


# ============ MULTI-AGENT ORCHESTRATOR ============

class MultiAgentOrchestrator:
    """
    Orchestrates multiple agents working together.
    Like a TEAM LEADER who assigns tasks to specialists.
    """
    
    def __init__(self):
        # Create all agents
        self.agents = {
            "research": ResearchAgent(),
            "coder": CoderAgent(),
            "writer": WriterAgent(),
            "analyzer": AnalyzerAgent(),
            "planner": PlannerAgent()
        }
        
        self.task_history: List[Dict] = []
    
    def route_task(self, task: str) -> str:
        """Route a task to the appropriate agent"""
        
        # Analyze which agent should handle this task
        routing_prompt = f"""Given this task: "{task}"

Which agent should handle it?
- research: For gathering information, facts, research
- coder: For writing code, debugging, programming
- writer: For writing content, articles, text
- analyzer: For analyzing data, patterns, insights
- planner: For creating plans, strategies, steps

Respond with ONLY the agent name (research/coder/writer/analyzer/planner)."""
        
        # Use Groq to determine routing (cheap helper call -> fast tier)
        client = Groq(api_key=os.getenv("GROQ_API_KEY"))
        response = client.chat.completions.create(
            model=resolve_model("fast"),
            messages=[
                {"role": "system", "content": "You are a task router."},
                {"role": "user", "content": routing_prompt}
            ],
            max_tokens=20
        )
        
        agent_name = response.choices[0].message.content.strip().lower()
        
        # Map to agent
        agent_mapping = {
            "research": "research",
            "coder": "coder",
            "writer": "writer",
            "analyzer": "analyzer",
            "planner": "planner"
        }
        
        agent_key = agent_mapping.get(agent_name, "research")
        agent = self.agents[agent_key]
        
        # Execute task
        result = agent.execute(task)
        
        # Log task
        self.task_history.append({
            "task": task,
            "agent": agent.name,
            "result_preview": result[:200],
            "timestamp": datetime.now().isoformat()
        })
        
        return f"🤖 **{agent.name}** handled this task:\n\n{result}"
    
    def collaborate(self, task: str) -> str:
        """Multiple agents collaborate on a complex task"""
        
        results = {}
        
        # Step 1: Planner creates a plan
        plan = self.agents["planner"].execute(f"Create a plan for: {task}")
        results["plan"] = plan
        
        # Step 2: Research agent gathers info
        research = self.agents["research"].execute(f"Research for this task: {task}")
        results["research"] = research
        
        # Step 3: If coding needed, coder writes code
        if any(word in task.lower() for word in ["code", "program", "function", "script"]):
            code = self.agents["coder"].execute(f"Write code for: {task}")
            results["code"] = code
        
        # Step 4: If content needed, writer writes
        if any(word in task.lower() for word in ["write", "content", "article", "document"]):
            content = self.agents["writer"].execute(f"Write content for: {task}")
            results["content"] = content
        
        # Step 5: Analyzer reviews everything
        analysis = self.agents["analyzer"].execute(
            f"Analyze these results and provide summary:\n"
            f"Plan: {plan[:500]}\n"
            f"Research: {research[:500]}"
        )
        results["analysis"] = analysis
        
        # Combine results
        final_result = "🎯 **Multi-Agent Collaboration Result:**\n\n"
        
        for key, value in results.items():
            final_result += f"### {key.title()}:\n{value}\n\n---\n\n"
        
        return final_result
    
    def get_team_status(self) -> str:
        """Get status of all agents"""
        status = "Agent Team Status:\n\n"
        
        for name, agent in self.agents.items():
            tasks_handled = len([t for t in self.task_history if agent.name in t["agent"]])
            status += f"[{agent.name}] ({agent.role})\n"
            status += f"   Tasks handled: {tasks_handled}\n\n"
        
        return status


# ============ CONVENIENCE FUNCTIONS ============

def run_single_agent(task: str, agent_type: str = "auto") -> str:
    """Run a single agent"""
    orchestrator = MultiAgentOrchestrator()
    
    if agent_type == "auto":
        return orchestrator.route_task(task)
    elif agent_type in orchestrator.agents:
        return orchestrator.agents[agent_type].execute(task)
    else:
        return f"Unknown agent type: {agent_type}"


def run_multi_agent(task: str) -> str:
    """Run multi-agent collaboration"""
    orchestrator = MultiAgentOrchestrator()
    return orchestrator.collaborate(task)


# ============ CLI ============

if __name__ == "__main__":
    print("=" * 60)
    print("  🤖 Multi-Agent System")
    print("=" * 60)
    print("  Agents: Research, Coder, Writer, Analyzer, Planner")
    print("  Commands:")
    print("    /team  - Show team status")
    print("    /multi - Run multi-agent collaboration")
    print("    quit   - Exit")
    print("=" * 60)
    
    orchestrator = MultiAgentOrchestrator()
    
    while True:
        try:
            user_input = input("\n👤 You: ").strip()
            
            if not user_input:
                continue
            
            if user_input.lower() == "quit":
                print("👋 Goodbye!")
                break
            
            if user_input.lower() == "/team":
                print(orchestrator.get_team_status())
                continue
            
            if user_input.lower() == "/multi":
                task = input("Enter task for multi-agent: ").strip()
                print("\n🤖 Collaborating...")
                result = orchestrator.collaborate(task)
                print(result)
                continue
            
            print("\n🤖 Routing task...")
            result = orchestrator.route_task(user_input)
            print(result)
            
        except KeyboardInterrupt:
            print("\n\n👋 Goodbye!")
            break
        except Exception as e:
            print(f"\n❌ Error: {str(e)}")
