"""
Llama Agent - ENHANCED VERSION
Now with: Memory, RAG, Web Search, Monitoring, Logging
"""

import asyncio
import hashlib
import os
import datetime
import threading
import time
from collections import OrderedDict
from contextlib import contextmanager, nullcontext
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
from modules.prompt_rules import common_rules, chat_max_tokens
from modules.memory import ConversationMemory, FactMemory
from modules.rag import SimpleRAG
from modules.monitoring import AgentLogger, PerformanceMonitor
from modules.secrets import get_secret
from modules.security import safe_math_eval
from modules.model_tiers import resolve_model
from modules.security_hardening import PROMPT_INJECTION_GUARD
from modules.doc_scope import owner_dir_name, scoped
from modules.request_context import bind_owner, get_owner

# OpenTelemetry tracer for agent-level spans (best-effort; degrades to a no-op
# tracer when Phoenix/OTel isn't configured, e.g. the CLI).
try:
    from opentelemetry import trace as _otel_trace
    _AGENT_TRACER = _otel_trace.get_tracer("llama-agent")
except Exception:  # nosec B112
    _AGENT_TRACER = None

import contextvars

# Per-request identity, set by run_agent / run_agent_stream so the agent's
# tool functions (remember_fact, recall_fact, clear_memory, ...) operate on
# the correct tenant instead of the shared default.
_current_user_id = contextvars.ContextVar("current_user_id", default="guest")
_current_conversation_id = contextvars.ContextVar("current_conversation_id", default="default")

# Groq client timeouts (env-overridable). Without these a stalled HTTP
# connection can hang the streaming path forever.
GROQ_TIMEOUT = float(os.getenv("GROQ_TIMEOUT", "60"))
GROQ_MAX_RETRIES = int(os.getenv("GROQ_MAX_RETRIES", "2"))

# Bound for the agent.run() workflow (mirrors api.py's AGENT_TIMEOUT so the CLI
# path and the web path use the same limit instead of a magic 60s).
AGENT_TIMEOUT = float(os.getenv("AGENT_TIMEOUT", "60"))

# ---- run_agent result cache + single-flight -------------------------------
# Bounded LRU of recent ReAct answers keyed by a hash of the full prompt (which
# already embeds per-user facts/context, so the cache stays per-user). A short
# TTL avoids serving stale answers after facts change. The in-flight map
# coalesces concurrent identical requests into a single LLM run.
_RUN_CACHE: "OrderedDict[str, str]" = OrderedDict()
_RUN_CACHE_TTL: Dict[str, float] = {}
_RUN_CACHE_MAX = int(os.getenv("AGENT_CACHE_MAX", "256"))
_RUN_CACHE_TTL_SECONDS = float(os.getenv("AGENT_CACHE_TTL", "60"))
_run_cache_lock = threading.Lock()
_inflight: Dict[str, "asyncio.Future"] = {}
_inflight_lock = threading.Lock()


def _run_cache_get(key: str):
    with _run_cache_lock:
        if key not in _RUN_CACHE:
            return None
        if time.time() - _RUN_CACHE_TTL.get(key, 0) > _RUN_CACHE_TTL_SECONDS:
            _RUN_CACHE.pop(key, None)
            _RUN_CACHE_TTL.pop(key, None)
            return None
        _RUN_CACHE.move_to_end(key)
        return _RUN_CACHE[key]


def _run_cache_put(key: str, value: str):
    with _run_cache_lock:
        _RUN_CACHE[key] = value
        _RUN_CACHE_TTL[key] = time.time()
        _RUN_CACHE.move_to_end(key)
        while len(_RUN_CACHE) > _RUN_CACHE_MAX:
            old, _ = _RUN_CACHE.popitem(last=False)
            _RUN_CACHE_TTL.pop(old, None)


def _budget_context_string(mem, user_id, conversation_id, max_tokens: int = 2000) -> str:
    """Token-budgeted conversation context (mirrors api.py's _window_messages).

    Replaces the fixed n=5 turn window so long conversations can't blow the
    context window or spike cost: keep as many recent turns as fit in
    max_tokens (approx 4 chars/token) instead of a hard turn count.
    """
    try:
        msgs = mem.get_messages(user_id=user_id, conversation_id=conversation_id)
    except Exception:  # nosec B112
        msgs = []
    if not msgs:
        return "No conversation history yet."
    kept = []
    used = 0
    for m in reversed(msgs):
        content = (m.get("content") or "")[:200]  # parity with get_context_string cap
        t = len(content) // 4 + 1
        if used + t > max_tokens and kept:
            break
        used += t
        kept.append((m.get("role", "user"), content))
    kept.reverse()
    lines = ["Recent conversation history:"]
    for role, content in kept:
        r = "User" if role == "user" else "Assistant"
        lines.append(f"{r}: {content}")
    return "\n".join(lines)


def _span_cm(name: str, user_id: str, conversation_id: str):
    """Context manager yielding an OTel span (or a null context).

    The identity attributes are set HERE rather than at each call site: the
    streaming path is the one /chat/stream actually uses, and it previously
    passed ``user_id``/``conversation_id`` in and then dropped them, so every
    stream span was anonymous. A span tree you cannot attribute to a user is
    a timing diagram, not a trace.
    """
    if _AGENT_TRACER is None:
        return nullcontext()
    cm = _AGENT_TRACER.start_as_current_span(name)

    @contextmanager
    def _with_attrs():
        with cm as span:
            if span is not None and hasattr(span, "set_attribute"):
                span.set_attribute("user.id", str(user_id or "guest"))
                span.set_attribute("conversation.id", str(conversation_id or ""))
                # Which build served this run - the only way to answer
                # "was this the old prompt or the new one?" from a trace.
                span.set_attribute("config.commit",
                                   os.getenv("GIT_SHA", "unknown"))
            yield span

    return _with_attrs()

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
    # Part of the app making the calls (metrics label): an agent, chat, rag...
    component: str = "other"
    _client: Groq = PrivateAttr()

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._client = Groq(api_key=self.api_key, base_url=self.base_url, timeout=GROQ_TIMEOUT, max_retries=GROQ_MAX_RETRIES)

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
            entry: Dict[str, Any] = {"role": role, "content": message.content}
            # Preserve tool-calling fields (multi-agent tool loop) that a
            # content-only mapping would drop: assistant messages carry
            # tool_calls, tool messages carry tool_call_id.
            if message.additional_kwargs.get("tool_calls"):
                entry["tool_calls"] = message.additional_kwargs["tool_calls"]
            if message.additional_kwargs.get("tool_call_id"):
                entry["tool_call_id"] = message.additional_kwargs["tool_call_id"]
            formatted.append(entry)
        return formatted

    def _parse_response(self, response: Any) -> ChatResponse:
        choice = response.choices[0]
        message = choice.message
        text = message.content or ""
        return ChatResponse(
            message=ChatMessage(role=MessageRole.ASSISTANT, content=text),
            raw=response,
        )

    def _record_usage(self, usage: Any) -> None:
        if usage is None:
            return
        try:
            from modules.prometheus_metrics import record_llm_usage
            get = (lambda k: usage.get(k)) if isinstance(usage, dict) else (lambda k: getattr(usage, k, 0))
            record_llm_usage(self.model, get("prompt_tokens") or 0, get("completion_tokens") or 0,
                             component=self.component)
        except Exception:
            pass

    def chat(self, messages: Sequence[ChatMessage], **kwargs: Any) -> ChatResponse:
        response = self._client.chat.completions.create(
            model=self.model,
            messages=self._format_messages(messages),
            **kwargs,
        )
        self._record_usage(getattr(response, "usage", None))
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
                # Groq reports token usage on the final chunk (x_groq.usage);
                # that chunk may carry no choices at all.
                x_groq = getattr(chunk, "x_groq", None)
                usage = getattr(chunk, "usage", None) or (
                    getattr(x_groq, "usage", None) if x_groq is not None else None)
                if usage is not None:
                    self._record_usage(usage)
                choices = getattr(chunk, "choices", None) or []
                if choices and choices[0].delta.content:
                    yield CompletionResponse(
                        text=choices[0].delta.content,
                        raw=chunk,
                    )
        return generate()

    def stream_complete(self, prompt: str, formatted: bool = False, **kwargs: Any) -> CompletionResponseGen:
        raise NotImplementedError("Use stream_chat instead")

    async def astream_chat(self, messages: Sequence[ChatMessage], **kwargs: Any) -> ChatResponseGen:
        return await asyncio.to_thread(self.stream_chat, messages, **kwargs)

    async def astream_complete(self, prompt: str, formatted: bool = False, **kwargs: Any) -> CompletionResponseGen:
        return await asyncio.to_thread(self.stream_complete, prompt, formatted=formatted, **kwargs)


# One-shot (non-streaming) agent runs use the strong model tier; the
# streaming path below uses the fast tier for low latency.
llm = GroqLLM(model=resolve_model("strong"), api_key=GROQ_API_KEY, component="chat")

# Streaming agent runs (run_agent_stream) use the fast tier. Keeping a
# dedicated llama-index LLM here - instead of calling the raw Groq SDK
# client - ensures streaming calls are instrumented and show up as
# GroqLLM.* spans in Phoenix.
stream_llm = GroqLLM(model=resolve_model("fast"), api_key=GROQ_API_KEY, component="chat")

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
    facts.remember(key, value, user_id=_current_user_id.get())
    return f"✅ I'll remember that: {key} = {value}"


def recall_fact(key: str) -> str:
    """Recall a previously stored fact."""
    return facts.recall(key, user_id=_current_user_id.get())


def list_facts() -> str:
    """List all stored facts."""
    all_facts = facts.get_all_facts(user_id=_current_user_id.get())
    if not all_facts:
        return "No facts stored yet."
    result = "📝 Stored facts:\n"
    for k, v in all_facts.items():
        result += f"  - {k}: {v}\n"
    return result


def _user_rag():
    """The shared RAG store scoped to the current user (tenant isolation)."""
    return scoped(rag, get_owner() or _current_user_id.get())


def read_document(file_path: str) -> str:
    """Add one of the user's uploaded documents to the knowledge base.

    Only files inside the user's own upload directory are accepted - the
    model must never be able to index arbitrary server files (/etc/passwd,
    .env, other users' uploads).
    """
    owner = get_owner() or _current_user_id.get()
    upload_root = os.path.realpath(os.path.join(
        os.getenv("UPLOAD_DIR", "uploads"), owner_dir_name(owner)
    ))
    candidate = os.path.realpath(
        file_path if os.path.isabs(file_path)
        else os.path.join(upload_root, os.path.basename(file_path))
    )
    if not candidate.startswith(upload_root + os.sep) or not os.path.isfile(candidate):
        return "📄 Only documents you uploaded can be read. Upload the file first."
    result = _user_rag().add_document(candidate)
    return f"📄 {result}"


def search_documents(query: str) -> str:
    """Search loaded documents for relevant information."""
    context = _user_rag().get_context(query)
    return context


def list_documents() -> str:
    """List all loaded documents."""
    docs = _user_rag().list_documents()
    if not docs:
        return "No documents loaded."
    return "📚 Loaded documents:\n" + "\n".join(f"  - {d}" for d in docs)


def get_memory_stats() -> str:
    """Get memory statistics."""
    conv_stats = memory.get_stats()
    fact_stats = {"facts_stored": len(facts.get_all_facts(user_id=_current_user_id.get()))}
    return f"🧠 Memory stats: {conv_stats}\n📝 Facts: {fact_stats}"


def clear_memory() -> str:
    """Clear this user's memory (scoped to the current tenant)."""
    memory.clear(user_id=_current_user_id.get())
    facts.clear(user_id=_current_user_id.get())
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

class GuardedTool(FunctionTool):
    """Wraps a FunctionTool so its string output is marked as untrusted data.

    PROMPT_INJECTION_GUARD tells the model to treat tool results as data, not
    instructions; delimiting the output makes that explicit and contains any
    injection that arrives through retrieved documents or search results.
    """

    def __init__(self, base: "FunctionTool"):
        # Reuse the wrapped tool's function + name/description so the tool
        # schema is preserved exactly. Build a reference tool via
        # from_defaults (the version-stable public constructor, which accepts
        # name/description in all llama-index releases), then initialize the
        # base class from its components. This tolerates llama-index versions
        # where FunctionTool.__init__ takes `metadata=` instead of
        # `name=`/`description=`.
        ref = FunctionTool.from_defaults(
            fn=base.fn,
            name=base.metadata.name,
            description=base.metadata.description,
        )
        try:
            super().__init__(fn=ref.fn, metadata=ref.metadata)
        except TypeError:
            super().__init__(
                fn=ref.fn,
                name=ref.metadata.name,
                description=ref.metadata.description,
            )
        self._base = base

    def call(self, *args, **kwargs):
        return self._guard(self._base.call(*args, **kwargs))

    async def acall(self, *args, **kwargs):
        return self._guard(await self._base.acall(*args, **kwargs))

    @staticmethod
    def _guard(output):
        if hasattr(output, "content") and isinstance(output.content, str):
            output.content = (
                f"<<UNTRUSTED_DATA>>\n{output.content}\n<</UNTRUSTED_DATA>>"
            )
            return output
        if isinstance(output, str):
            return f"<<UNTRUSTED_DATA>>\n{output}\n<</UNTRUSTED_DATA>>"
        return output


tools = [
    GuardedTool(FunctionTool.from_defaults(fn=calculator, name="calculator", description="Mathematical calculations")),
    GuardedTool(FunctionTool.from_defaults(fn=get_current_datetime, name="get_datetime", description="Get current date and time")),
    GuardedTool(FunctionTool.from_defaults(fn=text_analyzer, name="text_analyzer", description="Analyze text statistics")),
    GuardedTool(FunctionTool.from_defaults(fn=unit_converter, name="unit_converter", description="Convert between units")),
    GuardedTool(FunctionTool.from_defaults(fn=string_operations, name="string_operations", description="String operations")),
    GuardedTool(FunctionTool.from_defaults(fn=remember_fact, name="remember_fact", description="Remember an important fact")),
    GuardedTool(FunctionTool.from_defaults(fn=recall_fact, name="recall_fact", description="Recall a stored fact")),
    GuardedTool(FunctionTool.from_defaults(fn=list_facts, name="list_facts", description="List all stored facts")),
    GuardedTool(FunctionTool.from_defaults(fn=read_document, name="read_document", description="Read and load a document")),
    GuardedTool(FunctionTool.from_defaults(fn=search_documents, name="search_documents", description="Search loaded documents")),
    GuardedTool(FunctionTool.from_defaults(fn=list_documents, name="list_documents", description="List loaded documents")),
    GuardedTool(FunctionTool.from_defaults(fn=get_memory_stats, name="get_memory_stats", description="Get memory statistics")),
    GuardedTool(FunctionTool.from_defaults(fn=clear_memory, name="clear_memory", description="Clear all memory")),
    GuardedTool(FunctionTool.from_defaults(fn=get_agent_health, name="get_agent_health", description="Get agent health stats")),
]

# ===================== CREATE AGENT =====================

GENERAL_SYSTEM_PROMPT = (
    "You are Llama Agent, a helpful, concise AI assistant with tools for math, "
    "text analysis, unit conversion, memory, and document search. "
    "Always respond in English. Be concise and direct: answer the question, "
    "show code when asked, and skip verbose introductions, filler, redundant "
    "headings, and marketing language.\n\n"
    + PROMPT_INJECTION_GUARD
)

agent = ReActAgent(
    tools=tools,
    llm=llm,
    verbose=True,
    streaming=False,
    max_iterations=20,
    max_retries=3,
    system_prompt=GENERAL_SYSTEM_PROMPT,
)


async def run_agent(user_input: str, memory: Optional[Any] = None,
                    facts: Optional[Any] = None,
                    user_id: str = "guest", conversation_id: str = "default") -> str:
    mem = memory if memory is not None else globals().get("memory")
    fact_store = facts if facts is not None else globals().get("facts")
    _current_user_id.set(user_id)
    _current_conversation_id.set(conversation_id)
    bind_owner(user_id)

    # Session memory: token-budgeted context (mirrors api.py's _window_messages)
    # so long conversations can't blow the context window or spike cost.
    context = _budget_context_string(mem, user_id, conversation_id)
    fact_context = fact_store.get_context_string(user_id=user_id)

    # Log the interaction
    monitor.track_request()
    start_time = time.time()

    # Build the prompt: user message + recent conversation + known facts.
    # Conversation history and stored facts are user-controlled, so they are
    # delimited as untrusted data to contain prompt-injection attempts.
    prompt = f"<<UNTRUSTED_DATA>>\n{user_input}\n<</UNTRUSTED_DATA>>"
    if context and "No conversation history yet." not in context:
        prompt = (
            f"Recent conversation context:\n"
            f"<<UNTRUSTED_DATA>>\n{context}\n<</UNTRUSTED_DATA>>\n\nUser: {prompt}"
        )
    if fact_context and "No facts stored yet." not in fact_context:
        prompt = (
            f"{prompt}\n\nKnown facts:\n"
            f"<<UNTRUSTED_DATA>>\n{fact_context}\n<</UNTRUSTED_DATA>>"
        )

    # Response cache + single-flight: serve a recent identical answer without a
    # second LLM call, and coalesce concurrent identical requests into one run.
    cache_key = hashlib.sha256(prompt.encode("utf-8")).hexdigest()
    cached = _run_cache_get(cache_key)
    if cached is not None:
        elapsed = time.time() - start_time
        logger.log_interaction(user_input, cached, [], elapsed)
        return cached

    with _span_cm("run_agent", user_id, conversation_id):
        # user.id / conversation.id / config.commit are set inside _span_cm so
        # the streaming path gets them too (it used to drop them).
        result = await _run_agent_compute(prompt, cache_key, start_time, user_input)
    return result


async def _run_agent_compute(prompt: str, cache_key: str,
                             start_time: float, user_input: str) -> str:
    """Execute the ReAct run with timeout, caching, and single-flight coalescing."""
    loop = asyncio.get_running_loop()
    with _inflight_lock:
        fut = _inflight.get(cache_key)
        owner = fut is None
        if owner:
            fut = loop.create_future()
            _inflight[cache_key] = fut

    if owner:
        try:
            # ReActAgent (llama-index workflow agent) returns a WorkflowHandler;
            # awaiting it runs the async ReAct loop and yields an AgentOutput
            # whose response.content is the answer text. Run it in the event
            # loop (NOT a worker thread) and bound it with AGENT_TIMEOUT.
            handler = agent.run(prompt)
            output = await asyncio.wait_for(handler, timeout=AGENT_TIMEOUT)
            # output is an AgentOutput; response.content holds the answer.
            result = getattr(getattr(output, "response", None), "content", None) or str(output)
            _run_cache_put(cache_key, result)
            if not fut.cancelled():
                fut.set_result(result)
        except Exception as e:
            elapsed = time.time() - start_time
            monitor.track_error()
            logger.log_error(e, context=f"User input: {user_input}")
            # Never leak internal error details to the caller.
            result = "Error: something went wrong. Please try again."
            if not fut.cancelled():
                fut.set_result(result)
        finally:
            with _inflight_lock:
                _inflight.pop(cache_key, None)
        elapsed = time.time() - start_time
        logger.log_interaction(user_input, result, [], elapsed)
        return result

    # Not the owner: wait for the in-flight request to finish and reuse its
    # (already cached) result instead of running the LLM a second time.
    try:
        result = await fut
    except Exception:
        result = "Error: something went wrong. Please try again."
    elapsed = time.time() - start_time
    logger.log_interaction(user_input, result, [], elapsed)
    return result


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
    _current_user_id.set(user_id)
    _current_conversation_id.set(conversation_id)
    bind_owner(user_id)

    # Token-budgeted context (mirrors run_agent / api.py _window_messages).
    context = _budget_context_string(mem, user_id, conversation_id)
    fact_context = fact_store.get_context_string(user_id=user_id)
    system_prompt = (
        "You are Llama Agent, a helpful AI assistant with tools for math, "
        "text analysis, unit conversion, memory, and document search. "
        "Always respond in English. Be concise and direct: answer the question, "
        "show code when asked, and skip verbose introductions, filler, "
        "redundant headings, and marketing language.\n\n"
        # Built per request, so the date is always today's.
        + common_rules() + "\n\n"
        + PROMPT_INJECTION_GUARD
    )

    messages = [
        {"role": "system", "content": system_prompt},
    ]

    # Conversation history and stored facts are user-controlled, so delimit
    # them as untrusted data to contain prompt-injection attempts.
    if context and "No conversation history yet." not in context:
        messages.append({"role": "user", "content": f"Previous context:\n<<UNTRUSTED_DATA>>\n{context}\n<</UNTRUSTED_DATA>>"})
        messages.append({"role": "assistant", "content": "I understand the context."})

    if fact_context and "No facts stored yet." not in fact_context:
        messages.append({"role": "user", "content": f"Known facts:\n<<UNTRUSTED_DATA>>\n{fact_context}\n<</UNTRUSTED_DATA>>"})
        messages.append({"role": "assistant", "content": "I'll keep that in mind."})

    messages.append({"role": "user", "content": f"<<UNTRUSTED_DATA>>\n{user_input}\n<</UNTRUSTED_DATA>>"})

    # Stream from the llama-index LLM (fast tier) instead of the raw Groq
    # client: the OpenInference llama-index instrumentor only emits
    # GroqLLM.* spans for calls routed through llama-index, so using the raw
    # SDK here previously produced zero LLM spans in Phoenix.
    # LLMOps guard: keep the request inside the fast tier's input budget
    # (long histories / pasted documents would otherwise hit HTTP 413).
    from modules import llm_guard
    messages, _trimmed = llm_guard.fit_messages(
        messages, llm_guard.input_budget(stream_llm.model))
    llm_guard.record_call(stream_llm.model, llm_guard.estimate_tokens(messages))
    llm_messages = [
        ChatMessage(role=MessageRole(message["role"]), content=message["content"])
        for message in messages
    ]
    with _span_cm("run_agent_stream", user_id, conversation_id):
        try:
            full_response = ""
            for chunk in stream_llm.stream_chat(llm_messages, max_tokens=chat_max_tokens()):
                token = chunk.text
                if token:
                    full_response += token
                    yield token
        except Exception as e:
            # Never let the stream die silently, and never leak internals.
            logger.log_error(e, context=f"Streaming user input: {user_input}")
            if llm_guard.is_rate_limited(e):
                # Groq's token limit (often the free tier's daily cap): the
                # retry below can't succeed, and "rephrase" doesn't help. Say
                # so once - which limit and Groq's "try again in" time, as the
                # document answers do - and keep it out of the model's memory.
                yield llm_guard.friendly_llm_error(e)
                return
            yield "\n\n⚠️ Error while generating response. Please try again."
        if not full_response.strip():
            # The model streamed nothing (reasoning models occasionally spend
            # the whole budget thinking). Retry once without streaming so the
            # user never gets a blank bubble saved to history.
            try:
                rescued = (stream_llm.chat(llm_messages, max_tokens=chat_max_tokens()).message.content or "").strip()
            except Exception:
                rescued = ""
            full_response = rescued or (
                "⚠️ The model returned an empty answer. Please try again, "
                "or rephrase the question.")
            yield full_response

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
            # Persist the exchange (run_agent no longer saves to memory itself).
            mem = globals().get("memory") or ConversationMemory()
            fact_store = globals().get("facts") or FactMemory()
            mem.add_message("user", user_input, user_id="guest", conversation_id="default_cli")
            mem.add_message("assistant", result, user_id="guest", conversation_id="default_cli")
            print(f"\n🤖 Agent: {result}")
            
        except KeyboardInterrupt:
            print("\n\n👋 Goodbye!")
            break
        except Exception as e:
            print(f"\n❌ Error: {str(e)}")


if __name__ == "__main__":
    main()
