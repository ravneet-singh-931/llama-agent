"""
Multi-Agent System
Multiple agents working together like a TEAM!
"""

import asyncio
import os
import sys
from typing import List, Dict, Any, Optional
from datetime import datetime

sys.path.insert(0, os.path.dirname(__file__))

try:
    from dotenv import load_dotenv
except ImportError:
    def load_dotenv(*_args, **_kwargs):
        return False

from groq import Groq
from modules.secrets import get_secret

load_dotenv()

# Load GROQ_API_KEY from Docker secret file or env var
_groq_key = get_secret("GROQ_API_KEY", default=os.getenv("GROQ_API_KEY", ""))
if _groq_key:
    os.environ["GROQ_API_KEY"] = _groq_key


# ============ BASE AGENT ============

class BaseAgent:
    """Base class for all agents"""
    
    def __init__(self, name: str, role: str, model: str = "llama-3.3-70b-versatile"):
        self.name = name
        self.role = role
        self.model = model
        self.client = Groq(api_key=os.getenv("GROQ_API_KEY"))
        self.memory: List[Dict] = []
    
    def think(self, task: str) -> str:
        """Think about a task"""
        messages = [
            {"role": "system", "content": f"You are {self.name}, a {self.role}. Think step by step."},
            {"role": "user", "content": task}
        ]
        
        response = self.client.chat.completions.create(
            model=self.model,
            messages=messages,
            max_tokens=1024
        )
        
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
    
    def __init__(self):
        super().__init__(
            name="Research Agent",
            role="research specialist who gathers and analyzes information"
        )
    
    def execute(self, task: str) -> str:
        """Research a topic"""
        thinking = self.think(f"I need to research: {task}\n\nWhat do I need to find out?")
        
        prompt = f"""Research this topic thoroughly: {task}

Provide:
1. Key facts and information
2. Different perspectives
3. Relevant examples
4. Sources if possible

Be comprehensive and accurate."""
        
        messages = [
            {"role": "system", "content": f"You are {self.name}. {self.role}."},
            {"role": "user", "content": prompt}
        ]
        
        response = self.client.chat.completions.create(
            model=self.model,
            messages=messages,
            max_tokens=2048
        )
        
        result = response.choices[0].message.content
        self.remember(task, result)
        return result


class CoderAgent(BaseAgent):
    """Agent specialized in writing and reviewing code"""
    
    def __init__(self):
        super().__init__(
            name="Coder Agent",
            role="programming expert who writes, reviews, and debugs code"
        )
    
    def execute(self, task: str) -> str:
        """Write or review code"""
        prompt = f"""Programming task: {task}

Provide:
1. Clean, working code
2. Comments explaining the code
3. Example usage
4. Any warnings or considerations

Write production-quality code."""
        
        messages = [
            {"role": "system", "content": f"You are {self.name}. {self.role}."},
            {"role": "user", "content": prompt}
        ]
        
        response = self.client.chat.completions.create(
            model=self.model,
            messages=messages,
            max_tokens=2048
        )
        
        result = response.choices[0].message.content
        self.remember(task, result)
        return result


class WriterAgent(BaseAgent):
    """Agent specialized in writing content"""
    
    def __init__(self):
        super().__init__(
            name="Writer Agent",
            role="content writer who creates clear, engaging text"
        )
    
    def execute(self, task: str) -> str:
        """Write content"""
        prompt = f"""Writing task: {task}

Write:
1. Clear, engaging content
2. Well-structured with headers
3. Appropriate tone for the audience
4. Error-free grammar

Create high-quality content."""
        
        messages = [
            {"role": "system", "content": f"You are {self.name}. {self.role}."},
            {"role": "user", "content": prompt}
        ]
        
        response = self.client.chat.completions.create(
            model=self.model,
            messages=messages,
            max_tokens=2048
        )
        
        result = response.choices[0].message.content
        self.remember(task, result)
        return result


class AnalyzerAgent(BaseAgent):
    """Agent specialized in data analysis"""
    
    def __init__(self):
        super().__init__(
            name="Analyzer Agent",
            role="data analyst who interprets and analyzes information"
        )
    
    def execute(self, task: str) -> str:
        """Analyze data or information"""
        prompt = f"""Analysis task: {task}

Analyze:
1. Key patterns and trends
2. Important insights
3. Potential implications
4. Recommendations

Provide thorough analysis."""
        
        messages = [
            {"role": "system", "content": f"You are {self.name}. {self.role}."},
            {"role": "user", "content": prompt}
        ]
        
        response = self.client.chat.completions.create(
            model=self.model,
            messages=messages,
            max_tokens=2048
        )
        
        result = response.choices[0].message.content
        self.remember(task, result)
        return result


class PlannerAgent(BaseAgent):
    """Agent specialized in planning and strategy"""
    
    def __init__(self):
        super().__init__(
            name="Planner Agent",
            role="strategic planner who creates actionable plans"
        )
    
    def execute(self, task: str) -> str:
        """Create a plan"""
        prompt = f"""Planning task: {task}

Create a plan with:
1. Clear objectives
2. Step-by-step actions
3. Timeline/deadlines
4. Resources needed
5. Potential risks and mitigation

Make it actionable and specific."""
        
        messages = [
            {"role": "system", "content": f"You are {self.name}. {self.role}."},
            {"role": "user", "content": prompt}
        ]
        
        response = self.client.chat.completions.create(
            model=self.model,
            messages=messages,
            max_tokens=2048
        )
        
        result = response.choices[0].message.content
        self.remember(task, result)
        return result


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
        
        # Use Groq to determine routing
        client = Groq(api_key=os.getenv("GROQ_API_KEY"))
        response = client.chat.completions.create(
            model="llama-3.3-70b-versatile",
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
