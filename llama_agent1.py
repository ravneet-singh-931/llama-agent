"""
Llama Agent - ENHANCED VERSION
Now with: Memory, RAG, Web Search, Monitoring, Logging
"""

import asyncio
import os
import datetime
import math
import time
from typing import Any, Dict, List, Optional, Sequence

try:
    from dotenv import load_dotenv
except ImportError:
    def load_dotenv(*_args, **_kwargs):
        return False

from groq import Groq
from llama_index.core.agent import ReActAgent
from llama_index.core.llms.llm import LLM
from llama_index.core.base.llms.types import (
    ChatMessage,
    ChatResponse,
    CompletionResponse,
    ChatResponseGen,
    CompletionResponseGen,
    LLMMetadata,
    MessageRole,
)
from llama_index.core.bridge.pydantic import ConfigDict, PrivateAttr
from llama_index.core.tools import FunctionTool

# Import our custom modules
from modules.memory import ConversationMemory, FactMemory
from modules.rag import SimpleRAG, DocumentReader
from modules.monitoring import AgentLogger, PerformanceMonitor
from modules.secrets import get_secret
from modules.security import safe_math_eval

# ===================== SETUP =====================
load_dotenv()

# Load GROQ_API_KEY from Docker secret file or env var
_groq_key = get_secret("GROQ_API_KEY", default=os.getenv("GROQ_API_KEY", ""))
if _groq_key:
    os.environ["GROQ_API_KEY"] = _groq_key

GROQ_API_KEY = os.getenv("GROQ_API_KEY")
if not GROQ_API_KEY:
    raise SystemExit("GROQ_API_KEY not set. Add it to your environment or a .env file.")

# Initialize modules
memory = ConversationMemory()
facts = FactMemory()
rag = SimpleRAG()
logger = AgentLogger()
monitor = PerformanceMonitor()

# ===================== LLM =====================

class GroqLLM(LLM):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    model: str
    api_key: str
    base_url: Optional[str] = None
    _client: Groq = PrivateAttr()

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._client = Groq(api_key=self.api_key, base_url=self.base_url)

    @property
    def metadata(self) -> LLMMetadata:
        return LLMMetadata(
            context_window=8192,
            num_output=1024,
            is_chat_model=True,
            is_function_calling_model=False,
            model_name=self.model,
            system_role=MessageRole.SYSTEM,
        )

    def _format_messages(self, messages: Sequence[ChatMessage]) -> List[Dict[str, Any]]:
        formatted = []
        for message in messages:
            role = message.role.value if isinstance(message.role, MessageRole) else message.role
            formatted.append({"role": role, "content": message.content})
        return formatted

    def _parse_response(self, response: Any) -> ChatResponse:
        choice = response.choices[0]
        message = choice.message
        text = message.content or ""
        return ChatResponse(
            message=ChatMessage(role=MessageRole.ASSISTANT, content=text),
            raw=response,
        )

    def chat(self, messages: Sequence[ChatMessage], **kwargs: Any) -> ChatResponse:
        response = self._client.chat.completions.create(
            model=self.model,
            messages=self._format_messages(messages),
            **kwargs,
        )
        return self._parse_response(response)

    async def achat(self, messages: Sequence[ChatMessage], **kwargs: Any) -> ChatResponse:
        return await asyncio.to_thread(self.chat, messages, **kwargs)

    def complete(self, prompt: str, formatted: bool = False, **kwargs: Any) -> CompletionResponse:
        response = self.chat([ChatMessage(role=MessageRole.USER, content=prompt)], **kwargs)
        return CompletionResponse(text=response.message.content or "", raw=response)

    async def acomplete(self, prompt: str, formatted: bool = False, **kwargs: Any) -> CompletionResponse:
        return await asyncio.to_thread(self.complete, prompt, formatted=formatted, **kwargs)

    def stream_chat(self, messages: Sequence[ChatMessage], **kwargs: Any) -> ChatResponseGen:
        """Stream chat response token by token"""
        kwargs.pop("stream", None)  # Remove if present
        response = self._client.chat.completions.create(
            model=self.model,
            messages=self._format_messages(messages),
            stream=True,
            **kwargs,
        )
        def generate():
            for chunk in response:
                if chunk.choices[0].delta.content:
                    yield CompletionResponse(
                        text=chunk.choices[0].delta.content,
                        raw=chunk,
                    )
        return generate()

    def stream_complete(self, prompt: str, formatted: bool = False, **kwargs: Any) -> CompletionResponseGen:
        raise NotImplementedError("Use stream_chat instead")

    async def astream_chat(self, messages: Sequence[ChatMessage], **kwargs: Any) -> ChatResponseGen:
        return await asyncio.to_thread(self.stream_chat, messages, **kwargs)

    async def astream_complete(self, prompt: str, formatted: bool = False, **kwargs: Any) -> CompletionResponseGen:
        return await asyncio.to_thread(self.stream_complete, prompt, formatted=formatted, **kwargs)


llm = GroqLLM(model=os.getenv("LLM_MODEL", "llama-3.3-70b-versatile"), api_key=GROQ_API_KEY)

# ===================== ENHANCED TOOLS =====================

def calculator(expression: str) -> str:
    """Evaluates a mathematical expression safely (no arbitrary code)."""
    try:
        result = safe_math_eval(expression)
        return f"Result: {result}"
    except Exception as e:
        return f"Error calculating: {str(e)}"


def get_current_datetime() -> str:
    """Returns the current date and time."""
    now = datetime.datetime.now()
    return f"Current Date & Time: {now.strftime('%Y-%m-%d %H:%M:%S')}"


def text_analyzer(text: str) -> str:
    """Analyzes text and returns statistics."""
    words = len(text.split())
    characters = len(text)
    sentences = text.count('.') + text.count('!') + text.count('?')
    return f"Words: {words}, Characters: {characters}, Sentences: {sentences}"


def unit_converter(value: float, from_unit: str, to_unit: str) -> str:
    """Converts between common units."""
    conversions = {
        ("km", "miles"): lambda x: x * 0.621371,
        ("miles", "km"): lambda x: x * 1.60934,
        ("kg", "pounds"): lambda x: x * 2.20462,
        ("pounds", "kg"): lambda x: x * 0.453592,
        ("celsius", "fahrenheit"): lambda x: (x * 9/5) + 32,
        ("fahrenheit", "celsius"): lambda x: (x - 32) * 5/9,
        ("meters", "feet"): lambda x: x * 3.28084,
        ("feet", "meters"): lambda x: x * 0.3048,
    }
    key = (from_unit.lower(), to_unit.lower())
    if key in conversions:
        result = conversions[key](value)
        return f"{value} {from_unit} = {result:.4f} {to_unit}"
    return f"Conversion not supported."


def string_operations(text: str, operation: str) -> str:
    """Performs string operations."""
    operations = {
        "uppercase": text.upper(),
        "lowercase": text.lower(),
        "reverse": text[::-1],
        "capitalize": text.capitalize(),
        "title": text.title(),
    }
    op = operation.lower()
    if op in operations:
        return f"Result: {operations[op]}"
    return f"Operation not supported."


# ===================== NEW ENHANCED TOOLS =====================

def remember_fact(key: str, value: str) -> str:
    """Remember an important fact about the user or anything."""
    facts.remember(key, value)
    return f"✅ I'll remember that: {key} = {value}"


def recall_fact(key: str) -> str:
    """Recall a previously stored fact."""
    return facts.recall(key)


def list_facts() -> str:
    """List all stored facts."""
    all_facts = facts.get_all_facts()
    if not all_facts:
        return "No facts stored yet."
    result = "📝 Stored facts:\n"
    for k, v in all_facts.items():
        result += f"  - {k}: {v}\n"
    return result


def read_document(file_path: str) -> str:
    """Read a document and add it to knowledge base."""
    result = rag.add_document(file_path)
    return f"📄 {result}"


def search_documents(query: str) -> str:
    """Search loaded documents for relevant information."""
    context = rag.get_context(query)
    return context


def list_documents() -> str:
    """List all loaded documents."""
    docs = rag.list_documents()
    if not docs:
        return "No documents loaded."
    return "📚 Loaded documents:\n" + "\n".join(f"  - {d}" for d in docs)


def get_memory_stats() -> str:
    """Get memory statistics."""
    conv_stats = memory.get_stats()
    fact_stats = {"facts_stored": len(facts.get_all_facts())}
    return f"🧠 Memory stats: {conv_stats}\n📝 Facts: {fact_stats}"


def clear_memory() -> str:
    """Clear all memory."""
    memory.clear()
    facts.clear()
    return "🗑️ All memory cleared."


def get_agent_health() -> str:
    """Get agent health and performance stats."""
    health = monitor.get_health()
    log_metrics = logger.get_metrics()
    return (
        f"🏥 Health: {health['status']}\n"
        f"⏱️ Uptime: {health['uptime_human']}\n"
        f"📊 Total queries: {log_metrics['total_queries']}\n"
        f"❌ Total errors: {log_metrics['total_errors']}\n"
        f"📈 Avg response time: {log_metrics['avg_response_time']:.2f}s"
    )


# ===================== CREATE TOOLS =====================

tools = [
    FunctionTool.from_defaults(fn=calculator, name="calculator", description="Mathematical calculations"),
    FunctionTool.from_defaults(fn=get_current_datetime, name="get_datetime", description="Get current date and time"),
    FunctionTool.from_defaults(fn=text_analyzer, name="text_analyzer", description="Analyze text statistics"),
    FunctionTool.from_defaults(fn=unit_converter, name="unit_converter", description="Convert between units"),
    FunctionTool.from_defaults(fn=string_operations, name="string_operations", description="String operations"),
    FunctionTool.from_defaults(fn=remember_fact, name="remember_fact", description="Remember an important fact"),
    FunctionTool.from_defaults(fn=recall_fact, name="recall_fact", description="Recall a stored fact"),
    FunctionTool.from_defaults(fn=list_facts, name="list_facts", description="List all stored facts"),
    FunctionTool.from_defaults(fn=read_document, name="read_document", description="Read and load a document"),
    FunctionTool.from_defaults(fn=search_documents, name="search_documents", description="Search loaded documents"),
    FunctionTool.from_defaults(fn=list_documents, name="list_documents", description="List loaded documents"),
    FunctionTool.from_defaults(fn=get_memory_stats, name="get_memory_stats", description="Get memory statistics"),
    FunctionTool.from_defaults(fn=clear_memory, name="clear_memory", description="Clear all memory"),
    FunctionTool.from_defaults(fn=get_agent_health, name="get_agent_health", description="Get agent health stats"),
]

# ===================== CREATE AGENT =====================

agent = ReActAgent(
    tools=tools,
    llm=llm,
    verbose=True,
    streaming=False,
    max_iterations=20,
    max_retries=3,
)


# ===================== ENHANCED AGENT COORDINATION =====================

from dataclasses import dataclass
from typing import Dict, List, Optional, Any
from collections import defaultdict
import asyncio

@dataclass
class Complexity:
    level: int
    factors: List[str]

@dataclass
class RoutingResult:
    agent: Any
    reasoning: str
    confidence: float

@dataclass
class AgentContext:
    conversation_history: List[Any]
    shared_knowledge: Dict[str, Any]
    active_tasks: List[str]
    last_updated: Any

class EnhancedAgentOrchestrator:
    def __init__(self, agents: List[Any]):
        self.agents = {agent.name: agent for agent in agents}
        self.routing_engine = SmartRoutingEngine()
        self.context_manager = ContextSharingService()
    
    async def route_query(self, query: str, context: Any) -> RoutingResult:
        complexity = await self.routing_engine.analyze_complexity(query)
        context_relevance = await self.context_manager.analyze_context_relevance(query, context)
        
        candidates = self.get_relevant_agents(query, complexity, context_relevance)
        selected_agent = self.select_best_agent(candidates, query)
        
        return RoutingResult(
            agent=selected_agent,
            reasoning=self.generate_routing_reasoning(query, complexity, context_relevance),
            confidence=self.calculate_confidence(query, selected_agent)
        )
    
    def get_relevant_agents(self, query: str, complexity: Complexity, context_relevance: float) -> List[Any]:
        return [agent for agent in self.agents.values() 
                if self.is_agent_relevant(agent, query, complexity, context_relevance)]
    
    def select_best_agent(self, candidates: List[Any], query: str) -> Any:
        if not candidates:
            return self.agents.get("research")  # Default fallback
        
        # Score candidates based on query match
        scored = [(agent, self.calculate_agent_score(agent, query)) for agent in candidates]
        scored.sort(key=lambda x: x[1], reverse=True)
        return scored[0][0]
    
    def generate_routing_reasoning(self, query: str, complexity: Complexity, context_relevance: float) -> str:
        factors = []
        
        if complexity.level > 5:
            factors.append("high complexity")
        if context_relevance > 0.7:
            factors.append("strong context match")
        if len(query) < 20:
            factors.append("short query")
        
        if factors:
            return f"Query routed based on: {', '.join(factors)} (confidence: {self.calculate_confidence(query, self.agents.get('research'))})"
        return "Query routed to research agent (default)"
    
    def calculate_confidence(self, query: str, agent: Any) -> float:
        query_words = [w for w in query.lower().split() if len(w) > 3]
        agent_keywords = getattr(agent, 'keywords', []) or []
        
        if not query_words or not agent_keywords:
            return 0.5
        
        matches = sum(1 for word in query_words 
                     if any(keyword.lower() in word for keyword in agent_keywords))
        
        return min(1.0, matches / min(len(query_words), len(agent_keywords)) * 1.5)
    
    def is_agent_relevant(self, agent: Any, query: str, complexity: Complexity, context_relevance: float) -> bool:
        # Simple relevance check
        return True
    
    def calculate_agent_score(self, agent: Any, query: str) -> float:
        query_words = [w for w in query.lower().split() if len(w) > 3]
        agent_keywords = getattr(agent, 'keywords', []) or []
        
        if not query_words or not agent_keywords:
            return 0.5
        
        matches = sum(1 for word in query_words 
                     if any(keyword.lower() in word for keyword in agent_keywords))
        
        return min(1.0, matches / min(len(query_words), len(agent_keywords)) * 1.5)

# Smart Routing Engine
class SmartRoutingEngine:
    async def analyze_complexity(self, query: str) -> Complexity:
        word_count = len(query.split())
        has_technical_terms = self.detect_technical_terms(query)
        has_specific_topics = self.detect_specific_topics(query)
        has_context_clues = self.detect_context_clues(query)
        
        complexity = 1
        
        if word_count > 50:
            complexity += 3
        elif word_count > 20:
            complexity += 2
        elif word_count > 10:
            complexity += 1
        
        if has_technical_terms:
            complexity += 2
        if has_specific_topics:
            complexity += 1
        if has_context_clues:
            complexity += 1
        
        return Complexity(
            level=min(10, complexity),
            factors=self.generate_complexity_factors(query)
        )
    
    def detect_technical_terms(self, query: str) -> bool:
        technical_terms = [
            "api", "database", "algorithm", "function", "class", "method",
            "interface", "component", "service", "architecture", "framework",
            "library", "protocol", "integration", "deployment", "configuration"
        ]
        return any(term in query.lower() for term in technical_terms)
    
    def detect_specific_topics(self, query: str) -> bool:
        specific_topics = [
            "machine learning", "neural network", "deep learning", "ai model",
            "data science", "big data", "cloud computing", "devops",
            "microservices", "rest api", "graphql", "docker", "kubernetes"
        ]
        return any(topic in query.lower() for topic in specific_topics)
    
    def detect_context_clues(self, query: str) -> bool:
        return any(clue in query for clue in ["?", "please", "help", "need"])
    
    def generate_complexity_factors(self, query: str) -> List[str]:
        factors = []
        
        if len(query.split()) > 50:
            factors.append("long_query")
        if self.detect_technical_terms(query):
            factors.append("technical")
        if self.detect_specific_topics(query):
            factors.append("specific_topic")
        if self.detect_context_clues(query):
            factors.append("uncertain")
        
        return factors

# Context Sharing Service
class ContextSharingService:
    def __init__(self):
        self.contexts: Dict[str, AgentContext] = {}
    
    async def share_context(self, source_agent: str, target_agents: List[str], context: AgentContext) -> None:
        # Store context for source agent
        self.contexts[source_agent] = context
        
        # Share with target agents
        for target_agent in target_agents:
            if target_agent in self.contexts:
                # Merge contexts
                existing_context = self.contexts[target_agent]
                merged_context = self.merge_contexts(existing_context, context)
                self.contexts[target_agent] = merged_context
    
    async def retrieve_context(self, agent_id: str, query: str) -> AgentContext:
        if agent_id not in self.contexts:
            return self.create_default_context()
        
        context = self.contexts[agent_id]
        
        # Filter context based on query relevance
        return self.filter_context_by_query(context, query)
    
    def merge_contexts(self, context1: AgentContext, context2: AgentContext) -> AgentContext:
        return AgentContext(
            conversation_history=context1.conversation_history + context2.conversation_history,
            shared_knowledge={**context1.shared_knowledge, **context2.shared_knowledge},
            active_tasks=context1.active_tasks + context2.active_tasks,
            last_updated=__import__('datetime').datetime.now()
        )
    
    def filter_context_by_query(self, context: AgentContext, query: str) -> AgentContext:
        relevant_history = [msg for msg in context.conversation_history 
                           if self.is_message_relevant(msg, query)]
        
        relevant_knowledge = {k: v for k, v in context.shared_knowledge.items()
                             if self.is_knowledge_relevant(k, v, query)}
        
        return AgentContext(
            conversation_history=relevant_history,
            shared_knowledge=relevant_knowledge,
            active_tasks=context.active_tasks,
            last_updated=context.last_updated
        )
    
    def is_message_relevant(self, message: Any, query: str) -> bool:
        message_text = str(message.content).lower() if hasattr(message, 'content') else str(message).lower()
        query_words = query.lower().split()
        return any(word in message_text for word in query_words if len(word) > 3)
    
    def is_knowledge_relevant(self, key: str, value: Any, query: str) -> bool:
        key_lower = key.lower()
        query_lower = query.lower()
        return key_lower in query_lower or (isinstance(value, str) and query_lower in value.lower())
    
    def create_default_context(self) -> AgentContext:
        return AgentContext(
            conversation_history=[],
            shared_knowledge={},
            active_tasks=[],
            last_updated=__import__('datetime').datetime.now()
        )
    
async def analyze_context_relevance(self, query: str, context: Any) -> float:
        # Simple relevance analysis
        return 0.5

async def run_agent(user_input: str, memory: Optional[Any] = None,
                    facts: Optional[Any] = None,
                    user_id: str = "guest", conversation_id: str = "default") -> str:
    mem = memory if memory is not None else globals().get("memory")
    fact_store = facts if facts is not None else globals().get("facts")

    # Add context from memory (scoped to this user's conversation)
    context = mem.get_context_string(n=5, user_id=user_id, conversation_id=conversation_id)
    fact_context = fact_store.get_context_string()
    
    # Log the interaction
    monitor.track_request()
    start_time = time.time()
    
    try:
        handler = agent.run(user_input)
        output = await asyncio.wait_for(handler, timeout=60)
        result = str(output)
        
        # Log success
        elapsed = time.time() - start_time
        mem.add_message("user", user_input, user_id=user_id, conversation_id=conversation_id)
        mem.add_message("assistant", result, user_id=user_id, conversation_id=conversation_id)
        logger.log_interaction(user_input, result, [], elapsed)
        
        return result
        
    except Exception as e:
        elapsed = time.time() - start_time
        monitor.track_error()
        logger.log_error(e, context=f"User input: {user_input}")
        return f"Error: {str(e)}"


# ===================== STREAMING AGENT =====================

def run_agent_stream(user_input: str, memory: Optional[Any] = None,
                     facts: Optional[Any] = None,
                     user_id: str = "guest", conversation_id: str = "default"):
    """
    Stream agent response token by token.
    Yields text chunks for st.write_stream() in Streamlit.
    """
    mem = memory if memory is not None else globals().get("memory")
    fact_store = facts if facts is not None else globals().get("facts")
    client = Groq(api_key=GROQ_API_KEY)

    # Build messages with context (scoped to this user's conversation)
    context = mem.get_context_string(n=5, user_id=user_id, conversation_id=conversation_id)
    fact_context = fact_store.get_context_string()
    system_prompt = (
        "You are Llama Agent, a helpful AI assistant with tools for math, "
        "text analysis, unit conversion, memory, and document search. "
        "Always respond in English. Be concise and helpful."
    )

    messages = [
        {"role": "system", "content": system_prompt},
    ]

    if context:
        messages.append({"role": "user", "content": f"Previous context:\n{context}"})
        messages.append({"role": "assistant", "content": "I understand the context."})

    if fact_context:
        messages.append({"role": "user", "content": fact_context})
        messages.append({"role": "assistant", "content": "I'll keep that in mind."})

    messages.append({"role": "user", "content": user_input})

    # Stream from Groq API
    try:
        stream = client.chat.completions.create(
            model=os.getenv("LLM_MODEL", "llama-3.3-70b-versatile"),
            messages=messages,
            stream=True,
            max_tokens=2048,
        )

        full_response = ""
        for chunk in stream:
            if chunk.choices[0].delta.content:
                token = chunk.choices[0].delta.content
                full_response += token
                yield token
    except Exception as e:
        # Never let the stream die silently - yield a readable error instead
        yield f"\n\n⚠️ Error while generating response: {e}"

    # Save to memory after streaming is done
    mem.add_message("user", user_input, user_id=user_id, conversation_id=conversation_id)
    mem.add_message("assistant", full_response, user_id=user_id, conversation_id=conversation_id)
    monitor.track_request()


def main():
    print("=" * 60)
    print("  🤖 Llama Agent - ENHANCED VERSION")
    print("=" * 60)
    print("  NEW Features:")
    print("  🧠 Memory (remembers conversations)")
    print("  📄 RAG (reads documents)")
    print("  📊 Monitoring & Logging")
    print("  🔧 14 Tools available!")
    print("\n  Type 'quit' to exit")
    print("=" * 60)
    
    while True:
        try:
            user_input = input("\n👤 You: ").strip()
            
            if not user_input:
                print("Please enter something!")
                continue
            
            if user_input.lower() in ["quit", "exit", "bye"]:
                print("👋 Goodbye!")
                break
            
            print("\n🤖 Agent: Thinking...")
            result = asyncio.run(run_agent(user_input))
            print(f"\n🤖 Agent: {result}")
            
        except KeyboardInterrupt:
            print("\n\n👋 Goodbye!")
            break
        except Exception as e:
            print(f"\n❌ Error: {str(e)}")


if __name__ == "__main__":
    main()
