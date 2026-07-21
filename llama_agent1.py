"""
Llama Agent - ENHANCED VERSION
Now with: Memory, RAG, Web Search, Monitoring, Logging
"""

import asyncio
import os
import datetime
import math
import time
from typing import Any, Sequence

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

# ===================== SETUP =====================
load_dotenv()

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
    base_url: str | None = None
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

    def _format_messages(self, messages: Sequence[ChatMessage]) -> list[dict[str, Any]]:
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


llm = GroqLLM(model="llama-3.3-70b-versatile", api_key=GROQ_API_KEY)

# ===================== ENHANCED TOOLS =====================

def calculator(expression: str) -> str:
    """Evaluates a mathematical expression."""
    try:
        allowed_names = {k: v for k, v in math.__dict__.items() if not k.startswith("__")}
        result = eval(expression, {"__builtins__": {}}, allowed_names)
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
)

# ===================== MAIN LOOP =====================

async def run_agent(user_input: str) -> str:
    # Add context from memory
    context = memory.get_context_string(n=5)
    fact_context = facts.get_context_string()
    
    # Log the interaction
    monitor.track_request()
    start_time = time.time()
    
    try:
        handler = agent.run(user_input)
        output = await handler
        result = str(output)
        
        # Log success
        elapsed = time.time() - start_time
        memory.add_message("user", user_input)
        memory.add_message("assistant", result)
        logger.log_interaction(user_input, result, [], elapsed)
        
        return result
        
    except Exception as e:
        elapsed = time.time() - start_time
        monitor.track_error()
        logger.log_error(e, context=f"User input: {user_input}")
        return f"Error: {str(e)}"


# ===================== STREAMING AGENT =====================

def run_agent_stream(user_input: str):
    """
    Stream agent response token by token.
    Yields text chunks for st.write_stream() in Streamlit.
    """
    client = Groq(api_key=GROQ_API_KEY)

    # Build messages with context
    context = memory.get_context_string(n=5)
    fact_context = facts.get_context_string()
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
    stream = client.chat.completions.create(
        model="llama-3.3-70b-versatile",
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

    # Save to memory after streaming is done
    memory.add_message("user", user_input)
    memory.add_message("assistant", full_response)
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
