"""
Llama Agent - REST API (FastAPI) - Enterprise Version
Run with: uvicorn api:app --reload

Features:
- JWT/API Key Authentication
- WebSocket Real-time Chat
- Database Integration
- Rate Limiting
- Health Checks
"""

import asyncio
import json
import logging
import os
import re
import sys
import time
import uuid
import contextvars
from datetime import datetime, timezone
from typing import Any, Optional, List, Dict
from contextlib import asynccontextmanager

from modules.silent import degrade
from fastapi import (
    FastAPI,
    HTTPException,
    UploadFile,
    File,
    WebSocket,
    Depends,
    Request,
    Response,
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, EmailStr, Field

# Add current directory to path
sys.path.insert(0, os.path.dirname(__file__))

# Import new modules
from modules.config import get_config
from modules.database import get_db_manager
from modules.auth import (
    get_current_user,
    optional_auth,
    require_admin,
    user_manager,
    jwt_manager,
    authenticate_ws_token,
    ACCESS_TOKEN_EXPIRE_MINUTES,
    REFRESH_TOKEN_EXPIRE_DAYS,
)
from modules.websocket import get_ws_handler

# MCP client (real Model Context Protocol over SSE)
from modules.mcp_client import get_mcp_status, call_mcp_tool

# WebSocket handler
ws_handler = get_ws_handler()

# Prometheus metrics
from modules.pii import StreamRedactor
from modules.prometheus_metrics import (
    init_metrics,
    metrics_endpoint,
    prometheus_middleware,
    timed_chat_stream,
)

# Enterprise security hardening (headers, brute-force, audit, sanitization, CSRF)
from modules.security_hardening import (
    brute_force,
    audit_logger,
    get_security_headers,
    get_client_ip,
    strip_html_vectors,
    detect_prompt_injection,
)

# Enterprise monitoring (alerts, business metrics, cost tracking, SLA)
from modules.advanced_monitoring import (
    alert_manager,
    business_metrics,
    sla_monitor,
    get_monitoring_dashboard,
)

# Enterprise operations (feature flags, A/B testing, deployment tracking)
from modules.cicd_improvements import feature_flags
from modules.doc_scope import owner_dir_name, scoped
from modules.request_context import bind_owner

# Module logger (used by the best-effort Postgres/Redis persistence mirror)
_log = logging.getLogger("llama_agent.api")


# ---------------------------------------------------------------------------
# Shared, replica-safe state helpers (Redis-backed, in-memory fallback)
#
# `agent_stats` and `doc_followup` are kept in app.state for speed but mirrored
# to Redis so every API replica sees the same values. This fixes P0 #2: the
# previous design stored them only in-process, so brute-force/agent stats and
# doc-follow-up anchors were invisible across replicas behind a load balancer.
# All Redis access is best-effort and falls back to the in-process copy if
# Redis is unavailable, so behaviour is unchanged when Redis is down.
# ---------------------------------------------------------------------------
_AGENT_STATS_KEY = "llama:agent_stats"
_DOC_FOLLOWUP_KEY = "llama:doc_followup"


async def _load_shared_dict(key: str, default: dict) -> dict:
    try:
        from modules import redis_store
        val = await redis_store.get_json(key)
        if isinstance(val, dict):
            return val
    except Exception as _e:
        degrade("redis conversation cache read failed (falling through to local memory)", _e)
    return default


async def _save_shared_dict(key: str, value: dict) -> None:
    try:
        from modules import redis_store
        await redis_store.set_json(key, value, ttl=86400)
    except Exception as _e:
        degrade("redis conversation cache write failed (chat continues on memory)", _e)


# Lazy singleton Groq client (P2 #7): avoid rebuilding the client on every
# document question.




# ============ P2 #8: downstream timeouts + MCP circuit breaker ============
# Bound worst-case latency on external dependencies (ChromaDB, MCP server, Groq)
# so a slow/hung dependency can never hang a request indefinitely. Sync calls
# are moved off the event loop with asyncio.to_thread before being bounded by
# asyncio.wait_for.

DOWNSTREAM_TIMEOUT = float(os.getenv("DOWNSTREAM_TIMEOUT", "30"))
MCP_TIMEOUT = float(os.getenv("MCP_TIMEOUT", "25"))
AGENT_TIMEOUT = float(os.getenv("AGENT_TIMEOUT", "60"))
# Hard cap on a single /chat/stream response. The agent streaming loops are
# synchronous generators (they block the event loop per chunk), so we cannot
# wrap them in asyncio.wait_for without breaking the SSE yield protocol. Instead
# we enforce a total stream duration: if it is exceeded we stop emitting and
# close the stream cleanly instead of streaming forever.
STREAM_MAX_DURATION = float(os.getenv("STREAM_MAX_DURATION", "300"))

# Minimal in-process circuit breaker for the external MCP server. After
# MCP_FAILURE_THRESHOLD consecutive failures it opens for MCP_CIRCUIT_COOLDOWN
# seconds, failing fast with 503 instead of hanging on a dead dependency.
_MCP_FAILURES = 0
_MCP_OPEN_UNTIL = 0.0
MCP_FAILURE_THRESHOLD = 3
MCP_CIRCUIT_COOLDOWN = 30.0


def _mcp_circuit_open() -> bool:
    return time.monotonic() < _MCP_OPEN_UNTIL


def _mcp_record_failure() -> None:
    global _MCP_FAILURES, _MCP_OPEN_UNTIL
    _MCP_FAILURES += 1
    if _MCP_FAILURES >= MCP_FAILURE_THRESHOLD:
        _MCP_OPEN_UNTIL = time.monotonic() + MCP_CIRCUIT_COOLDOWN
        _MCP_FAILURES = 0


def _mcp_record_success() -> None:
    global _MCP_FAILURES
    _MCP_FAILURES = 0


# ============ Document answering (moved to modules/doc_answers.py) ============
#
# 23 functions, 19 constants, ~660 lines of document routing / retrieval /
# answer / citation-checking. Re-exported here because tests and
# evals/rag_eval.py reach them as `from api import X` / `api.X`:
#   answer_from_documents, _ask_doc_question, is_document_question,
#   is_doc_follow_up, is_thin_answer, _DOC_*_NOTE, _WHOLE_DOC_RE, ...
#
# The persistence helpers are injected rather than imported, so
# modules/doc_answers.py does not import api (which would be a cycle).

from modules.doc_answers import (        # noqa: E402,F401
    answer_from_documents,
    DocAnswerUnavailable,
    is_doc_follow_up,
    is_document_question,
    is_thin_answer,
    _ask_doc_question,
    _check_doc_citations,
    _compare_documents,
    _detect_compare_documents,
    _distill_compare_query,
    _doc_answer_tokens,
    _doc_cache_key,
    _doc_completion_text,
    _doc_followup_query,
    _doc_match_score,
    _get_doc_followup,
    _get_groq_client,
    _is_document_inventory_question,
    _names_uploaded_document,
    _reformulate_doc_query,
    _retrieve_context,
    _set_doc_followup,
    _usable_doc_answer,
    _complete_with_fallback,
    _DOC_BAD_CITATION_NOTE,
    _DOC_CUT_NOTE,
    _DOC_FOLLOWUP_PHRASES,
    _DOC_INVENTORY_PHRASES,
    _DOC_QUESTION_INTENTS,
    _DOC_QUESTION_PHRASES,
    _DOC_QUESTION_REF_WORDS,
    _DOC_REF_RE,
    _DOC_UNSOURCED_NOTE,
    _DOC_UNVERIFIED_NOTE,
    _LEGACY_DOC_ERRORS,
    _THIN_ANSWER_PHRASES,
    _WHOLE_DOC_RE,
    _COMPARE_NOISE_WORDS,
    _COMPARE_SIGNALS,
    _CREATE_REQUEST_RE,
    _GENERIC_NAME_TOKENS,
)

# Give the module the real Redis-backed helpers (it defaults to in-memory).
from modules import doc_answers as _doc_answers_module   # noqa: E402
# Forwarders, not the functions themselves: they resolve the names at call time,
# so patching api._load_shared_dict still takes effect inside the doc module.
_doc_answers_module.bind_shared_dict_store(
    lambda key, default: _load_shared_dict(key, default),
    lambda key, value: _save_shared_dict(key, value),
    _DOC_FOLLOWUP_KEY,
)

# ============ Re-exported helpers (moved to modules/api_utils.py) ============
#
# These live in modules/api_utils.py so a future route module can import them
# without importing `api` (which would be a cycle). They are re-exported here
# because 11 files — tests, evals/rag_eval.py, modules/routing.py — reach them
# as `from api import X` / `api.X`. The private aliases preserve the old names
# exactly; nothing outside this file has to change.

from modules.api_utils import (          # noqa: E402,F401
    BoundedLRUCache,   # only external users now (evals/rag_eval.py, tests)
    aiter_in_thread,
    chunk_score,       # only external users now (evals/rag_eval.py, tests)
    escape_sse,
    heap_top_k,        # only external users now (evals/rag_eval.py, tests)
    is_db_unavailable,
)

# Old private names, kept as aliases so existing imports keep working.
from modules.api_utils import (          # noqa: E402,F401
    _STREAM_END,
    StreamFailure as _StreamFailure,
)

_aiter_in_thread = aiter_in_thread
_is_db_unavailable = is_db_unavailable

# ============ DSA utilities: bounded LRU cache ============








# Fast in-memory LRU for repeated document questions (exact-match, TTL-bounded).


# ============ Request identity + per-user document scoping ============

UPLOAD_ROOT = os.getenv("UPLOAD_DIR", "uploads")
GUEST_COOKIE = "guest_id"
_GUEST_ID_RE = re.compile(r"^[a-f0-9]{32}$")
# Set by guest_identity_middleware for every HTTP request.
_guest_id_ctx: "contextvars.ContextVar[Optional[str]]" = contextvars.ContextVar(
    "guest_id", default=None
)
def _guest_uid(guest_id: Optional[str]) -> str:
    gid = guest_id if guest_id and _GUEST_ID_RE.match(guest_id) else uuid.uuid4().hex
    return f"guest-{gid}"


def _request_uid(user: Optional[Dict[str, Any]]) -> str:
    """Stable identity for the current request, bound for downstream code.

    - Registered user -> their UUID.
    - Anonymous       -> "guest-<per-browser id>" from the HttpOnly guest
      cookie, so two anonymous visitors NEVER share history, facts or
      documents (pg_store skips persistence for non-UUID ids).
    """
    uid = str(user["id"]) if user and user.get("id") else _guest_uid(_guest_id_ctx.get())
    bind_owner(uid)
    return uid


def _user_rag(user_id: str):
    """app.state.rag scoped to one owner (see modules/doc_scope.py)."""
    return scoped(app.state.rag, user_id)


def _bump_doc_generation(user_id: str) -> None:
    """Invalidate this owner's cached document answers (see modules/doc_answers).

    Delegates rather than keeping its own dict: the counter is part of the cache
    key computed in that module, so a second copy here would be bumped by uploads
    and read by nobody, leaving stale answers served after a re-upload.
    """
    _doc_answers_module.bump_doc_generation(user_id)




def _match_fact_question(all_facts: Dict, message: str):
    """Return (key, value) for a stored fact referenced in `message`.

    Uses a token inverted index so lookup is O(n_tokens + candidates) instead
    of the original O(F*n) linear scan over every fact per message. Only facts
    whose key shares a token with the message are candidate-matched. Returns
    (None, None) when no fact is referenced.
    """
    lower_input = (message or "").lower()
    if not any(p in lower_input for p in ("what", "know", "recall", "tell", "remind")):
        return None, None
    if not all_facts:
        return None, None
    # Build token -> fact-key inverted index (O(F * key_tokens)).
    index: Dict[str, List[str]] = {}
    for key in all_facts:
        for tok in re.findall(r"[a-z0-9]+", key.lower()):
            index.setdefault(tok, []).append(key)
    msg_tokens = set(re.findall(r"[a-z0-9]+", lower_input))
    candidates = set()
    for tok in msg_tokens:
        if tok in index:
            candidates.update(index[tok])
    for key in candidates:
        if key.lower() in lower_input:
            return key, all_facts[key]
    return None, None


def _window_messages(msgs: List[Dict], max_tokens: int = 1500) -> List[Dict]:
    """Return the most recent messages whose estimated token size fits the
    budget. Bounds LLM context cost/latency (a token-budget sliding window
    instead of a fixed last-N messages)."""
    if not msgs:
        return []
    chosen = []
    total = 0
    for m in reversed(msgs):
        est = max(1, len(m.get("content", "")) // 4)
        if total + est > max_tokens and chosen:
            break
        chosen.append(m)
        total += est
    chosen.reverse()
    return chosen


# ============ MODELS ============


class ChatRequest(BaseModel):
    """Chat request model"""
    message: str = Field(..., min_length=1, max_length=10000, description="Chat message")
    user_id: Optional[str] = "default"
    context: Optional[str] = None
    conversation_id: Optional[str] = None
    # DSA feature toggles (override server env defaults when provided).
    use_critic: Optional[bool] = None
    use_reflection: Optional[bool] = None
    use_parallel: Optional[bool] = None
    agent: Optional[str] = None


class ChatResponse(BaseModel):
    """Chat response model"""
    response: str
    timestamp: str
    tools_used: List[str] = []
    response_time: float = 0


class RememberRequest(BaseModel):
    """Remember fact request"""
    key: str = Field(..., min_length=1, max_length=100, description="Fact key")
    value: str = Field(..., min_length=1, max_length=1000, description="Fact value")


class RecallRequest(BaseModel):
    """Recall fact request"""
    key: str = Field(..., min_length=1, max_length=100, description="Fact key")


class SearchRequest(BaseModel):
    """Search documents request"""
    query: str = Field(..., min_length=1, max_length=500, description="Search query")
    top_k: Optional[int] = Field(default=3, ge=1, le=20, description="Number of results")


class HealthResponse(BaseModel):
    """Health check response"""
    status: str
    uptime: str
    version: str
    modules: Dict[str, str]


class MCPToolRequest(BaseModel):
    """Request to invoke a tool on the MCP Agent Server"""
    name: str = Field(..., min_length=1, max_length=100, description="MCP tool name (e.g. coder_agent)")
    arguments: Dict[str, str] = Field(default_factory=dict, description="Tool arguments (e.g. {'task': '...'})")


# ============ AUTH MODELS ============


class RegisterRequest(BaseModel):
    """User registration request"""
    username: str = Field(..., min_length=3, max_length=50, pattern=r'^[a-zA-Z0-9_-]+$')
    email: EmailStr
    # New accounts need >= 8 chars (login still accepts older, shorter ones).
    password: str = Field(..., min_length=8, max_length=128)
    full_name: Optional[str] = Field(default=None, max_length=100)


class LoginRequest(BaseModel):
    """User login request"""
    username: str = Field(..., min_length=1, max_length=50)  # Can be username or email
    password: str = Field(..., min_length=1, max_length=128)


class AuthSuccess(BaseModel):
    """Cookie-based auth success (tokens are set as HttpOnly cookies)"""
    status: str = "ok"
    user: Optional[Dict[str, Any]] = None


class RefreshRequest(BaseModel):
    """Refresh token request (refresh token may also arrive as a cookie)"""
    refresh_token: Optional[str] = None


# ============ APP LIFETIME ============


def _run_db_health_check() -> Dict:
    """
    Run the enterprise DatabaseHealthCheck using a short-lived sync
    connection (psycopg2). The app's ORM uses asyncpg; this helper
    only exists so the health check can probe the same database.
    """
    try:
        import psycopg2
        from modules.database_improvements import db_health_check

        from modules.secrets import resolve_database_url
        db_url = resolve_database_url()
        sync_url = db_url.replace("postgresql+asyncpg://", "postgresql://")
        conn = psycopg2.connect(sync_url, connect_timeout=3)
        try:
            return db_health_check.run_health_check(conn)
        finally:
            conn.close()
    except ImportError:
        return {"status": "unavailable", "error": "psycopg2 not installed"}
    except Exception:
        # Never leak raw DB exception details (host/credentials) on a health
        # endpoint; the failure is logged server-side via the caller's handler.
        return {"status": "unavailable", "error": "database health check failed"}


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup and shutdown events"""
    # Startup
    config = get_config()
    print(f"🚀 Llama Agent API starting... (Environment: {config.environment.value})")
    print("📊 Loading modules...")
    
    # Initialize modules
    from modules.memory import ConversationMemory, FactMemory
    from modules.rag import SimpleRAG
    from modules.monitoring import AgentLogger, PerformanceMonitor
    
    app.state.memory = ConversationMemory()
    app.state.facts = FactMemory()
    
    # Use OptimizedRAG (hybrid search, reranker, CRAG, cache; contextual chunks opt-in) when enabled via env var.
    # Falls back to SimpleRAG for backward compatibility / when ChromaDB is unavailable.
    use_optimized = os.environ.get("USE_OPTIMIZED_RAG", "").lower() in ("true", "1", "yes")
    if use_optimized:
        try:
            from modules.rag_optimizations import OptimizedRAG
            from modules.vector_store import get_vector_store
            vector_store = get_vector_store()
            app.state.rag = OptimizedRAG(vector_store)
            print("✅ OptimizedRAG initialized (hybrid search, cross-encoder reranker, "
                  "semantic cache; contextual summaries: "
                  f"{'on' if os.getenv('RAG_CONTEXTUAL', '0') == '1' else 'off'}; query decomposition: "
                  f"{'on' if os.getenv('RAG_DECOMPOSE', '0') == '1' else 'off'})")
            # Load the reranker now (not on the first question) and say which
            # backend is really in use - "keyword" means no model was found.
            print(f"🔎 Reranker: {app.state.rag.reranker_backend()} | "
                  f"CRAG: {'on' if os.getenv('RAG_CRAG', '1') == '1' else 'off'}")
        except Exception as e:
            print(f"⚠️  Failed to initialize OptimizedRAG, falling back to SimpleRAG: {e}")
            app.state.rag = SimpleRAG()
    else:
        app.state.rag = SimpleRAG()
    app.state.logger = AgentLogger()
    app.state.monitor = PerformanceMonitor()
    app.state.config = config
    # Real agent-team tools (web search + document reads) read the SAME RAG
    # store, so any user can ask the agents to read their uploaded files.
    from modules.agent_tools import set_document_store
    set_document_store(app.state.rag)
    # Share the SAME instances with the ReAct agent module so its
    # document/fact tools (read_document, search_documents, list_documents,
    # remember_fact, ...) operate on the cross-replica shared store, not the
    # module-level SimpleRAG()/FactMemory() it created at import time.
    import llama_agent1 as _llama_agent1
    _llama_agent1.rag = app.state.rag
    _llama_agent1.facts = app.state.facts
    # Real agent-team usage tracking (populated by chat dispatches and MCP
    # tool calls below). Reset on restart, like the other in-memory stats.
    app.state.agent_stats = {"handled": {}, "last_used": {}}
    # Per-conversation anchor for document follow-ups: conversation_id ->
    # the last message that asked about an uploaded document. Used to route
    # "what about X?" / "and the Y column?" follow-ups back to the RAG
    # answerer instead of general chat. In-memory like agent_stats.
    
    # Re-index previously uploaded files so RAG knowledge survives restarts.
    # Uploads live in uploads/<owner>/<file>; each file is indexed for its
    # owner only. Files already present in a shared/persistent vector store
    # (e.g. ChromaDB server) are skipped, so restarts don't re-embed (or
    # re-summarize via the LLM) everything.
    try:
        uploads_dir = UPLOAD_ROOT
        if os.path.isdir(uploads_dir):
            legacy = [f for f in os.listdir(uploads_dir)
                      if os.path.isfile(os.path.join(uploads_dir, f))]
            if legacy:
                print(f"⚠️  {len(legacy)} legacy upload(s) in {uploads_dir}/ have no owner "
                      f"and are NOT indexed (re-upload them or move into {uploads_dir}/<user_id>/)")
            for owner in sorted(os.listdir(uploads_dir)):
                owner_path = os.path.join(uploads_dir, owner)
                if not os.path.isdir(owner_path):
                    continue
                view = scoped(app.state.rag, owner)
                for fname in sorted(os.listdir(owner_path)):
                    fpath = os.path.join(owner_path, fname)
                    if not os.path.isfile(fpath):
                        continue
                    try:
                        if view.has_document(fname):
                            continue
                        result = view.add_document(fpath)
                        print(f"📄 Re-indexed {owner}/{fname} ({result})")
                    except Exception as e:
                        print(f"⚠️  Failed to re-index {owner}/{fname}: {e}")
    except Exception as e:
        print(f"⚠️  Could not re-index uploads directory: {e}")
    
    # Initialize database (if enabled)
    if config.features.enable_auth:
        try:
            db = get_db_manager()
            await db.init_db()
            print("✅ Database initialized")
        except Exception as e:
            print(f"⚠️  Database not available: {e}")
            print("   Running without persistence (memory-only mode)")
    
    # Enterprise: database health check + feature flags
    app.state.feature_flags = feature_flags
    app.state.db_health_result = _run_db_health_check()
    if app.state.db_health_result.get("status") == "healthy":
        print("✅ Database health check passed")
    else:
        print(f"⚠️  Database health check unavailable: {app.state.db_health_result.get('error', 'unknown')}")
    
    # Enterprise: initial alert sweep so the monitoring stack has a baseline
    triggered = alert_manager.check_alerts(_alert_metrics())
    if triggered:
        print(f"📈 Baseline alerts triggered: {[a['type'] for a in triggered]}")
    
    print("🔒 Security hardening active: headers, brute-force protection, audit logging, input sanitization, CSRF")
    print("📈 Enterprise monitoring active: alerts, business metrics, cost tracking, SLA")
    print(f"🚩 Feature flags: {feature_flags.get_enabled_flags()}")
    
    print("✅ All modules loaded!")
    print("🌐 API ready at http://localhost:8000")
    print("📡 WebSocket ready at ws://localhost:8000/ws")
    print("📊 Prometheus metrics at http://localhost:8000/metrics")
    
    # Initialize Prometheus metrics
    init_metrics()

    # Phoenix tracing (OTel auto-instruments FastAPI, httpx, etc.)
    # batch=True uses a BatchSpanProcessor (recommended for production) instead
    # of the default SimpleSpanProcessor.
    try:
        from phoenix.otel import register as register_phoenix
        register_phoenix(endpoint="http://phoenix:6006/v1/traces", auto_instrument=True, batch=True)
        print("📡 Phoenix tracing active (batch processor)")
    except Exception as e:
        print(f"⚠️  Phoenix tracing unavailable: {e}")

    # Instrument the FastAPI app itself so EVERY request (chat, health, auth...)
    # becomes a trace in Phoenix. The phoenix `auto_instrument` flag only wires
    # OpenInference entry points (openai, llama-index), NOT the plain OTel
    # FastAPI instrumentor, so it must be applied explicitly.
    try:
        from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
        FastAPIInstrumentor.instrument_app(app)
        # CRITICAL: uvicorn invokes the app with a `lifespan` ASGI scope BEFORE
        # the lifespan body runs, which makes Starlette build AND cache the
        # middleware stack (without the OTel middleware) ahead of time.
        # instrument_app only patches build_middleware_stack, so the cached
        # stack must be rebuilt explicitly or requests will never be traced.
        app.middleware_stack = app.build_middleware_stack()
        print("🌐 FastAPI request tracing active")
    except Exception as e:
        print(f"⚠️  FastAPI instrumentation unavailable: {e}")

    yield
    
    # Shutdown
    print("👋 Shutting down...")
    try:
        from modules import pg_store, redis_store
        await pg_store.close_pool()
        await redis_store.close()
        print("✅ Persistence layer closed")
    except Exception as e:
        print(f"⚠️  Persistence close error: {e}")


# ============ CREATE APP ============


app = FastAPI(
    title="🤖 Llama Agent API",
    description="Enterprise AI Agent with Memory, RAG, Auth, WebSockets, and 14+ Tools",
    version="3.5.6",
    lifespan=lifespan,
)

# CORS middleware - RESTRICTED for security
config = get_config()

# Production CORS: only allow specific origins
# Development: allow all (for local testing)
_environment = os.getenv("ENVIRONMENT", "development")

if _environment == "production":
    # PRODUCTION: Only allow your actual domain(s)
    cors_origins = [
        "http://localhost:80",      # Nginx HTTP
        "http://localhost:443",     # Nginx HTTPS
        "http://localhost:3000",    # Next.js frontend
        "http://localhost:3001",    # Next.js frontend (alt)
        # Add your production domain here:
        # "https://yourdomain.com",
    ]
else:
    # DEVELOPMENT: list the local dev origins explicitly rather than "*".
    #
    # "*" together with allow_credentials=True is not safe: Starlette responds to
    # any caller with `Access-Control-Allow-Origin: <their origin>` and
    # `Access-Control-Allow-Credentials: true`, so any website the developer
    # visits can make authenticated cross-origin calls to their local API. The
    # browser refuses a literal "*" plus credentials, but Starlette substitutes
    # the caller's origin, which the browser accepts.
    #
    # No cross-origin caller needs access: the Next.js frontend reaches the API
    # through its same-origin "/api" rewrite (frontend/next.config.*), and Nginx
    # proxies /api/* too.
    cors_origins = [
        "http://localhost:3000",    # Next.js frontend
        "http://localhost:3001",    # Next.js frontend (alt)
        "http://127.0.0.1:3000",
    ]

app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_origins,
    # Only the listed origins above, never a reflected wildcard.
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "PATCH"],
    allow_headers=["Authorization", "Content-Type", "X-API-Key"],
)

# Rate limiting middleware (with audit logging for violations)
from modules.security import rate_limiter

@app.middleware("http")
async def rate_limit_middleware(request: Request, call_next):
    """Rate limiting to prevent abuse"""
    # Skip rate limiting for health checks and metrics
    if request.url.path in ("/health", "/metrics", "/"):
        return await call_next(request)
    
    # Get client IP (honors X-Forwarded-For / X-Real-IP behind Nginx)
    client_ip = get_client_ip(request)
    
    is_limited, message = rate_limiter.is_rate_limited(client_ip)
    if is_limited:
        # Enterprise: audit rate-limit violations
        audit_logger.log_rate_limit_violation(client_ip, request.url.path)
        return JSONResponse(
            status_code=429,
            content={"detail": message, "retry_after": 60},
        )
    
    return await call_next(request)

# Enterprise security headers middleware (outermost: covers every response)
@app.middleware("http")
async def security_headers_middleware(request: Request, call_next):
    """Add enterprise security headers to all responses"""
    response = await call_next(request)
    for header, value in get_security_headers().items():
        response.headers.setdefault(header, value)
    return response

# Identity middleware: per-browser guest id + silent access-token refresh
@app.middleware("http")
async def identity_middleware(request: Request, call_next):
    """
    1. Guest identity: every browser gets an HttpOnly ``guest_id`` cookie,
       so anonymous visitors are isolated from each other (history, facts,
       documents, caches) instead of all sharing one "guest" bucket.
    2. Silent refresh: when the short-lived access cookie is missing or
       expired but a valid refresh cookie exists, mint a new access token,
       inject it into THIS request (so auth dependencies see it) and set it
       on the response. Logged-in users therefore don't silently degrade to
       guests after ACCESS_TOKEN_EXPIRE_MINUTES.
    """
    cookies = request.cookies
    new_guest: Optional[str] = None
    guest_id = cookies.get(GUEST_COOKIE)
    if not guest_id or not _GUEST_ID_RE.match(guest_id):
        guest_id = new_guest = uuid.uuid4().hex
    _guest_id_ctx.set(guest_id)

    new_access: Optional[str] = None
    path = request.url.path
    if (not request.headers.get("authorization")
            and not path.rstrip("/").endswith(("/auth/logout", "/auth/refresh"))):
        access = cookies.get("access_token")
        payload = jwt_manager.verify_token(access) if access else None
        if not payload or payload.get("type") != "access":
            refresh = cookies.get("refresh_token")
            rpayload = jwt_manager.verify_token(refresh) if refresh else None
            if rpayload and rpayload.get("type") == "refresh" and rpayload.get("sub"):
                new_access = jwt_manager.create_access_token(data={"sub": rpayload["sub"]})

    if new_guest or new_access:
        merged = dict(cookies)
        if new_guest:
            merged[GUEST_COOKIE] = new_guest
        if new_access:
            merged["access_token"] = new_access
        headers = [(k, v) for k, v in request.scope["headers"] if k != b"cookie"]
        headers.append((b"cookie", "; ".join(f"{k}={v}" for k, v in merged.items()).encode("latin-1")))
        request.scope["headers"] = headers

    response = await call_next(request)

    secure = _request_is_https(request)
    if new_guest:
        response.set_cookie(
            key=GUEST_COOKIE, value=new_guest, httponly=True, secure=secure,
            samesite="lax", max_age=30 * 86400, path="/",
        )
    if new_access:
        response.set_cookie(
            key="access_token", value=new_access, httponly=True, secure=secure,
            samesite="lax", max_age=ACCESS_TOKEN_EXPIRE_MINUTES * 60, path="/",
        )
    return response


# Prometheus metrics middleware
@app.middleware("http")
async def prometheus_middleware_handler(request: Request, call_next):
    return await prometheus_middleware(request, call_next)


# ============ ROOT & HEALTH ============


@app.get("/")
async def root():
    """Root endpoint"""
    return {
        "name": "🤖 Llama Agent API",
        "version": "3.5.6",
        "docs": "/docs",
        "features": [
            "REST API",
            "WebSocket Real-time Chat",
            "JWT/API Key Authentication",
            "PostgreSQL Database",
            "14+ AI Tools",
            "Enterprise Security (headers, brute-force, audit, sanitization)",
            "Enterprise Monitoring (alerts, costs, SLA, dashboard)",
        ],
        "endpoints": {
            "chat": "/chat",
            "auth": "/auth/register, /auth/login",
            "websocket": "/ws/{user_id}",
            "memory": "/memory",
            "facts": "/facts",
            "documents": "/documents",
            "mcp": "/mcp/status, /mcp/tools",
            "monitoring": "/monitoring/dashboard",
            "health": "/health"
        }
    }


# ============ LLM AVAILABILITY ============

_LLM_KEY_HELP = (
    "No LLM API key is configured, so this endpoint cannot answer. Set "
    "GROQ_API_KEY in the environment, or put it in secrets/groq_api_key.txt "
    "(Docker secret) / .env, then restart. The rest of the app is up: see "
    "/health and /docs."
)


def _llm_key_configured() -> bool:
    """Whether an LLM key was supplied at startup (reported by /health)."""
    try:
        import llama_agent1 as _agent_mod
        return getattr(_agent_mod, "GROQ_API_KEY_CONFIGURED", True)
    except Exception:  # pragma: no cover
        return True


def require_api_key() -> None:
    """Raise 503 when no LLM key was supplied at startup.

    The app boots without a key on purpose (see the note in llama_agent1.py):
    a clone that has not filled in secrets/ should still be able to reach
    /health and /docs and read what is wrong. Only the routes that actually
    call a model refuse, and they refuse with an explanation rather than an
    authentication error from the provider several layers down.
    """
    if not _llm_key_configured():
        raise HTTPException(status_code=503, detail=_LLM_KEY_HELP)


@app.get("/health", response_model=HealthResponse)
async def health_check():
    """Health check endpoint"""
    monitor = app.state.monitor
    health = monitor.get_health()
    
    return HealthResponse(
        status=health["status"],
        uptime=health["uptime_human"],
        version="3.5.6",
        modules={
            "memory": "active",
            "rag": "active",
            "monitoring": "active",
            "llm_key": "configured" if _llm_key_configured() else "missing",
            "database": "active" if get_config().features.enable_auth else "disabled",
            "websocket": "active" if get_config().features.enable_websockets else "disabled",
        }
    )


# ============ AUTH ENDPOINTS ============




def _auth_error(action: str, e: Exception) -> HTTPException:
    """Log an unexpected auth failure to stderr (visible in `kubectl logs`,
    unlike the audit log file) and map it to a user-facing error."""
    if _is_db_unavailable(e):
        _log.error("%s failed: database unavailable: %r", action, e)
        return HTTPException(
            status_code=503,
            detail="Database unavailable - please try again in a minute.",
        )
    _log.exception("%s failed", action)
    return HTTPException(status_code=500, detail="Internal server error")


def _request_is_https(request: Optional[Request]) -> bool:
    """True when the client reached us over HTTPS (directly or via ingress)."""
    if request is None:
        return False
    proto = request.headers.get("x-forwarded-proto") or request.headers.get(
        "x-forwarded-scheme"
    )
    if proto and proto.split(",")[0].strip().lower() == "https":
        return True
    return request.url.scheme == "https"


def _set_auth_cookies(
    response: Response, access_token: str, refresh_token: str, request: Request = None
) -> None:
    """Set the access + refresh tokens as HttpOnly, SameSite cookies.

    `secure` is enabled ONLY when the client actually reached the API over
    HTTPS. We read the scheme from the ingress's X-Forwarded-Proto /
    X-Forwarded-Scheme header (or the request URL) rather than guessing from
    ENVIRONMENT: a deployment can run with ENVIRONMENT=production behind an
    HTTP-only ingress (e.g. local *.local hosts, no cert-manager), and a Secure
    cookie there would be rejected by the browser, silently breaking login.
    The frontend talks to the API same-origin (via the Nginx/Next proxy), so
    `samesite="lax"` is sufficient and avoids the cross-site Secure requirement.
    """
    secure = _request_is_https(request)
    response.set_cookie(
        key="access_token",
        value=access_token,
        httponly=True,
        secure=secure,
        samesite="lax",
        max_age=ACCESS_TOKEN_EXPIRE_MINUTES * 60,
        path="/",
    )
    response.set_cookie(
        key="refresh_token",
        value=refresh_token,
        httponly=True,
        secure=secure,
        samesite="lax",
        max_age=REFRESH_TOKEN_EXPIRE_DAYS * 86400,
        path="/",
    )


def _clear_auth_cookies(response: Response) -> None:
    for key in ("access_token", "refresh_token"):
        response.delete_cookie(key, path="/")


@app.post("/auth/register", response_model=AuthSuccess)
async def register(request: Request, response: Response, body: RegisterRequest):
    """Register a new user (tokens issued as HttpOnly cookies)"""
    client_ip = get_client_ip(request)
    try:
        result = await user_manager.register(
            username=body.username,
            email=body.email,
            password=body.password,
            full_name=body.full_name,
        )
        # Enterprise: audit registration success
        audit_logger.log_registration(body.username, client_ip, success=True)
        business_metrics.record_feature_usage("user_registration")
        _set_auth_cookies(response, result["access_token"], result["refresh_token"], request)
        return AuthSuccess(status="ok", user=result["user"])
    except HTTPException as e:
        # Enterprise: audit registration failure
        audit_logger.log_registration(body.username, client_ip, success=False, reason=str(e.detail))
        raise
    except Exception as e:
        audit_logger.log_registration(body.username, client_ip, success=False, reason=str(e))
        raise _auth_error("registration", e)


@app.post("/auth/login", response_model=AuthSuccess)
async def login(request: Request, response: Response, body: LoginRequest):
    """Login with username/email and password (tokens issued as HttpOnly cookies)"""
    client_ip = get_client_ip(request)
    
    # Enterprise: brute-force protection (per IP and per username)
    ip_locked, ip_seconds = brute_force.is_locked(client_ip)
    user_locked, user_seconds = brute_force.is_locked(body.username)
    if ip_locked or user_locked:
        seconds_left = ip_seconds or user_seconds or 0
        audit_logger.log_login_attempt(
            body.username,
            client_ip,
            success=False,
            reason=f"account/IP locked, retry in {seconds_left}s",
        )
        raise HTTPException(
            status_code=429,
            detail=f"Too many failed attempts. Try again in {seconds_left} seconds.",
        )
    
    try:
        result = await user_manager.login(
            username=body.username,
            password=body.password,
        )
        # Enterprise: reset counters + audit success
        brute_force.record_success(client_ip)
        brute_force.record_success(body.username)
        audit_logger.log_login_attempt(body.username, client_ip, success=True)
        _set_auth_cookies(response, result["access_token"], result["refresh_token"], request)
        return AuthSuccess(status="ok", user=result["user"])
    except HTTPException as e:
        # Enterprise: record failure + audit
        brute_force.record_failure(client_ip)
        brute_force.record_failure(body.username)
        audit_logger.log_login_attempt(
            body.username,
            client_ip,
            success=False,
            reason=str(e.detail),
        )
        raise
    except Exception as e:
        brute_force.record_failure(client_ip)
        brute_force.record_failure(body.username)
        audit_logger.log_login_attempt(body.username, client_ip, success=False, reason=str(e))
        raise _auth_error("login", e)


@app.post("/auth/refresh", response_model=AuthSuccess)
async def refresh_token(request: Request, response: Response, body: RefreshRequest):
    """Get new access token using a refresh token (cookie or body)"""
    refresh = body.refresh_token or request.cookies.get("refresh_token")
    if not refresh:
        raise HTTPException(status_code=401, detail="Missing refresh token")
    try:
        result = await user_manager.refresh_token(refresh)
        # Keep the same refresh token (it is still valid); only rotate access.
        _set_auth_cookies(response, result["access_token"], refresh, request)
        return AuthSuccess(status="ok")
    except HTTPException:
        raise
    except Exception as e:
        raise _auth_error("token refresh", e)


@app.post("/auth/logout")
async def logout(response: Response):
    """Clear the auth cookies (client-side session termination)"""
    _clear_auth_cookies(response)
    return {"status": "ok"}


@app.get("/auth/me")
async def get_me(user = Depends(get_current_user)):
    """Get current user info"""
    return {"user": user}


# ============ WEBSOCKET ENDPOINT ============


# ---------------------------------------------------------------------------
# WebSocket auth tickets
#
# The browser should NOT put the JWT in the WS URL (?token=...) because that
# leaks it via proxy logs, browser history and the Referer header. Instead the
# frontend fetches a short-lived, single-use ticket from /ws/ticket (a normal
# authenticated HTTP call) and connects with ?ticket=<ticket>. Tickets are
# kept in-memory (per-process; fine for a single API replica; use Redis in a
# multi-replica deployment) and are consumed on first use.
# ---------------------------------------------------------------------------
_ws_tickets: Dict[str, Dict[str, Any]] = {}
_WS_TICKET_TTL = 60  # seconds
_WS_TICKET_PREFIX = "llama:wsticket:"


async def _issue_ws_ticket(user: Dict[str, Any]) -> Dict[str, Any]:
    """Issue a single-use ticket. Stored in Redis when configured (so a
    ticket issued by one API replica can be redeemed on another), with an
    in-memory fallback for single-process deployments."""
    from modules import redis_store

    ticket = str(uuid.uuid4())
    record = {
        "user_id": str(user["id"]),
        "username": user.get("username", "Anonymous"),
    }
    if redis_store.is_configured() and await redis_store.set_json(
        _WS_TICKET_PREFIX + ticket, record, ttl=_WS_TICKET_TTL
    ):
        return {"ticket": ticket, "expires_in": _WS_TICKET_TTL}
    # Opportunistic purge of expired in-memory tickets
    now = time.monotonic()
    for t in [t for t, r in _ws_tickets.items() if r["exp"] < now]:
        _ws_tickets.pop(t, None)
    _ws_tickets[ticket] = {**record, "exp": now + _WS_TICKET_TTL, "used": False}
    return {"ticket": ticket, "expires_in": _WS_TICKET_TTL}


async def _consume_ws_ticket(ticket: str) -> Optional[Dict[str, Any]]:
    from modules import redis_store

    record = _ws_tickets.pop(ticket, None)
    if record is not None:
        if record["used"] or time.monotonic() > record["exp"]:
            return None
        record["used"] = True
        return record
    if redis_store.is_configured():
        return await redis_store.pop_json(_WS_TICKET_PREFIX + ticket)
    return None


@app.post("/ws/ticket")
async def ws_ticket(user=Depends(get_current_user)):
    """Issue a short-lived, single-use WebSocket auth ticket.

    The frontend calls this over HTTPS, then connects to the WebSocket with
    `?ticket=<ticket>` instead of embedding the JWT in the URL.
    """
    return await _issue_ws_ticket(user)


@app.websocket("/ws/{user_id}")
async def websocket_endpoint(
    websocket: WebSocket,
    user_id: str,
    token: str = "",
    ticket: str = "",
):
    """
    WebSocket endpoint for real-time chat

    Connect: wss://<host>/ws/<anything>?ticket=<ticket>

    Identity is NEVER taken from the path (kept only for URL compatibility):
    - Valid ticket            -> the ticket's user
    - Valid access token      -> the token's user (query param or cookie)
    - Nothing                 -> anonymous: the browser's guest cookie id, or a
                                 fresh random id (can't read anyone's history)
    - Invalid/expired ticket/token -> connection rejected (WS 1008)

    Messages (JSON):
    - Send: {"type": "chat", "data": {"content": "Hello!"}}
    - Receive: {"type": "response", "data": {"content": "Hi there!"}}
    """
    username = "Anonymous"
    if ticket:
        record = await _consume_ws_ticket(ticket)
        if not record:
            await websocket.close(code=1008, reason="Invalid or expired ticket")
            return
        user_id = record["user_id"]
        username = record["username"]
    elif token:
        user = await authenticate_ws_token(token)
        if not user:
            # Fail closed: never accept a spoofed identity or bad token
            await websocket.close(code=1008, reason="Invalid or expired token")
            return
        user_id = str(user["id"])
        username = user["username"]
    else:
        user = await authenticate_ws_token(websocket.cookies.get("access_token"))
        if user:
            user_id = str(user["id"])
            username = user["username"]
        else:
            user_id = _guest_uid(websocket.cookies.get(GUEST_COOKIE))

    bind_owner(user_id)
    handler = get_ws_handler()
    await handler.handle_connection(websocket, user_id, username)


@app.get("/ws/status")
async def websocket_status():
    """WebSocket connection counts (no user identities are exposed)."""
    handler = get_ws_handler()
    return {
        "total_connections": handler.manager.connection_count,
        "total_users": handler.manager.user_count,
    }


# ============ CHAT ENDPOINTS ============

# Intent routing, keyword groups and the Aho-Corasick matcher now live in
# modules/routing.py (pure pattern matching, no app state). Re-exported here
# because tests and evals import them from `api`:
#   from api import route_multi_agent, detect_agent_prefix, AGENT_KEYS
from modules.routing import (  # noqa: F401
    AGENT_KEYS,
    AhoCorasick,
    CODE_INTENT_RE,
    CODE_KEYWORDS,
    ANALYZER_KEYWORDS,
    PLANNER_KEYWORDS,
    RECENCY_KEYWORDS,
    RESEARCH_KEYWORDS,
    WRITER_KEYWORDS,
    _AGENT_PREFIX_MAP,
    _AGENT_PREFIX_RE,
    _MCP_TOOL_TO_AGENT,
    _NAME_EXTRACT_PATTERNS,
    _NAME_QUESTION_PATTERNS,
    _NAME_STOPWORDS,
    _NAME_TOKEN,
    _ROUTING_AC,
    _ROUTING_PRIORITY,
    _TEAM_PREFIX_RE,
    detect_agent_prefix,
    detect_team_prefix,
    extract_name_from_message,
    is_name_question,
    route_multi_agent,
)

# Name memory: users can tell the agent their name in plain chat
# ("my name is X" / "call me X" / "i am X") and later ask it back
# ("what is my name?"). The name is stored as a fact under the key
# "user's name" - deliberately NOT a short key like "name", so the
# generic substring fact check below cannot false-positive on questions
# like "what is the name of the capital of France?".




# Patterns for the remember command: "my name is X" and "X is Y"
_NAME_PATTERN = re.compile(r"my name is\s+(" + _NAME_TOKEN + r"(?:\s+" + _NAME_TOKEN + r"){0,3})", re.IGNORECASE)
_IS_PATTERN = re.compile(r"(.{5,}?)\s+is\s+(.+)", re.IGNORECASE)


























async def _record_agent_usage(agent_keys) -> None:
    """Increment real task counters + last-used timestamps for agent keys.

    Mirrored to Redis so all replicas share the same counters (P0 #2).
    """
    stats = getattr(app.state, "agent_stats", None)
    if stats is None:
        stats = {"handled": {}, "last_used": {}}
        app.state.agent_stats = stats
    shared = await _load_shared_dict(_AGENT_STATS_KEY, {"handled": {}, "last_used": {}})
    handled = shared.setdefault("handled", {})
    last_used = shared.setdefault("last_used", {})
    # UTC-aware so the frontend can compute relative times correctly
    now = datetime.now(timezone.utc).isoformat()
    for key in agent_keys:
        if key not in AGENT_KEYS:
            continue
        handled[key] = handled.get(key, 0) + 1
        last_used[key] = now
    # Keep the in-process mirror in sync for single-replica speed.
    stats["handled"] = handled
    stats["last_used"] = last_used
    await _save_shared_dict(_AGENT_STATS_KEY, shared)


async def run_agents_parallel(agent_keys, task: str, orchestrator) -> Dict[str, str]:
    """Run multiple specialist agents concurrently and return {agent: result}.

    Each agent's synchronous ``execute`` runs in its own thread so the calls
    truly overlap (parallel LLM fan-out instead of sequential). Per-agent
    timeouts (AGENT_TIMEOUT) and per-agent error isolation mean one slow/failed
    agent never blocks or aborts the others.
    """
    async def _run(key: str):
        agent = orchestrator.agents[key]
        return await asyncio.wait_for(
            asyncio.to_thread(agent.execute, task), timeout=AGENT_TIMEOUT
        )

    results = await asyncio.gather(
        *(_run(k) for k in agent_keys), return_exceptions=True
    )
    out: Dict[str, str] = {}
    for k, r in zip(agent_keys, results):
        if isinstance(r, Exception):
            out[k] = f"⚠️ {k.title()} agent error: {r}"
        else:
            out[k] = r
    return out


def _default_conversation_id(user_id: str) -> str:
    """Stable per-user UUID conversation id.

    A real UUID (valid for the Postgres conversations.id column) that is
    deterministic per user, so messages sent without an explicit
    conversation_id all land in the same per-user conversation.
    """
    return str(uuid.uuid5(uuid.NAMESPACE_DNS, f"llama-agent-default:{user_id}"))


async def _mirror_message(user_id: str, conversation_id: str, role: str, content: str) -> str:
    """Mirror one chat message to PostgreSQL + invalidate the Redis caches.

    Used when the message was ALREADY written to the JSON memory store by
    another component (the general ReAct agent writes to memory itself), so we
    only add the durable Postgres copy the sidebar reads. Never raises:
    Postgres/Redis are best-effort mirrors.
    """
    from modules import pg_store, redis_store
    try:
        await pg_store.add_message(user_id, conversation_id, role, content)
        norm = pg_store.normalize_conversation_id(conversation_id)
        await redis_store.invalidate_user(user_id)
        await redis_store.invalidate_conversation(norm)
    except Exception as e:
        _log.warning("persist mirror failed (chat continues on memory store): %s", e)
    return conversation_id


async def _persist_message(user_id: str, conversation_id: str, role: str, content: str) -> str:
    """Write one chat message to the live JSON memory store (agent context) and
    mirror it to PostgreSQL (durable history the sidebar reads) + invalidate the
    Redis caches. Never raises: Postgres/Redis are best-effort mirrors.

    Returns the conversation_id the message was actually written to.
    """
    memory = app.state.memory
    resolved = memory.add_message(role, content, user_id=user_id,
                                  conversation_id=conversation_id) or conversation_id
    return await _mirror_message(user_id, resolved, role, content)


async def _with_context(message: str, memory, user_id: str, conversation_id: str) -> str:
    """Prepend this conversation's recent history so specialized agents can
    answer follow-up questions that reference earlier messages.

    History comes from PostgreSQL (through the Redis cache) when available -
    the durable store the sidebar reads - falling back to the JSON memory
    store when the databases are unreachable.
    """
    from modules import pg_store, redis_store
    msgs = None
    try:
        norm = pg_store.normalize_conversation_id(conversation_id)
        msgs = await redis_store.get_json(redis_store.messages_key(norm))
        if msgs is None:
            msgs = await pg_store.get_messages(user_id, conversation_id)
            if msgs is not None:
                # Only trust PostgreSQL when the conversation actually exists
                # there; an empty result may also mean the conversation is a
                # legacy one that predates the Postgres mirror and lives only
                # in the JSON memory store.
                if not msgs and await pg_store.get_conversation(user_id, conversation_id) is None:
                    msgs = None
                else:
                    await redis_store.set_json(redis_store.messages_key(norm), msgs, ttl=redis_store.MSGS_TTL)
    except Exception as e:
        _log.debug("_with_context pg read failed, using memory: %s", e)
        msgs = None
    if msgs is None:
        context = memory.get_context_string(n=5, user_id=user_id, conversation_id=conversation_id)
    else:
        recent = _window_messages(msgs)
        if not recent:
            context = "No conversation history yet."
        else:
            # One shared budget across messages, not 200 chars each: the
            # per-message cap contradicted _window_messages' 1500-token
            # admission and cut every answer off mid-word. See
            # modules/context_budget.py for the measurement.
            from modules.context_budget import render_history
            context = "Recent conversation history:\n" + render_history(recent)
    if not context or "No conversation history yet." in context:
        return message
    return f"Recent conversation context:\n{context}\n\nUser's new message: {message}"


def _merge_conversations(pg_convs: List[Dict], mem_convs: List[Dict]) -> List[Dict]:
    """Combine Postgres conversations with JSON-memory conversations.

    PostgreSQL only contains chats created after the mirror was added; older
    conversations live solely in the JSON memory store. Merging both (deduped
    by normalized conversation id, newest first) guarantees no history is ever
    hidden from the sidebar.
    """
    from modules import pg_store
    merged = list(pg_convs or [])
    seen = {pg_store.normalize_conversation_id(c["id"]) for c in merged}
    for c in mem_convs or []:
        key = pg_store.normalize_conversation_id(c["id"])
        if key not in seen:
            merged.append(c)
            seen.add(key)
    merged.sort(key=lambda c: c.get("updated_at") or "", reverse=True)
    return merged


async def _list_conversations_for(user_id: str, memory) -> List[Dict]:
    """Full sidebar conversation list for a user.

    Read path: Redis cache -> PostgreSQL (durable store) -> JSON memory store,
    merging the legacy memory conversations into the Postgres result so chats
    that predate the Postgres mirror are always shown. Populates the cache so
    the sidebar never hits the database twice.
    """
    from modules import pg_store, redis_store
    cache_key = redis_store.list_key(user_id)
    cached = await redis_store.get_json(cache_key)
    if cached is not None:
        return cached
    convs = await pg_store.list_conversations(user_id)
    if convs is None:
        convs = memory.get_conversations(user_id)
    else:
        convs = _merge_conversations(convs, memory.get_conversations(user_id))
    await redis_store.set_json(cache_key, convs, ttl=redis_store.LIST_TTL)
    return convs




# Questions asking WHAT documents/files are uploaded (an inventory question)
# rather than about the CONTENT of one specific file. These route to RAG so
# the answer lists the user's actual uploads instead of a generic agent guess.






# Whole words that refer to a file/document (no "doc" inside "docker").

# "write/create/generate ... a file" is a task for an agent, not a document question.

# File-name words too generic to identify a document.




# Words that point at an uploaded file/document.

# Phrases that signal the user wants information ABOUT that file.


# Phrases that indicate a message continues a previous document conversation
# (e.g. "what about the pricing column?" right after "summarize the CRM
# file"). The check additionally requires that a document question was asked
# before in THIS conversation, otherwise nothing routes here.










# Answer shapes that mean "the retrieval did not actually answer the
# question". When a first-pass document answer looks like one of these, the
# question is reformulated and the documents are queried once more.


# User-facing text for a failed model call (shared with general chat).
# Re-exported: tests/test_doc_answers.py reaches it as `from api import ...`.
from modules.llm_guard import friendly_llm_error  # noqa: E402,F401


# Questions about a document AS A WHOLE ("summarize it", "what is this
# file?") - only these get a one-line description of the document first.

# Marks an answer cut off by the token limit (such answers are not cached).


# Marks an answer Corrective RAG could not verify against the excerpts
# (shown so the user knows, never cached so asking again retries).


# Error texts older versions returned as answers - and cached (the semantic
# cache lives in ChromaDB and survives deploys). Never serve them.














# Explicit comparison signals for multi-document questions.




# Words inside a comparison request that carry no retrieval signal. The
# file names being compared are stripped separately in _distill_compare_query;
# this set covers the comparison framing words themselves (stopwords are
# handled by the RAG store's own tokenizer).










@app.post("/chat", response_model=ChatResponse)
async def chat(
    request: ChatRequest,
    http_request: Request,
    user = Depends(optional_auth),  # Optional auth (works with or without login)
):
    """Chat with the agent. Auth optional in development mode."""
    require_api_key()
    start_time = datetime.now()
    memory = app.state.memory
    facts = app.state.facts
    logger = app.state.logger
    user_id = _request_uid(user)
    rag = _user_rag(user_id)  # this caller's documents only
    # Per-user default conversation: two users must never share "default".
    # The storage layer also refuses to rebind a conversation to another user.
    conversation_id = request.conversation_id or _default_conversation_id(user_id)
    
    query_type = "general"
    # Name introduced in plain chat? ("my name is X" / "call me X" / "i am X")
    detected_name = extract_name_from_message(request.message)
    
    # Web-safety detection layer (XSS/script vectors), audit only.
    # strip_html_vectors() is destructive - it HTML-escapes quotes and braces -
    # so it is used purely as a signal: if it alters the message we flag it,
    # then continue with the ORIGINAL text so the agent is not degraded.
    # This is NOT prompt-injection defence; see detect_prompt_injection below.
    sanitized_message = strip_html_vectors(request.message)
    if sanitized_message != request.message:
        audit_logger.log_suspicious_activity(
            "unsanitized_input",
            get_client_ip(http_request),
            f"chat message contained potentially dangerous content",
        )

    # Prompt-injection detection (audit only, non-blocking). The structural
    # defense lives in the agent modules (PROMPT_INJECTION_GUARD + delimiters);
    # here we just flag attempts so they show up in the audit trail.
    if detect_prompt_injection(request.message):
        audit_logger.log_suspicious_activity(
            "prompt_injection_attempt",
            get_client_ip(http_request),
            "chat message matched a prompt-injection pattern",
        )
    
    try:
        # Check for special commands
        if request.message.lower().startswith("remember "):
            content = request.message[9:].strip()
            # Support "my name is X" format
            name_match = _NAME_PATTERN.search(content)
            if name_match:
                facts.remember("user's name", name_match.group(1), user_id=user_id)
                response = f"✅ Remembered: your name is {name_match.group(1)}"
            elif "=" in content:
                key, value = content.split("=", 1)
                facts.remember(key.strip(), value.strip(), user_id=user_id)
                response = f"✅ Remembered: {key.strip()} = {value.strip()}"
            else:
                # Support "X is Y" format (e.g. "favorite color is blue")
                is_match = _IS_PATTERN.search(content)
                if is_match:
                    facts.remember(is_match.group(1).strip(), is_match.group(2).strip(), user_id=user_id)
                    response = f"✅ Remembered: {is_match.group(1).strip()} = {is_match.group(2).strip()}"
                else:
                    response = "Usage: remember key = value, or say 'my name is X', or 'X is Y'."
        
        elif request.message.lower().startswith("recall "):
            key = request.message[7:].strip()
            # "recall my name" → look up "user's name"
            if _NAME_PATTERN.search(key) or key.lower() in ("my name", "my name please"):
                key = "user's name"
            value = facts.recall(key, user_id=user_id)
            if value:
                response = f"✅ {key}: {value}"
            else:
                response = f"I don't remember anything about '{key}'."
        
        elif request.message.lower() == "show facts":
            all_facts = facts.get_all_facts(user_id=user_id)
            if all_facts:
                response = "\n".join(f"- {k}: {v}" for k, v in all_facts.items())
            else:
                response = "No facts stored."
        
        elif request.message.lower().startswith("search "):
            query = request.message[7:].strip()
            response = await asyncio.wait_for(asyncio.to_thread(rag.get_context, query), timeout=DOWNSTREAM_TIMEOUT)
        
        elif detected_name:
            # Name memory: "my name is X" stores the user's name as a fact so
            # it survives across conversations and can be recalled later.
            facts.remember("user's name", detected_name, user_id=user_id)
            response = f"Nice to meet you, **{detected_name}**! I've remembered your name."
            query_type = "name_introduction"
        
        elif is_name_question(request.message):
            # "what is my name?" recalls the stored name. MUST run before the
            # multi-agent routing in the else-branch below, which would
            # otherwise treat "what is my name?" (matches "what is") as a
            # research task and answer with a generic essay instead.
            stored_name = facts.recall("user's name", user_id=user_id)
            if "don't remember" in stored_name.lower():
                response = "I don't know your name yet. Tell me by saying something like \"My name is ...\""
            else:
                response = f"Your name is **{stored_name}**!"
            query_type = "name_question"
        
        else:
            # Check if user is asking about a stored fact
            all_facts = facts.get_all_facts(user_id=user_id)
            fact_key, fact_value = _match_fact_question(all_facts, request.message)
            fact_answered = False
            if fact_key is not None:
                response = f"Your {fact_key} is **{fact_value}**."
                fact_answered = True
            
            if not fact_answered:
                # Simple greeting handler - bypass agent for simple messages
                greetings = ["hi", "hello", "hey", "hola", "good morning", "good afternoon", "good evening", "howdy", "yo", "sup"]
                if is_document_question(request.message, rag):
                    # Clean answers about uploaded documents (RAG retrieval +
                    # LLM summary) instead of dumping raw chunks.
                    response = await asyncio.wait_for(asyncio.to_thread(answer_from_documents, request.message, rag), timeout=DOWNSTREAM_TIMEOUT)
                    query_type = "document_question"
                    # Anchor this conversation so later messages like
                    # "what about the pricing column?" route back here.
                    await _set_doc_followup(conversation_id, request.message)
                else:
                    # Doc-aware follow-up: message continues an earlier
                    # document question in this conversation.
                    followup_query = await _doc_followup_query(conversation_id, request.message)
                    if followup_query:
                        response = await asyncio.wait_for(asyncio.to_thread(answer_from_documents, followup_query, rag), timeout=DOWNSTREAM_TIMEOUT)
                        query_type = "document_question"
                    elif any(g in request.message.lower() for g in greetings):
                        response = "Hello! How can I help you today?"
                    else:
                        # Try routing through multi-agent system for specialized tasks
                        from multi_agent import MultiAgentOrchestrator

                        use_multi_agent, agent_type = route_multi_agent(request.message)

                        if use_multi_agent:
                            query_type = f"multi_agent:{agent_type}"
                            orchestrator = MultiAgentOrchestrator()
                            # Strip @Agent prefix so the agent sees
                            # the clean task, not the routing directive.
                            _, clean_message = detect_agent_prefix(request.message)
                            task = await _with_context(clean_message, memory, user_id, conversation_id)
                            # Parallel LLM fan-out: when the message matches several
                            # specialist intents, run all matched agents concurrently
                            # (each in its own thread) and combine their answers,
                            # instead of running them one-by-one.
                            matched = _ROUTING_AC.find_payloads(request.message.lower())
                            if len(matched) > 1:
                                parallel = await run_agents_parallel(sorted(matched), task, orchestrator)
                                response = "\n\n".join(
                                    f"**{k.title()} Agent:**\n{v}" for k, v in parallel.items()
                                )
                                await _record_agent_usage(sorted(matched))
                            else:
                                response = await asyncio.wait_for(
                                    asyncio.to_thread(orchestrator.agents[agent_type].execute, task),
                                    timeout=AGENT_TIMEOUT,
                                )
                                await _record_agent_usage([agent_type])
                        else:
                            # Fall back to general ReAct agent (it injects this
                            # conversation's recent history + facts internally, so
                            # follow-ups about earlier messages work).
                            from llama_agent1 import run_agent
                            response = await asyncio.wait_for(
                                run_agent(
                                    request.message,
                                    memory=memory,
                                    facts=facts,
                                    user_id=user_id,
                                    conversation_id=conversation_id,
                                ),
                                timeout=AGENT_TIMEOUT,
                            )
        
        # Calculate response time
        elapsed = (datetime.now() - start_time).total_seconds()
        
        # Log interaction (scoped to this user's conversation)
        # Mirrored to PostgreSQL + Redis cache for the frontend sidebar.
        await _persist_message(user_id, conversation_id, "user", request.message)
        await _persist_message(user_id, conversation_id, "assistant", str(response))
        logger.log_interaction(request.message, str(response), [], elapsed)
        
        # Enterprise monitoring: business metrics + SLA + alert sweep
        business_metrics.record_question(
            user_id=user_id,
            success=True,
            response_time=elapsed,
            query_type=query_type,
        )
        sla_monitor.record_response_time(elapsed)
        alert_manager.check_alerts(_alert_metrics())
        
        return ChatResponse(
            response=str(response),
            timestamp=datetime.now().isoformat(),
            response_time=elapsed
        )
        
    except Exception as e:
        logger.log_error(e, context=f"User: {request.message}")
        # Enterprise monitoring: record the failure + SLA error
        elapsed = (datetime.now() - start_time).total_seconds()
        sla_monitor.record_error("agent_error", str(e))
        business_metrics.record_question(
            user_id=user_id,
            success=False,
            response_time=elapsed,
            query_type="error",
        )
        raise HTTPException(status_code=500, detail="Internal server error")


# ============ STREAMING CHAT (SSE) ============


@app.post("/chat/stream")
async def chat_stream(
    request: ChatRequest,
    user = Depends(optional_auth),
):
    """Chat with streaming response (Server-Sent Events)."""
    require_api_key()
    from llama_agent1 import run_agent_stream

    # Single unified memory/facts instances (shared with /chat and /memory),
    # so EVERY response - general agent, multi-agent, and special commands -
    # is saved to the same conversation history.
    memory = app.state.memory
    facts = app.state.facts
    user_id = _request_uid(user)
    rag = _user_rag(user_id)  # this caller's documents only
    # Per-user default conversation (never share the legacy "default" id).
    conversation_id = request.conversation_id or _default_conversation_id(user_id)

    # Per-tenant token budget. llm_guard's ITPM window bounds tokens per
    # minute globally; this bounds what ONE user may spend per window, so a
    # single client looping a @team request cannot consume the whole
    # organisation quota. Disabled unless AGENT_USER_TOKEN_BUDGET is set.
    from modules import llm_guard as _guard
    if _guard.over_budget(user_id):
        _log.warning("token budget exhausted for %s", user_id)

        def _refuse():
            yield "data: " + json.dumps({"type": "reasoning", "stage": "budget",
                                         "content": _guard.budget_message()}) + "\n\n"
            yield f"data: {escape_sse(_guard.budget_message())}\n\n"
            yield "data: [DONE]\n\n"
        return StreamingResponse(_refuse(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache"})
    # SLO metrics: the branches below set the route; timed_chat_stream records
    # the full wait (start -> end of stream), outcome and time to first text.
    chat_meta = {"route": "command", "start": time.monotonic()}

    async def event_generator():
        # The body is iterated after the handler returns - re-bind the owner
        # so agent tools / caches stay scoped to this caller.
        bind_owner(user_id)
        # Hard cap on total stream duration (see STREAM_MAX_DURATION). The agent
        # streaming loops below are synchronous generators, so we check elapsed
        # time between yielded chunks and stop cleanly if the cap is exceeded.
        start = time.monotonic()

        # Resolve DSA feature config (request overrides server env defaults).
        # Surfaced to the UI as a `config` SSE event so the control panel and
        # the live reasoning view stay in sync with what actually ran.
        critic = (
            request.use_critic
            if request.use_critic is not None
            else (os.getenv("AGENT_CRITIQUE", "1") == "1")
        )
        reflect = (
            request.use_reflection
            if request.use_reflection is not None
            else (os.getenv("AGENT_REFLECT", "1") == "1")
        )
        parallel = (
            request.use_parallel
            if request.use_parallel is not None
            else _dsa_defaults()["parallel"]
        )
        agent_override = request.agent

        def config_event(applied_critic: bool, applied_reflect: bool,
                         applied_parallel: bool) -> str:
            """`config` SSE event describing what ACTUALLY runs on the route
            this message took (the UI chips), plus what was requested.
            Direct replies (facts, commands, documents, general chat) run no
            critic/reflection; a single specialist never runs in parallel;
            only the @team route can use all three."""
            return (
                "data: "
                + json.dumps({
                    "type": "config",
                    "critic": applied_critic,
                    "reflection": applied_reflect,
                    "parallel": applied_parallel,
                    "agent": agent_override,
                    "requested": {"critic": critic, "reflection": reflect,
                                  "parallel": parallel},
                })
                + "\n\n"
            )

        # Helper: record the assistant reply, emit it, then signal stream end.
        async def done(text: str):
            await _persist_message(user_id, conversation_id, "assistant", text)
            yield config_event(False, False, False)
            yield f"data: {escape_sse(text)}\n\n"
            yield "data: [DONE]\n\n"

        # Handle special commands first
        if request.message.lower().startswith("remember "):
            content = request.message[9:].strip()
            await _persist_message(user_id, conversation_id, "user", request.message)
            if "=" in content:
                key, value = content.split("=", 1)
                facts.remember(key.strip(), value.strip(), user_id=user_id)
                reply = f"✅ Remembered: {key.strip()} = {value.strip()}"
            else:
                reply = "Usage: remember key = value"
            async for chunk in done(reply):
                yield chunk
            return

        elif request.message.lower().startswith("recall "):
            key = request.message[7:].strip()
            await _persist_message(user_id, conversation_id, "user", request.message)
            result = facts.recall(key, user_id=user_id)
            async for chunk in done(result):
                yield chunk
            return

        elif request.message.lower() == "show facts":
            await _persist_message(user_id, conversation_id, "user", request.message)
            all_facts = facts.get_all_facts(user_id=user_id)
            result = "\n".join(f"- {k}: {v}" for k, v in all_facts.items()) if all_facts else "No facts stored."
            async for chunk in done(result):
                yield chunk
            return

        elif request.message.lower().startswith("search "):
            query = request.message[7:].strip()
            await _persist_message(user_id, conversation_id, "user", request.message)
            result = await asyncio.wait_for(asyncio.to_thread(rag.get_context, query), timeout=DOWNSTREAM_TIMEOUT)
            async for chunk in done(result):
                yield chunk
            return

        # Name memory (BEFORE multi-agent routing): "my name is X" stores the
        # user's name as a fact; "what is my name?" recalls it. Without this,
        # "what is my name?" (matches "what is") routes to the Research Agent
        # and gets a generic essay instead of the stored name.
        detected_name = extract_name_from_message(request.message)
        if detected_name:
            await _persist_message(user_id, conversation_id, "user", request.message)
            facts.remember("user's name", detected_name, user_id=user_id)
            reply = f"Nice to meet you, **{detected_name}**! I've remembered your name."
            async for chunk in done(reply):
                yield chunk
            return

        if is_name_question(request.message):
            await _persist_message(user_id, conversation_id, "user", request.message)
            stored_name = facts.recall("user's name", user_id=user_id)
            if "don't remember" in stored_name.lower():
                reply = "I don't know your name yet. Tell me by saying something like \"My name is ...\""
            else:
                reply = f"Your name is **{stored_name}**!"
            async for chunk in done(reply):
                yield chunk
            return

        # Check facts
        all_facts = facts.get_all_facts(user_id=user_id)
        fact_key, fact_value = _match_fact_question(all_facts, request.message)
        if fact_key is not None:
            await _persist_message(user_id, conversation_id, "user", request.message)
            reply = f"Your {fact_key} is **{fact_value}**."
            async for chunk in done(reply):
                yield chunk
            return

        # Clean answers about uploaded documents (RAG + LLM summary)
        if is_document_question(request.message, rag):
            chat_meta["route"] = "document"
            await _persist_message(user_id, conversation_id, "user", request.message)
            result = await asyncio.wait_for(asyncio.to_thread(answer_from_documents, request.message, rag), timeout=DOWNSTREAM_TIMEOUT)
            # Anchor this conversation so later messages like
            # "what about the pricing column?" route back here.
            await _set_doc_followup(conversation_id, request.message)
            async for chunk in done(result):
                yield chunk
            return

        # Doc-aware follow-up: message continues an earlier document
        # question in this conversation (e.g. "and the pricing column?").
        followup_query = await _doc_followup_query(conversation_id, request.message)
        if followup_query:
            chat_meta["route"] = "document"
            await _persist_message(user_id, conversation_id, "user", request.message)
            result = await asyncio.wait_for(asyncio.to_thread(answer_from_documents, followup_query, rag), timeout=DOWNSTREAM_TIMEOUT)
            async for chunk in done(result):
                yield chunk
            return

        # Check if this is a specialized task for multi-agent
        from multi_agent import MultiAgentOrchestrator

        use_multi_agent, agent_type = route_multi_agent(request.message)
        team_mode, team_task = detect_team_prefix(request.message)
        if (agent_override or "").lower() == "team":
            team_mode = True
        elif agent_override in AGENT_KEYS:
            # An explicit agent picked in the UI always wins over keyword routing.
            use_multi_agent, agent_type = True, agent_override

        if team_mode:
            chat_meta["route"] = "team"
            # "@team ..." / agent=team: plan a DAG and run the whole agent team,
            # streaming each agent's tokens with agent_start/agent_done events.
            await _persist_message(user_id, conversation_id, "user", request.message)
            orchestrator = MultiAgentOrchestrator()
            task = await _with_context(team_task, memory, user_id, conversation_id)
            yield config_event(critic, reflect, parallel)
            yield (
                "data: "
                + json.dumps({"type": "reasoning", "stage": "route",
                              "content": "Collaborating with the full agent team"})
                + "\n\n"
            )
            final_text = ""
            streamed = ""
            used_agents = set()
            _redactor = StreamRedactor()
            try:
                async for ev in _aiter_in_thread(lambda: orchestrator.collaborate_stream(
                        task, critique=critic, reflect=reflect, parallel=parallel)):
                    if time.monotonic() - start > STREAM_MAX_DURATION:
                        yield f"data: {escape_sse(f'⚠️ Stream timed out after {int(STREAM_MAX_DURATION)}s.')}\n\n"
                        break
                    etype = ev.get("type")
                    if etype == "token":
                        streamed += ev.get("content", "")
                        _safe = _redactor.feed(ev.get("content", ""))
                        if _safe:
                            yield f"data: {escape_sse(_safe)}\n\n"
                    elif etype in ("tool", "reasoning"):
                        # Tool activity / plan / critic notes go to the
                        # reasoning view, never into the answer text.
                        yield _reasoning_sse(ev)
                    elif etype == "agent_start":
                        used_agents.add(ev.get("agent"))
                        header = f"\n\n### {ev.get('agent', 'Agent')}\n\n"
                        streamed += header
                        yield "data: " + json.dumps({"type": "agent_start", "agent": ev.get("agent"), "step": ev.get("step")}) + "\n\n"
                        yield f"data: {escape_sse(header)}\n\n"
                    elif etype == "agent_done":
                        yield "data: " + json.dumps({"type": "agent_done", "agent": ev.get("agent"), "step": ev.get("step")}) + "\n\n"
                    elif etype == "final":
                        final_text = ev.get("content", "")
            except Exception :
                logging.getLogger(__name__).exception("team collaboration failed")
                err = "⚠️ The agent team could not complete this task."
                streamed += "\n\n" + err
                yield f"data: {escape_sse(err)}\n\n"
            await _record_agent_usage(sorted(
                k for k, a in orchestrator.agents.items() if a.name in used_agents))
            if not (final_text or streamed).strip():
                streamed = "⚠️ The agent team returned an empty answer. Please try again."
                yield f"data: {escape_sse(streamed)}\n\n"
            await _persist_message(user_id, conversation_id, "assistant", final_text or streamed)
            yield "data: [DONE]\n\n"
            return

        if use_multi_agent:
            chat_meta["route"] = "agent"
            # Stream the agent-team run: tool activity first, then answer
            # tokens - the same experience as general chat, no blocking
            # spinner. Inject recent conversation context so follow-ups about
            # earlier messages work.
            await _persist_message(user_id, conversation_id, "user", request.message)
            orchestrator = MultiAgentOrchestrator()
            # Honor an explicit agent selection from the UI control panel.
            if agent_override and agent_override in orchestrator.agents:
                agent_type = agent_override
            # Strip @Agent prefix so the agent sees the clean task.
            _, clean_message = detect_agent_prefix(request.message)
            task = await _with_context(clean_message, memory, user_id, conversation_id)
            agent = orchestrator.agents[agent_type]
            await _record_agent_usage([agent_type])
            yield config_event(critic, reflect, False)
            yield (
                "data: "
                + json.dumps(
                    {
                        "type": "reasoning",
                        "stage": "route",
                        "content": f"Routing to {agent.name}",
                    }
                )
                + "\n\n"
            )
            full = ""
            reviewed = False  # the agent ran critic/reflection itself
            _redactor = StreamRedactor()
            try:
                async for event in _aiter_in_thread(lambda: agent.execute_stream(
                        task, critique=critic, reflect=reflect)):
                    if time.monotonic() - start > STREAM_MAX_DURATION:
                        yield f"data: {escape_sse(f'⚠️ Stream timed out after {int(STREAM_MAX_DURATION)}s.')}\n\n"
                        break
                    etype = event.get("type")
                    if etype in ("tool", "reasoning"):
                        if etype == "reasoning" and event.get("stage") in ("critic", "reflection"):
                            reviewed = True
                        yield _reasoning_sse(event)
                    else:
                        full += event.get("content", "")
                        _safe = _redactor.feed(event.get("content", ""))
                        if _safe:
                            yield f"data: {escape_sse(_safe)}\n\n"
            except Exception:
                logging.getLogger(__name__).exception("%s agent stream failed", agent_type)
                full = f"⚠️ The {agent.name} could not complete this task. Please try again."
                yield f"data: {escape_sse(full)}\n\n"
            _tail = _redactor.flush()
            if _tail:
                full += _tail
                yield f"data: {escape_sse(_tail)}\n\n"
            if not full.strip():
                full = "⚠️ The model returned an empty answer. Please try again, or rephrase the question."
                yield f"data: {escape_sse(full)}\n\n"
            # DSA: critic pass on an answer that streamed live (the agent
            # reviews pre-generated answers itself, before streaming them).
            if critic and not reviewed and not full.startswith("⚠️"):
                try:
                    note = orchestrator.agents["analyzer"].critique(task, full)
                    if note:
                        yield (
                            "data: "
                            + json.dumps(
                                {"type": "reasoning", "stage": "critic", "content": note}
                            )
                            + "\n\n"
                        )
                except Exception as _e:
                    degrade("critic pass produced no note for this answer", _e)
            _tail = _redactor.flush()
            if _tail:
                streamed += _tail
                full = (full or "") + _tail
                yield f"data: {escape_sse(_tail)}\n\n"
            await _persist_message(user_id, conversation_id, "assistant", full)
            yield "data: [DONE]\n\n"
        else:
            chat_meta["route"] = "general"
            # Stream from general ReAct agent (it reads + writes the SAME memory
            # instances, so the response is saved to this user's conversation).
            yield config_event(False, False, False)
            yield (
                "data: "
                + json.dumps(
                    {
                        "type": "reasoning",
                        "stage": "route",
                        "content": "Llama Agent is thinking",
                    }
                )
                + "\n\n"
            )
            full_response = ""
            _redactor = StreamRedactor()
            async for token in _aiter_in_thread(lambda: run_agent_stream(
                request.message,
                memory=memory,
                facts=facts,
                user_id=user_id,
                conversation_id=conversation_id,
            )):
                if time.monotonic() - start > STREAM_MAX_DURATION:
                    yield f"data: {escape_sse(f'⚠️ Stream timed out after {int(STREAM_MAX_DURATION)}s.')}\n\n"
                    break
                full_response += token
                _safe = _redactor.feed(token)
                if _safe:
                    yield f"data: {escape_sse(_safe)}\n\n"
            _tail = _redactor.flush()
            if _tail:
                full_response += _tail
                yield f"data: {escape_sse(_tail)}\n\n"
            # The agent already wrote both messages to the JSON memory store;
            # mirror them to PostgreSQL (durable history the sidebar reads) and
            # refresh the Redis caches so the sidebar/conversation views agree.
            await _mirror_message(user_id, conversation_id, "user", request.message)
            await _mirror_message(user_id, conversation_id, "assistant", full_response)
            yield "data: [DONE]\n\n"

    return StreamingResponse(
        timed_chat_stream(event_generator(), chat_meta),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


def _dsa_defaults() -> Dict[str, bool]:
    """Server-side defaults for the DSA toggles (request fields override)."""
    return {
        "critic": os.getenv("AGENT_CRITIQUE", "1") == "1",
        "reflection": os.getenv("AGENT_REFLECT", "1") == "1",
        "parallel": os.getenv("AGENT_PARALLEL_STEPS", "1") == "1",
    }


def _reasoning_sse(event: Dict[str, Any]) -> str:
    """Forward an agent tool/reasoning event as a structured `reasoning`
    SSE event, so tool markers never get glued into the answer text."""
    if event.get("type") == "tool":
        payload = {"type": "reasoning", "stage": "tool",
                   "content": str(event.get("content", "")).strip()}
    else:
        payload = {"type": "reasoning", "stage": event.get("stage", "note"),
                   "content": event.get("content", "")}
    return "data: " + json.dumps(payload) + "\n\n"


@app.get("/chat/options")
async def chat_options():
    """DSA defaults so the UI toggles show what an unset toggle will do."""
    return {
        "defaults": _dsa_defaults(),
        "applies_to": {
            "critic": "specialist agents and @team",
            "reflection": "specialist agents and @team steps",
            "parallel": "@team (independent steps run concurrently)",
        },
    }


def _redacted_stream(chunks):
    """Wrap a token stream so PII is redacted before the user sees it.

    Redaction used to happen only on the way into logs (modules/monitoring.py,
    modules/security_hardening.py). A model that echoes an SSN or a card number
    out of an uploaded document sent it straight to the browser. This is the
    output half of that boundary, and it is stream-aware: StreamRedactor holds
    back enough text that a number split across two tokens is still redacted
    whole.
    """
    from modules.pii import StreamRedactor
    redactor = StreamRedactor()
    for chunk in chunks:
        out = redactor.feed(chunk)
        if out:
            yield out
    tail = redactor.flush()
    if tail:
        yield tail










# ============ FACTS ENDPOINTS ============


@app.post("/remember")
async def remember_fact(request: RememberRequest, user = Depends(optional_auth)):
    """Remember a fact"""
    try:
        facts = app.state.facts
        user_id = _request_uid(user)
        facts.remember(request.key, request.value, user_id=user_id)
        return {"status": "success", "message": f"Remembered: {request.key} = {request.value}"}
    except Exception :
        raise HTTPException(status_code=500, detail="Internal server error")


@app.post("/recall")
async def recall_fact(request: RecallRequest, user = Depends(optional_auth)):
    """Recall a fact"""
    try:
        facts = app.state.facts
        user_id = _request_uid(user)
        value = facts.recall(request.key, user_id=user_id)
        return {"key": request.key, "value": value}
    except Exception :
        raise HTTPException(status_code=500, detail="Internal server error")


@app.get("/facts")
async def list_facts(user = Depends(optional_auth)):
    """List all facts"""
    try:
        facts = app.state.facts
        user_id = _request_uid(user)
        all_facts = facts.get_all_facts(user_id=user_id)
        return {"facts": all_facts}
    except Exception :
        raise HTTPException(status_code=500, detail="Internal server error")


@app.delete("/facts/{key}")
async def delete_fact(key: str, user = Depends(optional_auth)):
    """Delete a fact"""
    try:
        facts = app.state.facts
        user_id = _request_uid(user)
        facts.forget(key, user_id=user_id)
        return {"status": "success", "message": f"Deleted: {key}"}
    except Exception :
        raise HTTPException(status_code=500, detail="Internal server error")


# ============ DOCUMENTS ENDPOINTS ============
#
# Every document endpoint is scoped to the caller (registered user id, or
# the per-browser guest id). Files are stored under uploads/<owner>/ and
# indexed with owner metadata, so users never see each other's documents.


def _user_upload_dir(user_id: str) -> str:
    path = os.path.join(UPLOAD_ROOT, owner_dir_name(user_id))
    os.makedirs(path, exist_ok=True)
    return path


def _write_bytes(path: str, data: bytes) -> None:
    """Blocking write, called via asyncio.to_thread so it never stalls the loop."""
    with open(path, "wb") as f:
        f.write(data)


@app.post("/documents/read")
async def read_document(file_path: str, user: Optional[Dict] = Depends(optional_auth)):
    """(Re-)index one of the caller's OWN uploaded files.

    Only paths inside uploads/<caller>/ are accepted (realpath containment,
    symlinks neutralized). Arbitrary server paths - data/ (memory & facts
    JSON), other users' uploads, ../../etc/passwd - are rejected.
    """
    try:
        from modules.security import is_path_within_roots

        user_id = _request_uid(user)
        own_dir = _user_upload_dir(user_id)
        candidate = file_path
        if not os.path.isabs(candidate) and not is_path_within_roots(candidate, [own_dir]):
            # Allow bare filenames: "report.pdf" -> uploads/<me>/report.pdf
            candidate = os.path.join(own_dir, os.path.basename(file_path))
        if not is_path_within_roots(candidate, [own_dir]):
            raise HTTPException(
                status_code=400,
                detail="Invalid file path: only your own uploaded files can be read",
            )
        if not os.path.isfile(candidate):
            raise HTTPException(status_code=404, detail="File not found")

        rag = _user_rag(user_id)
        result = await asyncio.to_thread(rag.add_document, candidate)
        _bump_doc_generation(user_id)
        return {"status": "success", "message": result}
    except HTTPException:
        raise
    except Exception :
        raise HTTPException(status_code=500, detail="Internal server error")


@app.post("/documents/upload")
async def upload_document(file: UploadFile = File(...), user: Optional[Dict] = Depends(optional_auth)):
    """Upload and read a document (with security validation)"""
    try:
        # SECURITY: Validate file upload
        from modules.security import validate_file_upload, sanitize_filename, get_upload_path

        user_id = _request_uid(user)
        # Resolve user for audit logging (works with or without auth)
        username = (user or {}).get("username") or "anonymous"

        # Read file content to check size and validate
        content = await file.read()
        file_size = len(content)

        is_valid, error = validate_file_upload(
            file.filename or "unknown",
            file_size,
            file.content_type,
        )
        if not is_valid:
            # Enterprise: audit rejected upload
            audit_logger.log_file_upload(
                file.filename or "unknown",
                username,
                success=False,
                reason=error,
            )
            raise HTTPException(status_code=400, detail=f"Upload rejected: {error}")

        # Per-owner directory: two users uploading "report.pdf" never collide
        upload_dir = _user_upload_dir(user_id)
        safe_filename = sanitize_filename(file.filename or "uploaded_file")
        file_path = get_upload_path(upload_dir, safe_filename)

        # Off the event loop: this runs inside `async def`, and a synchronous
        # write stalls every other in-flight request for its duration. The cap
        # is MAX_FILE_SIZE (10 MB), so the stall is short — but the next line
        # already offloads its work, and there was no reason for this one to
        # block.
        await asyncio.to_thread(_write_bytes, file_path, content)

        # Index for this owner only (replaces a previous upload of the same
        # name) and drop this owner's cached answers.
        rag = _user_rag(user_id)
        result = await asyncio.to_thread(rag.add_document, file_path)
        _bump_doc_generation(user_id)

        # Enterprise: audit success + business metrics
        audit_logger.log_file_upload(safe_filename, username, success=True)
        business_metrics.record_document_upload(username)
        business_metrics.record_feature_usage("document_upload")

        return {"status": "success", "message": result, "file": safe_filename}
    except HTTPException:
        raise
    except Exception :
        raise HTTPException(status_code=500, detail="Internal server error")


@app.post("/documents/search")
async def search_documents(request: SearchRequest, user: Optional[Dict] = Depends(optional_auth)):
    """Search the caller's documents (raw retrieval, for power users)"""
    try:
        rag = _user_rag(_request_uid(user))
        results = await asyncio.wait_for(asyncio.to_thread(rag.search, request.query, request.top_k), timeout=DOWNSTREAM_TIMEOUT)
        return {"query": request.query, "results": results}
    except Exception :
        raise HTTPException(status_code=500, detail="Internal server error")


@app.post("/documents/ask")
async def ask_documents(request: SearchRequest, user: Optional[Dict] = Depends(optional_auth)):
    """Ask a question about the caller's uploaded documents and get a clean,
    LLM-written answer (instead of raw chunk JSON)."""
    require_api_key()
    try:
        rag = _user_rag(_request_uid(user))
        answer = await asyncio.wait_for(asyncio.to_thread(answer_from_documents, request.query, rag), timeout=DOWNSTREAM_TIMEOUT)
        return {"query": request.query, "answer": answer}
    except Exception :
        raise HTTPException(status_code=500, detail="Internal server error")


@app.get("/documents")
async def list_documents(user: Optional[Dict] = Depends(optional_auth)):
    """List the caller's loaded documents"""
    try:
        rag = _user_rag(_request_uid(user))
        docs = rag.list_documents()
        return {"documents": docs}
    except Exception :
        raise HTTPException(status_code=500, detail="Internal server error")


# ============ MEMORY ENDPOINTS ============


@app.get("/memory")
async def get_memory(
    user = Depends(optional_auth),
    limit: int = 50,
):
    """Get conversation memory (scoped to the current user)"""
    try:
        memory = app.state.memory
        user_id = _request_uid(user)
        recent = memory.get_recent(min(max(1, limit), 200), user_id=user_id)
        stats = memory.get_stats()
        conversations = await _list_conversations_for(user_id, memory)
        return {
            "stats": stats,
            "recent_messages": recent,
            "conversations": conversations,
        }
    except Exception :
        raise HTTPException(status_code=500, detail="Internal server error")


@app.delete("/memory")
async def clear_memory(user = Depends(optional_auth)):
    """Clear memory for the current user (conversations + facts)"""
    from modules import pg_store, redis_store
    try:
        memory = app.state.memory
        facts = app.state.facts
        user_id = _request_uid(user)
        memory.clear(user_id=user_id)
        facts.clear(user_id=user_id)
        # Clear the durable store + caches too so the sidebar matches.
        await pg_store.clear_user(user_id)
        await redis_store.invalidate_user(user_id)
        return {"status": "success", "message": f"Memory cleared for {user_id}"}
    except Exception :
        raise HTTPException(status_code=500, detail="Internal server error")


# ============ CONVERSATION ENDPOINTS ============
# Per-user conversation history used by the Next.js chat-history sidebar.
# Chats are created/resumed by conversation_id, so each conversation in the
# sidebar maps 1:1 to a persisted conversation in the memory store.


@app.get("/conversations")
async def list_conversations(
    user = Depends(optional_auth),
    limit: int = 50,
    offset: int = 0,
):
    """List the current user's conversations (newest first), paginated.

    Read path: Redis cache -> PostgreSQL (durable store) -> JSON memory store.
    Legacy memory conversations are merged in so pre-mirror history is shown.
    """
    try:
        memory = app.state.memory
        user_id = _request_uid(user)
        convs = await _list_conversations_for(user_id, memory)
        total = len(convs)
        limit = max(1, min(limit, 200))
        offset = max(0, offset)
        return {
            "conversations": convs[offset:offset + limit],
            "total": total,
            "limit": limit,
            "offset": offset,
        }
    except Exception :
        raise HTTPException(status_code=500, detail="Internal server error")


@app.get("/conversations/{conversation_id}")
async def get_conversation(conversation_id: str, user = Depends(optional_auth)):
    """Get one conversation including its full message history.

    Read path: Redis cache -> PostgreSQL -> JSON memory fallback (legacy
    conversations that predate the Postgres mirror).
    """
    from modules import pg_store, redis_store
    try:
        memory = app.state.memory
        user_id = _request_uid(user)
        norm = pg_store.normalize_conversation_id(conversation_id)
        msgs = await redis_store.get_json(redis_store.messages_key(norm))
        if msgs is None:
            msgs = await pg_store.get_messages(user_id, conversation_id)
        if msgs is not None:
            summary = await pg_store.get_conversation(user_id, conversation_id)
            if summary is not None:
                await redis_store.set_json(redis_store.messages_key(norm), msgs, ttl=redis_store.MSGS_TTL)
                summary["messages"] = msgs
                return summary
            # Conversation not in Postgres (or not owned) -> try legacy memory
        summary = memory.get_conversation(user_id, conversation_id)
        if summary is None:
            raise HTTPException(status_code=404, detail="Conversation not found")
        summary["messages"] = memory.get_messages(user_id, conversation_id)
        return summary
    except HTTPException:
        raise
    except Exception :
        raise HTTPException(status_code=500, detail="Internal server error")


class CreateConversationRequest(BaseModel):
    """Create conversation request"""
    title: Optional[str] = Field(default="New chat", max_length=200)


@app.post("/conversations")
async def create_conversation(request: CreateConversationRequest = None,
                              user = Depends(optional_auth)):
    """Create a new empty conversation and return its id"""
    from modules import pg_store, redis_store
    try:
        memory = app.state.memory
        user_id = _request_uid(user)
        title = request.title if request is not None and request.title else "New chat"
        conv = memory.create_conversation(user_id, title=title)
        # Mirror into PostgreSQL + drop the user's cached list so the sidebar
        # sees the new conversation immediately.
        await pg_store.ensure_conversation(user_id, conv["id"], title=title)
        await redis_store.invalidate_user(user_id)
        return conv
    except Exception :
        raise HTTPException(status_code=500, detail="Internal server error")


@app.delete("/conversations/{conversation_id}")
async def delete_conversation(conversation_id: str, user = Depends(optional_auth)):
    """Delete a conversation (only the owner may delete it)"""
    from modules import pg_store, redis_store
    try:
        memory = app.state.memory
        user_id = _request_uid(user)
        deleted_mem = memory.delete_conversation(user_id, conversation_id)
        deleted_pg = await pg_store.delete_conversation(user_id, conversation_id)
        await redis_store.invalidate_user(user_id)
        await redis_store.invalidate_conversation(pg_store.normalize_conversation_id(conversation_id))
        if not deleted_mem and not deleted_pg:
            raise HTTPException(status_code=404, detail="Conversation not found")
        return {"status": "success", "conversation_id": conversation_id}
    except HTTPException:
        raise
    except Exception :
        raise HTTPException(status_code=500, detail="Internal server error")


# ============ ALERT METRICS ============

def _alert_metrics() -> Dict:
    """The metrics dict every alert rule is evaluated against.

    ``AlertManager.rules`` are one flat namespace of keys, but the data lives
    in two places: business metrics (failure_rate, avg_response_time) and the
    Prometheus cost summary (total_cost). Earlier this passed only the first,
    so any rule reading a cost field could never fire. Merging here means a
    rule and its input are defined in the same place, and a key that is not in
    this dict is visibly absent rather than silently defaulting to 0.
    """
    from modules.prometheus_metrics import cost_summary
    metrics = dict(business_metrics.get_metrics())
    metrics["total_cost"] = cost_summary().get("total_cost", 0.0)
    return metrics


# ============ METRICS ENDPOINTS ============


@app.get("/metrics")
async def get_metrics_prometheus(request: Request):
    """Prometheus-format metrics endpoint for scraping.

    Prometheus scrapes the API directly on the internal network. Requests
    that arrive through a reverse proxy (Nginx, the k8s ingress, the Next.js
    /api rewrite - all of which add X-Forwarded-For) come from the internet
    and are refused, so internal metrics (routes, error rates, agent usage)
    are never public. Set METRICS_ALLOW_PROXIED=1 to opt out.
    """
    if (request.headers.get("x-forwarded-for")
            and os.getenv("METRICS_ALLOW_PROXIED", "0") != "1"):
        raise HTTPException(status_code=404, detail="Not Found")
    return metrics_endpoint(None)


@app.get("/metrics/json")
async def get_metrics_json(user=Depends(require_admin)):
    """JSON metrics endpoint for dashboards (admin only).

    Process health plus the error log's own count. The running counters that
    used to be returned here (total_queries, popular_tools, avg_response_time)
    were a per-process second set of books for events Prometheus already
    counts -- `/metrics` is the number to read for those.
    """
    try:
        monitor = app.state.monitor
        logger = app.state.logger

        health = monitor.get_health()
        return {
            "health": health,
            "metrics": {
                "total_errors": logger.get_error_count(),
            },
        }
    except Exception :
        raise HTTPException(status_code=500, detail="Internal server error")


@app.get("/monitoring/dashboard")
async def monitoring_dashboard(user=Depends(require_admin)):
    """
    Enterprise monitoring dashboard: business metrics, costs, SLA
    compliance, recent alerts, feature flags, and DB health (admin only -
    it exposes security counters and cost data).
    """
    try:
        dashboard = get_monitoring_dashboard()
        # Attach runtime operations data
        dashboard["feature_flags"] = feature_flags.get_all_flags()
        dashboard["db_health"] = getattr(app.state, "db_health_result", None)
        dashboard["security"] = {
            "brute_force_lockouts": len([
                k for k, v in brute_force.attempts.items()
                if v.get("locked_until", 0) > 0
            ]),
            "audit_events": len(audit_logger.events),
        }
        return dashboard
    except Exception :
        raise HTTPException(status_code=500, detail="Internal server error")


@app.get("/agents/stats")
async def agent_team_stats(user = Depends(optional_auth)):
    """Real-time Agent Team statistics for the dashboard.

    Per-agent tasks handled / last-used are counted from real dispatches
    (chat multi-agent routing + MCP tool calls). Health numbers come from the
    live performance monitor. Nothing is simulated.
    """
    try:
        stats = await _load_shared_dict(_AGENT_STATS_KEY, {"handled": {}, "last_used": {}})
        handled = stats.get("handled", {})
        last_used = stats.get("last_used", {})
        health = app.state.monitor.get_health()
        agents = [
            {
                "key": key,
                "name": f"{key.capitalize()} Agent",
                "tasks_handled": handled.get(key, 0),
                "last_used": last_used.get(key),
            }
            for key in AGENT_KEYS
        ]
        return {
            "agents": agents,
            "health": health,
            "metrics": {
                "total_agents": len(AGENT_KEYS),
                "tasks_handled_total": sum(handled.values()),
            },
            "generated_at": datetime.now(timezone.utc).isoformat(),
        }
    except Exception :
        raise HTTPException(status_code=500, detail="Internal server error")


@app.get("/tools")
async def list_tools():
    """List all available tools"""
    return {
        "tools": [
            {"name": "calculator", "description": "Mathematical calculations"},
            {"name": "get_datetime", "description": "Get current date and time"},
            {"name": "text_analyzer", "description": "Analyze text statistics"},
            {"name": "unit_converter", "description": "Convert between units"},
            {"name": "string_operations", "description": "String operations"},
            {"name": "remember_fact", "description": "Remember a fact"},
            {"name": "recall_fact", "description": "Recall a stored fact"},
            {"name": "list_facts", "description": "List all facts"},
            {"name": "read_document", "description": "Read a document"},
            {"name": "search_documents", "description": "Search documents"},
            {"name": "list_documents", "description": "List loaded documents"},
            {"name": "get_memory_stats", "description": "Get memory statistics"},
            {"name": "clear_memory", "description": "Clear all memory"},
            {"name": "get_agent_health", "description": "Get agent health"},
        ]
    }


# ============ MCP ENDPOINTS ============
# These endpoints talk to the MCP Agent Server (llama-agent-mcp) over the
# Model Context Protocol using SSE transport. They prove MCP is a live
# integration technology in this stack:
#   frontend -> REST API -> MCP SSE -> multi-agent system -> Groq LLM


@app.get("/mcp/status")
async def mcp_status(user = Depends(optional_auth)):
    """Connect to the MCP Agent Server via SSE, run the MCP initialize
    handshake, and list all tools it exposes."""
    # Enterprise: feature flag gating
    if not feature_flags.is_enabled("enable_mcp_agents"):
        raise HTTPException(status_code=404, detail="MCP agents disabled (feature flag)")
    business_metrics.record_feature_usage("mcp_status")
    try:
        return await get_mcp_status()
    except Exception :
        raise HTTPException(
            status_code=503,
            detail="MCP server unreachable",
        )


@app.post("/mcp/tools")
async def mcp_tool_call(request: MCPToolRequest, user = Depends(get_current_user)):
    """Invoke a tool on the MCP Agent Server over the MCP protocol (SSE).

    Requires login: each call runs LLM-backed agents (real cost), so it is
    not available to anonymous visitors.

    Example body:
        {"name": "coder_agent", "arguments": {"task": "write a python function to sort a list"}}
    """
    # Enterprise: feature flag gating
    if not feature_flags.is_enabled("enable_mcp_agents"):
        raise HTTPException(status_code=404, detail="MCP agents disabled (feature flag)")
    business_metrics.record_feature_usage("mcp_tool_call")
    # Track real usage for the Agent Team dashboard (team_status runs no agent)
    agent_keys = _MCP_TOOL_TO_AGENT.get(request.name)
    if agent_keys:
        await _record_agent_usage(agent_keys)
    if _mcp_circuit_open():
        raise HTTPException(status_code=503, detail="MCP server temporarily unavailable")
    try:
        result = await asyncio.wait_for(
            call_mcp_tool(request.name, request.arguments), timeout=MCP_TIMEOUT
        )
        _mcp_record_success()
        return result
    except asyncio.TimeoutError:
        _mcp_record_failure()
        raise HTTPException(status_code=504, detail="MCP tool call timed out")
    except HTTPException:
        raise
    except Exception :
        _mcp_record_failure()
        raise HTTPException(status_code=502, detail="MCP tool call failed")
