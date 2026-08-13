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
import logging
import os
import re
import sys
import uuid
from datetime import datetime, timezone
from typing import Optional, List, Dict
from contextlib import asynccontextmanager

from fastapi import (
    FastAPI,
    HTTPException,
    UploadFile,
    File,
    WebSocket,
    WebSocketDisconnect,
    Depends,
    status,
    Request,
    Response,
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, EmailStr, Field

# Add current directory to path
sys.path.insert(0, os.path.dirname(__file__))

# Import new modules
from modules.config import get_config, AppConfig
from modules.database import get_db_manager
from modules.model_tiers import resolve_model, should_fallback
from modules.auth import (
    get_current_user,
    optional_auth,
    user_manager,
    jwt_manager,
    authenticate_ws_token,
)
from modules.websocket import get_ws_handler, MessageType, WSMessage

# MCP client (real Model Context Protocol over SSE)
from modules.mcp_client import MCP_SERVER_URL, get_mcp_status, call_mcp_tool

# WebSocket handler
ws_handler = get_ws_handler()

# Prometheus metrics
from modules.prometheus_metrics import (
    init_metrics,
    metrics_endpoint,
    prometheus_middleware,
)

# Enterprise security hardening (headers, brute-force, audit, sanitization, CSRF)
from modules.security_hardening import (
    brute_force,
    audit_logger,
    get_security_headers,
    get_client_ip,
    sanitize_input,
)

# Enterprise monitoring (alerts, business metrics, cost tracking, SLA)
from modules.advanced_monitoring import (
    alert_manager,
    business_metrics,
    cost_tracker,
    sla_monitor,
    get_monitoring_dashboard,
)

# Enterprise operations (feature flags, A/B testing, deployment tracking)
from modules.cicd_improvements import feature_flags

# Module logger (used by the best-effort Postgres/Redis persistence mirror)
_log = logging.getLogger("llama_agent.api")


# ============ MODELS ============


class ChatRequest(BaseModel):
    """Chat request model"""
    message: str = Field(..., min_length=1, max_length=10000, description="Chat message")
    user_id: Optional[str] = "default"
    context: Optional[str] = None
    conversation_id: Optional[str] = None


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
    password: str = Field(..., min_length=4, max_length=128)
    full_name: Optional[str] = Field(default=None, max_length=100)


class LoginRequest(BaseModel):
    """User login request"""
    username: str = Field(..., min_length=1, max_length=50)  # Can be username or email
    password: str = Field(..., min_length=1, max_length=128)


class TokenResponse(BaseModel):
    """Token response"""
    access_token: str
    refresh_token: str
    token_type: str = "bearer"


class RefreshRequest(BaseModel):
    """Refresh token request"""
    refresh_token: str


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

        db_url = os.getenv(
            "DATABASE_URL",
            "postgresql+asyncpg://postgres:postgres@localhost:5432/llama_agent",
        )
        sync_url = db_url.replace("postgresql+asyncpg://", "postgresql://")
        conn = psycopg2.connect(sync_url, connect_timeout=3)
        try:
            return db_health_check.run_health_check(conn)
        finally:
            conn.close()
    except ImportError:
        return {"status": "unavailable", "error": "psycopg2 not installed"}
    except Exception as e:
        return {"status": "unavailable", "error": str(e)}


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
    
    # Use OptimizedRAG (7 production-grade RAG features) when enabled via env var.
    # Falls back to SimpleRAG for backward compatibility / when ChromaDB is unavailable.
    use_optimized = os.environ.get("USE_OPTIMIZED_RAG", "").lower() in ("true", "1", "yes")
    if use_optimized:
        try:
            from modules.rag_optimizations import OptimizedRAG
            from modules.vector_store import get_vector_store
            vector_store = get_vector_store()
            app.state.rag = OptimizedRAG(vector_store)
            print("✅ OptimizedRAG initialized (Hybrid Search, Cross-Encoder, Contextual Retrieval, Query Decomposition, CRAG, Conversational Retrieval, Semantic Cache)")
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
    # Real agent-team usage tracking (populated by chat dispatches and MCP
    # tool calls below). Reset on restart, like the other in-memory stats.
    app.state.agent_stats = {"handled": {}, "last_used": {}}
    # Per-conversation anchor for document follow-ups: conversation_id ->
    # the last message that asked about an uploaded document. Used to route
    # "what about X?" / "and the Y column?" follow-ups back to the RAG
    # answerer instead of general chat. In-memory like agent_stats.
    app.state.doc_followup = {}
    
    # Re-index previously uploaded files so RAG knowledge survives restarts
    # (SimpleRAG keeps its index in memory, but files persist on disk)
    try:
        uploads_dir = "uploads"
        if os.path.isdir(uploads_dir):
            for fname in sorted(os.listdir(uploads_dir)):
                fpath = os.path.join(uploads_dir, fname)
                if os.path.isfile(fpath):
                    try:
                        result = app.state.rag.add_document(fpath)
                        print(f"📄 Re-indexed uploaded file: {fname} ({result})")
                    except Exception as e:
                        print(f"⚠️  Failed to re-index {fname}: {e}")
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
    triggered = alert_manager.check_alerts(business_metrics.get_metrics())
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
    version="3.0.0",
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
        "http://localhost:8501",    # Streamlit direct
        "http://localhost:3000",    # Next.js frontend
        "http://localhost:3001",    # Next.js frontend (alt)
        # Add your production domain here:
        # "https://yourdomain.com",
    ]
else:
    # DEVELOPMENT: Allow all (safe for local testing only)
    cors_origins = ["*"]

app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_origins,
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
        "version": "3.0.0",
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


@app.get("/health", response_model=HealthResponse)
async def health_check():
    """Health check endpoint"""
    monitor = app.state.monitor
    health = monitor.get_health()
    
    return HealthResponse(
        status=health["status"],
        uptime=health["uptime_human"],
        version="3.0.0",
        modules={
            "memory": "active",
            "rag": "active",
            "monitoring": "active",
            "database": "active" if get_config().features.enable_auth else "disabled",
            "websocket": "active" if get_config().features.enable_websockets else "disabled",
        }
    )


# ============ AUTH ENDPOINTS ============


@app.post("/auth/register", response_model=TokenResponse)
async def register(request: Request, body: RegisterRequest):
    """Register a new user"""
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
        return TokenResponse(
            access_token=result["access_token"],
            refresh_token=result["refresh_token"],
        )
    except HTTPException as e:
        # Enterprise: audit registration failure
        audit_logger.log_registration(body.username, client_ip, success=False, reason=str(e.detail))
        raise
    except Exception as e:
        audit_logger.log_registration(body.username, client_ip, success=False, reason=str(e))
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/auth/login", response_model=TokenResponse)
async def login(request: Request, body: LoginRequest):
    """Login with username/email and password"""
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
        return TokenResponse(
            access_token=result["access_token"],
            refresh_token=result["refresh_token"],
        )
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
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/auth/refresh", response_model=TokenResponse)
async def refresh_token(request: RefreshRequest):
    """Get new access token using refresh token"""
    try:
        result = await user_manager.refresh_token(request.refresh_token)
        return TokenResponse(
            access_token=result["access_token"],
            refresh_token=request.refresh_token,  # Keep same refresh token
        )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/auth/me")
async def get_me(user = Depends(get_current_user)):
    """Get current user info"""
    return {"user": user}


# ============ WEBSOCKET ENDPOINT ============


@app.websocket("/ws/{user_id}")
async def websocket_endpoint(
    websocket: WebSocket,
    user_id: str,
    token: str = "",
):
    """
    WebSocket endpoint for real-time chat

    Connect: ws://localhost:8000/ws/{user_id}?token=<JWT>

    Enterprise auth (frontend already sends `token`):
    - No token / empty token  -> anonymous connection (backwards compatible)
    - Valid access token      -> connected under the caller's REAL identity
                                 (username from the JWT, user_id from the JWT;
                                 path user_id is NOT trusted)
    - Invalid/expired token   -> connection rejected (HTTP 403 / WS 1008)

    Messages (JSON):
    - Send: {"type": "chat", "data": {"content": "Hello!"}}
    - Receive: {"type": "response", "data": {"content": "Hi there!"}}
    """
    username = "Anonymous"
    if token:
        user = await authenticate_ws_token(token)
        if not user:
            # Fail closed: never accept a spoofed identity or bad token
            await websocket.close(code=1008, reason="Invalid or expired token")
            return
        user_id = str(user["id"])
        username = user["username"]

    handler = get_ws_handler()
    await handler.handle_connection(websocket, user_id, username)


@app.get("/ws/status")
async def websocket_status():
    """Get WebSocket connection status"""
    handler = get_ws_handler()
    return {
        "online_users": handler.manager.get_online_users(),
        "total_connections": handler.manager.connection_count,
        "total_users": handler.manager.user_count,
    }


# ============ CHAT ENDPOINTS ============

# Name memory: users can tell the agent their name in plain chat
# ("my name is X" / "call me X" / "i am X") and later ask it back
# ("what is my name?"). The name is stored as a fact under the key
# "user's name" - deliberately NOT a short key like "name", so the
# generic substring fact check below cannot false-positive on questions
# like "what is the name of the capital of France?".

_NAME_TOKEN = r"[a-z][a-z0-9'.-]*"
_NAME_EXTRACT_PATTERNS = [
    # Strong signals - accept whatever name-like phrase follows.
    re.compile(r"my name is\s+(" + _NAME_TOKEN + r"(?:\s+" + _NAME_TOKEN + r"){0,3})", re.IGNORECASE),
    re.compile(r"call me\s+(" + _NAME_TOKEN + r"(?:\s+" + _NAME_TOKEN + r"){0,3})", re.IGNORECASE),
    re.compile(r"i am called\s+(" + _NAME_TOKEN + r"(?:\s+" + _NAME_TOKEN + r"){0,3})", re.IGNORECASE),
    # Weak signals - "i am X" / "i'm X" can catch verbs ("i am happy"),
    # so these are vetoed against the stopword set below.
    re.compile(r"\bi am\s+(" + _NAME_TOKEN + r"(?:\s+" + _NAME_TOKEN + r")?)", re.IGNORECASE),
    re.compile(r"\bi'm\s+(" + _NAME_TOKEN + r"(?:\s+" + _NAME_TOKEN + r")?)", re.IGNORECASE),
]

_NAME_STOPWORDS = {
    "asking", "wondering", "trying", "hoping", "planning", "working",
    "using", "building", "testing", "running", "starting", "checking",
    "looking", "interested", "learning", "going", "gonna", "wanting",
    "willing", "ready", "here", "back", "sorry", "happy", "fine", "good",
    "great", "new", "not", "just", "a", "an", "the", "my", "your", "his",
    "her", "this", "that", "these", "those", "in", "on", "at", "from",
    "with", "for", "to", "of", "and", "or", "but", "me", "we", "you",
    "they", "it", "am", "is", "are", "was", "were", "be", "have", "has",
    "had", "do", "does", "did", "very", "really", "actually", "also",
    "too", "now", "today", "ok", "okay", "sure", "please", "thanks",
    "need", "wish", "want", "know", "someone", "something",
}

_NAME_QUESTION_PATTERNS = [
    re.compile(r"what.{0,8}(?:is|'s)\s+my name", re.IGNORECASE),
    re.compile(r"what'?s\s+my name", re.IGNORECASE),
    re.compile(r"do you (?:know|remember) my name", re.IGNORECASE),
    re.compile(r"what do you call me", re.IGNORECASE),
    re.compile(r"(?:tell|remind).{0,12}my name", re.IGNORECASE),
    re.compile(r"who am i", re.IGNORECASE),
]

# Patterns for the remember command: "my name is X" and "X is Y"
_NAME_PATTERN = re.compile(r"my name is\s+(" + _NAME_TOKEN + r"(?:\s+" + _NAME_TOKEN + r"){0,3})", re.IGNORECASE)
_IS_PATTERN = re.compile(r"(.{5,}?)\s+is\s+(.+)", re.IGNORECASE)


def extract_name_from_message(message: str) -> Optional[str]:
    """Return a name if the message introduces one, otherwise None."""
    for i, pattern in enumerate(_NAME_EXTRACT_PATTERNS):
        match = pattern.search(message)
        if not match:
            continue
        tokens = [t for t in match.group(1).split()]
        # Trim trailing stopwords: "call me Mary Jane please" -> "Mary Jane"
        while tokens and tokens[-1].lower() in _NAME_STOPWORDS:
            tokens.pop()
        if not tokens:
            continue
        # First token must not be a stopword: "my name is the best" -> reject
        if tokens[0].lower() in _NAME_STOPWORDS:
            continue
        if i >= 3:  # weak "i am X" / "i'm X" patterns - veto any stopword
            if any(t.lower() in _NAME_STOPWORDS for t in tokens):
                continue
        name = " ".join(tokens).strip(".,!?;:()").strip()
        return name[:50]
    return None


def is_name_question(message: str) -> bool:
    """True if the message asks the agent what the user's name is."""
    return any(p.search(message) for p in _NAME_QUESTION_PATTERNS)


# Agent prefix routing: "@AgentType rest of message" explicitly routes
# to a specific agent. Supported prefixes (case-insensitive):
#   @Research Agent, @Coder Agent, @Writer Agent, @Planner Agent, @Analyzer Agent
_AGENT_PREFIX_MAP = {
    "research": "research",
    "coder": "coder",
    "writer": "writer",
    "planner": "planner",
    "analyzer": "analyzer",
}
_AGENT_PREFIX_RE = re.compile(
    r"^@\s*(" + "|".join(re.escape(k) for k in _AGENT_PREFIX_MAP) + r")\s+agent\s+(.*)",
    re.IGNORECASE,
)


def detect_agent_prefix(message: str) -> tuple:
    """If the message starts with @AgentType Agent, return
    (agent_type, stripped_message). Otherwise return (None, message).

    '@Research Agent tell me the latest news' → ('research', 'tell me the latest news')
    '@Coder Agent write a function' → ('coder', 'write a function')
    """
    m = _AGENT_PREFIX_RE.match(message.strip())
    if m:
        return _AGENT_PREFIX_MAP[m.group(1).lower()], m.group(2)
    return None, message


# ---------- shared multi-agent routing keywords (tight, intent-based) ---------
# Only clearly specialized requests are sent to the multi-agent system. Loose
# words like "what is", "how does", "python", "api" etc. are NOT enough — those
# go to the normal agent, which gives clean, concise answers.
CODE_KEYWORDS = ["write code", "write a function", "write python", "write a script",
                 "code to ", "code for ", "debug", "fix bug", "code review",
                 "refactor", "implement", "program to", "create a function",
                 "write a class", "build a script"]
WRITER_KEYWORDS = ["write an article", "write a blog", "write content",
                   "write an essay", "write a story", "creative writing",
                   "copywriting", "write a report", "write documentation",
                   "write a post", "write a letter"]
PLANNER_KEYWORDS = ["create a plan", "make a plan", "project plan", "action plan",
                    "roadmap", "strategy", "milestones", "timeline"]
ANALYZER_KEYWORDS = ["analyze", "analysis", "trends", "data analysis",
                     "evaluate", "assess", "insights"]
RESEARCH_KEYWORDS = ["research", "compare", "difference between",
                     "pros and cons", "investigate"]
# Requests about CURRENT/FRESH information. These must reach the research
# agent (which can call the web_search tool) instead of the general ReAct
# agent, which answers from static knowledge and would return stale data.
# Intent-based only - no message text is hardcoded.
RECENCY_KEYWORDS = [
    "latest", "recent", "breaking", "news", "headline", "headlines",
    "current events", "what's new", "what is new", "update on",
    "this week", "this month", "this year", "today's", "today's news",
    "news today", "just happened", "announced", "released today",
]


def route_multi_agent(message: str):
    """Return (use_multi_agent, agent_type) for a message.

    Checks @Agent prefix first (e.g. '@Research Agent ...'), then
    keyword-based routing, then falls back to the general ReAct agent.
    """
    task_lower = message.lower()

    # @Agent prefix: explicit routing
    agent_type, _ = detect_agent_prefix(message)
    if agent_type is not None:
        return True, agent_type

    if any(kw in task_lower for kw in CODE_KEYWORDS):
        return True, "coder"
    if any(kw in task_lower for kw in WRITER_KEYWORDS):
        return True, "writer"
    if any(kw in task_lower for kw in PLANNER_KEYWORDS):
        return True, "planner"
    if any(kw in task_lower for kw in ANALYZER_KEYWORDS):
        return True, "analyzer"
    if any(kw in task_lower for kw in RESEARCH_KEYWORDS):
        return True, "research"
    if any(kw in task_lower for kw in RECENCY_KEYWORDS):
        return True, "research"
    return False, "research"


# Agent team (static definitions - names match multi_agent.py + the frontend)
AGENT_KEYS = ["research", "coder", "writer", "analyzer", "planner"]
# MCP tool name -> agent key(s) it drives. "collaborate" runs every agent.
_MCP_TOOL_TO_AGENT = {
    "research_agent": ["research"],
    "coder_agent": ["coder"],
    "writer_agent": ["writer"],
    "analyzer_agent": ["analyzer"],
    "planner_agent": ["planner"],
    "collaborate": AGENT_KEYS,
}


def _record_agent_usage(agent_keys) -> None:
    """Increment real task counters + last-used timestamps for agent keys."""
    stats = getattr(app.state, "agent_stats", None)
    if stats is None:
        return
    handled = stats.setdefault("handled", {})
    last_used = stats.setdefault("last_used", {})
    # UTC-aware so the frontend can compute relative times correctly
    now = datetime.now(timezone.utc).isoformat()
    for key in agent_keys:
        if key not in AGENT_KEYS:
            continue
        handled[key] = handled.get(key, 0) + 1
        last_used[key] = now


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
        recent = msgs[-5:]
        if not recent:
            context = "No conversation history yet."
        else:
            context = "Recent conversation history:\n" + "\n".join(
                f"{'User' if m['role'] == 'user' else 'Assistant'}: {m['content'][:200]}"
                for m in recent
            )
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


_DOC_QUESTION_PHRASES = [
    "summarize the uploaded", "summarize uploaded", "summary of the uploaded",
    "summarize the file", "summarize the document", "summarize file",
    "sumarize the uploaded", "sumarize uploaded", "sumarize the file",
    "sumarize the document", "sumarize file",
    "about the uploaded", "about the document", "about the file",
    "from the document", "from the file", "in the document", "in the file",
    "from the uploaded", "document says", "file says", "uploaded file",
    "uploaded document", "what does the file", "what does the document",
    "tell me about the file", "tell me about the document",
]


# Questions asking WHAT documents/files are uploaded (an inventory question)
# rather than about the CONTENT of one specific file. These route to RAG so
# the answer lists the user's actual uploads instead of a generic agent guess.
_DOC_INVENTORY_PHRASES = (
    "what documents", "which documents", "what files", "which files",
    "list my documents", "list the documents", "list documents",
    "list my files", "list the files", "list files",
    "show my documents", "show the documents", "show documents",
    "show my files", "show the files", "show files",
    "uploaded documents", "uploaded files",
    "what have i uploaded", "what did i upload",
    "documents have i uploaded", "files have i uploaded",
    "how many documents", "how many files",
    "what is uploaded", "what are the documents", "what are the files",
    "what documents are", "what files are", "see my documents", "see my files",
)


def _is_document_inventory_question(message: str) -> bool:
    """True if the message asks to LIST the user's uploaded documents/files
    (e.g. "what documents have I uploaded?") instead of asking about the
    content of one specific file."""
    m = (message or "").lower()
    return any(p in m for p in _DOC_INVENTORY_PHRASES)


def is_document_question(message: str, rag=None) -> bool:
    """True if the user is asking about their uploaded documents/files.

    When a RAG store is supplied, a multi-document comparison ("compare X
    and Y", both uploaded files) also counts as a document question so it
    routes to the RAG answerer instead of a generic agent.
    """
    m = message.lower()
    if any(p in m for p in _DOC_QUESTION_PHRASES):
        return True
    if _is_document_inventory_question(m):
        return True
    # Generic pattern: a document-intent phrase + a reference to a file.
    # Catches forms the exact phrase list misses, e.g. "summarize the CRM
    # file" or "what is the Captainassistant.py file?".
    if (
        any(w in m for w in _DOC_QUESTION_REF_WORDS)
        and any(i in m for i in _DOC_QUESTION_INTENTS)
    ):
        return True
    if rag is not None and _detect_compare_documents(message, rag)[0]:
        return True
    return False


# Words that point at an uploaded file/document.
_DOC_QUESTION_REF_WORDS = (
    "file", "document", "uploaded", "upload", "doc", "docs",
    ".txt", ".csv", ".docx", ".pdf", ".py", ".md", ".json",
)

# Phrases that signal the user wants information ABOUT that file.
_DOC_QUESTION_INTENTS = (
    "summarize", "summary", "summarise", "sumarize", "describe",
    "what is", "what's", "tell me about", "explain", "show me",
    "read", "give me", "info about", "information about",
)


# Phrases that indicate a message continues a previous document conversation
# (e.g. "what about the pricing column?" right after "summarize the CRM
# file"). The check additionally requires that a document question was asked
# before in THIS conversation, otherwise nothing routes here.
_DOC_FOLLOWUP_PHRASES = [
    "what about", "how about", "and the", "and what", "tell me more",
    "more about", "what else", "anything else", "explain", "regarding",
    "about that", "that file", "this document", "the document", "the file",
    "does it", "what does it", "it says", "in it", "in the doc",
    "from the doc", "in that document", "show me", "can you show",
    "any details",
]


def is_doc_follow_up(message: str, last_doc_query: str) -> bool:
    """True if `message` continues a previous document conversation.

    Pure helper (testable offline): `last_doc_query` is the last message in
    this conversation that asked about an uploaded document ("" if none).
    """
    if not message or not last_doc_query:
        return False
    m = message.lower()
    return any(p in m for p in _DOC_FOLLOWUP_PHRASES)


def _get_doc_followup() -> Dict[str, str]:
    """Lazily-created per-conversation doc-follow-up anchors."""
    anchors = getattr(app.state, "doc_followup", None)
    if anchors is None:
        anchors = {}
        app.state.doc_followup = anchors
    return anchors


def _set_doc_followup(conversation_id: str, message: str) -> None:
    """Remember that this conversation last asked a document question."""
    _get_doc_followup()[conversation_id] = message


def _doc_followup_query(conversation_id: str, message: str) -> str:
    """RAG query for a follow-up message, or "" if it is not a follow-up.

    Combines the original document question with the new message so the
    retriever still finds the right chunks ("summarize the CRM file
    what about the pricing column?").
    """
    last = _get_doc_followup().get(conversation_id, "")
    if is_doc_follow_up(message, last):
        return f"{last} {message}".strip()
    return ""


# Answer shapes that mean "the retrieval did not actually answer the
# question". When a first-pass document answer looks like one of these, the
# question is reformulated and the documents are queried once more.
_THIN_ANSWER_PHRASES = (
    "doesn't contain", "does not contain", "doesn't mention", "does not mention",
    "doesn't include", "does not include", "don't have", "do not have",
    "not found", "no information", "cannot find", "can't find", "could not find",
    "couldn't find", "not available in", "not in the document", "not in the excerpts",
    "not covered", "doesn't say", "does not say", "no mention", "no details",
    "no such", "unable to find", "not included in",
)


def is_thin_answer(text: str, min_chars: int = 60) -> bool:
    """True if a document answer is too thin to trust (no real content).

    Pure helper (testable offline). An answer is thin when it is missing,
    extremely short, or explicitly says the documents don't contain the
    information.
    """
    if not text:
        return True
    lowered = text.lower()
    if len(lowered.strip()) < min_chars:
        return True
    return any(p in lowered for p in _THIN_ANSWER_PHRASES)


def _complete_with_fallback(client, messages, max_tokens):
    """Run a chat completion on the STRONG tier; on rate-limit/availability
    errors retry once on the FAST tier so strong-model quota limits never
    break one-shot answers."""
    model = resolve_model("strong")
    try:
        return client.chat.completions.create(
            model=model, messages=messages, max_tokens=max_tokens
        )
    except Exception as e:
        if should_fallback(e, "strong"):
            return client.chat.completions.create(
                model=resolve_model("fast"), messages=messages, max_tokens=max_tokens
            )
        raise


def _reformulate_doc_query(query: str, context: str, client) -> str:
    """Ask the fast model to rephrase a document question into a more
    specific search query for the second retrieval pass. Returns "" if no
    usable reformulation is produced (then the second pass is skipped)."""
    prompt = (
        "You are helping search the user's uploaded documents.\n\n"
        f"Document excerpts:\n{context[:3000]}\n\n"
        f"The user asked: {query}\n\n"
        "The first retrieval did not answer this well. Rephrase the user's "
        "question into ONE more specific search query that would match the "
        "details they are after (include the exact topic terms from the "
        "question). Reply with only the query text, nothing else."
    )
    try:
        resp = client.chat.completions.create(
            model=resolve_model("fast"),
            messages=[
                {"role": "system", "content": "You reformulate search queries. Reply with one query only."},
                {"role": "user", "content": prompt},
            ],
            max_tokens=120,
        )
        q = (resp.choices[0].message.content or "").strip()
    except Exception:
        return ""
    # Keep only plausible queries (a bare "the" is useless).
    if len(q) < 6:
        return ""
    # Strip a leading bullet/number/dash/quote if the model added one.
    q = q.lstrip("-*•0123456789).\"'").strip()
    return q


def _ask_doc_question(client, query: str, context: str) -> str:
    """One LLM answer about uploaded documents from the retrieved context."""
    prompt = (
        "You are answering a question about the user's uploaded documents.\n\n"
        f"Document excerpts:\n{context}\n\n"
        f"Question: {query}\n\n"
        "First, in one short line, state exactly what this document IS: its "
        "type (e.g. Python application, CSV dataset, Word/Markdown/PDF "
        "document), its purpose, and the main technology or content it "
        "involves. Use the file names and excerpt content shown above - do "
        "not guess beyond what the excerpts show.\n"
        "Then answer the question using ONLY the excerpts above. Cite the "
        "source for EVERY claim by appending the exact file name and chunk "
        "from the 'Source:' labels in parentheses, e.g. (CRM-3.docx, chunk "
        "2) - every sentence that makes a factual statement must end with "
        "such a citation. Never cite a file that is not in the excerpts, "
        "and never invent claims the excerpts do not support. Be concise "
        "and clear, using short paragraphs or bullets where helpful. Never "
        "dump raw excerpts. If the excerpts don't contain the answer, say "
        "so plainly."
    )
    try:
        resp = _complete_with_fallback(
            client,
            [
                {"role": "system", "content": "You are a helpful assistant that answers questions about uploaded documents."},
                {"role": "user", "content": prompt},
            ],
            max_tokens=1024,
        )
        return resp.choices[0].message.content
    except Exception as e:
        return f"Could not summarize the documents right now: {e}"


def _doc_match_score(query_lower: str, path: str) -> int:
    """How strongly a query references an uploaded document path.

    Direct references (the full path or its file name appears in the query)
    score 10; additionally, each query token that appears in the file name's
    stem scores 1 (e.g. "crm" matches "CRM-3.docx", "sample" matches
    "sample.txt"). Pure helper (testable offline).
    """
    base = os.path.basename(path).lower()
    stem = os.path.splitext(base)[0]
    score = 0
    if path.lower() in query_lower or base in query_lower:
        score += 10
    stem_tokens = set(re.findall(r"[a-z0-9]+", stem))
    query_tokens = set(re.findall(r"[a-z0-9]+", query_lower))
    score += len(stem_tokens & query_tokens)
    return score


# Explicit comparison signals for multi-document questions.
_COMPARE_SIGNALS = (
    "compare", "comparison", "versus", " vs ", "vs.", "contrast",
    "difference between", "differences between",
)


def _detect_compare_documents(query: str, rag) -> tuple:
    """Detect a request to compare two uploaded documents.

    Returns (True, [doc1, doc2]) with resolved store paths, or (False, []).

    Works for ANY pair of uploaded files (nothing is hardcoded): it only
    inspects the query text and the store's document list. Pure helper
    (testable offline).
    """
    m = (query or "").lower()
    if not any(sig in m for sig in _COMPARE_SIGNALS):
        return False, []
    available = rag.list_documents()
    if len(available) < 2:
        return False, []
    scored = [(path, _doc_match_score(m, path)) for path in available]
    scored = [(p, s) for p, s in scored if s > 0]
    scored.sort(key=lambda x: -x[1])
    if len(scored) < 2:
        return False, []
    return True, [p for p, _s in scored[:2]]


# Words inside a comparison request that carry no retrieval signal. The
# file names being compared are stripped separately in _distill_compare_query;
# this set covers the comparison framing words themselves (stopwords are
# handled by the RAG store's own tokenizer).
_COMPARE_NOISE_WORDS = frozenset(
    "compare comparison contrast versus vs differences difference between "
    "doc docs document documents file files both each says say tell what "
    "about and the of in for on with their they".split()
)


def _distill_compare_query(query: str, files) -> str:
    """Extract the retrieval TOPIC from a comparison request.

    Drops the file names being compared and the comparison framing words, so
    per-document retrieval targets the actual subject instead of matching
    generic words:

    'Compare what the CRM doc and sample.txt say about pricing'
        -> 'pricing'
    'differences between the budget and the sales forecast' (files
    financials.xlsx / other.xlsx) -> 'budget sales forecast'

    Pure helper (testable offline).
    """
    m = (query or "").lower()
    drop = set()
    for p in files or []:
        base = os.path.basename(p).lower()
        stem = os.path.splitext(base)[0]
        drop.add(base)
        drop.add(stem)
        # Each alphanumeric piece of the stem too (e.g. "crm-3" -> "crm").
        drop.update(re.findall(r"[a-z0-9]+", stem))
        # The file extension token (txt/csv/pdf/docx) is a generic type tag.
        ext = os.path.splitext(base)[1].lstrip(".")
        if ext:
            drop.add(ext)
    toks = [
        t for t in re.findall(r"[a-z0-9]+", m)
        if t not in drop and t not in _COMPARE_NOISE_WORDS
    ]
    return " ".join(toks)


def _compare_documents(query: str, files, rag, client) -> str:
    """Answer a multi-document comparison using per-document labeled context."""
    parts = []
    # Retrieve each document on the TOPIC (file names + comparison framing
    # stripped) so retrieval is not diluted by the request's own words.
    topic = _distill_compare_query(query, files)
    for f in files:
        ctx = rag.get_context_for_document(topic or query, f, top_k=4)
        if ctx:
            parts.append(ctx)
    if not parts:
        return "I could not find the documents to compare. Upload them on the Documents page first."
    combined = "\n\n".join(parts)
    prompt = (
        "You are comparing two uploaded documents.\n\n"
        f"Document excerpts (each labeled with its exact source file and "
        f"chunk number):\n{combined}\n\n"
        f"Comparison request: {query}\n\n"
        "Answer in this structure:\n"
        "1. One line about what each document IS (type, purpose) - label "
        "each with its exact file name from the 'Source:' labels.\n"
        "2. For EACH document, a short subsection headed by its exact file "
        "name, stating what THAT document says about the topic. Quote the "
        "relevant text word-for-word from the excerpt, then cite the exact "
        "chunk number in parentheses, e.g. (CRM-3.docx, chunk 2).\n"
        "3. Key similarities and differences, each cited the same way.\n"
        "CRITICAL: a (file, chunk) citation may ONLY be attached to a claim "
        "taken word-for-word from that exact labeled excerpt. Never attach "
        "one document's chunk to a claim about the other document - if a "
        "document does not discuss the topic, say so plainly instead of "
        "guessing. Never invent facts, file names, or chunk numbers.\n"
        "Use ONLY the excerpts above, and never dump raw excerpts into the "
        "answer."
    )
    try:
        resp = _complete_with_fallback(
            client,
            [
                {"role": "system", "content": "You are a careful document analyst."},
                {"role": "user", "content": prompt},
            ],
            max_tokens=1200,
        )
        return resp.choices[0].message.content
    except Exception as e:
        return f"Could not compare the documents right now: {e}"


def answer_from_documents(query: str, rag) -> str:
    """Answer a question about uploaded documents with a CLEAN, LLM-written
    answer (instead of dumping raw chunk JSON / excerpts).

    Handles multi-document comparisons ("compare X and Y") with per-document
    retrieval, then a two-pass retrieval for regular questions: if the first
    answer is "thin" (too short, or the model says the documents don't
    contain the information), the question is reformulated into a more
    specific query and the documents are searched once more before giving up.
    """
    # Inventory question ("what documents have I uploaded?") -> list the
    # actual uploads straight from the index; no retrieval needed.
    if _is_document_inventory_question(query):
        try:
            files = sorted(set(rag.list_documents()))
        except Exception:
            files = []
        if not files:
            return ("No documents have been uploaded yet. "
                    "Upload a file on the Documents page first, then ask me about it.")
        listing = "\n".join(f"- {os.path.basename(f) or f}" for f in files)
        label = "document" if len(files) == 1 else "documents"
        return f"You have {len(files)} uploaded {label}:\n{listing}"

    # Semantic Cache: serve a cached answer if one exists (skips the LLM call)
    cached = rag.get_cached_answer(query)
    if cached:
        return cached

    try:
        context = rag.get_context(query)
    except Exception:
        context = ""
    if not context or context == "No documents have been uploaded.":
        return "No documents have been uploaded yet. Upload a file on the Documents page first, then ask me about it."
    if "No documents" in context:
        return "No documents have been uploaded yet. Upload a file on the Documents page first, then ask me about it."

    from multi_agent import InstrumentedGroqClient
    from modules.secrets import get_secret
    api_key = os.environ.get("GROQ_API_KEY", "") or get_secret("GROQ_API_KEY", default="")
    if not api_key:
        return context
    client = InstrumentedGroqClient(api_key=api_key)

    # Multi-document comparison: retrieve per document and answer with a
    # comparison instead of a single mixed context.
    is_compare, compare_files = _detect_compare_documents(query, rag)
    if is_compare:
        return _compare_documents(query, compare_files, rag, client)

    first = _ask_doc_question(client, query, context)

    # Two-pass RAG: only retry when the first answer is actually thin.
    if is_thin_answer(first):
        reformulated = _reformulate_doc_query(query, context, client)
        if reformulated and reformulated.lower() != query.strip().lower():
            try:
                context2 = rag.get_context(reformulated)
            except Exception:
                context2 = ""
            if context2 and context2 != context and "No documents" not in context2:
                # Combine both retrievals (skip if the second is just a
                # substring of the first - i.e. nothing new was found).
                combined = context if context2 in context else context + "\n\n" + context2
                answer = _ask_doc_question(client, reformulated, combined)
                rag.cache_answer(query, answer)
                return answer

    rag.cache_answer(query, first)
    return first



@app.post("/chat", response_model=ChatResponse)
async def chat(
    request: ChatRequest,
    http_request: Request,
    user = Depends(optional_auth),  # Optional auth (works with or without login)
):
    """Chat with the agent. Auth optional in development mode."""
    start_time = datetime.now()
    monitor = app.state.monitor
    memory = app.state.memory
    facts = app.state.facts
    rag = app.state.rag
    logger = app.state.logger
    user_id = str(user["id"]) if user else "guest"
    # Per-user default conversation: two users must never share "default".
    # The storage layer also refuses to rebind a conversation to another user.
    conversation_id = request.conversation_id or _default_conversation_id(user_id)
    
    monitor.track_request()
    query_type = "general"
    # Name introduced in plain chat? ("my name is X" / "call me X" / "i am X")
    detected_name = extract_name_from_message(request.message)
    
    # Enterprise security: detect dangerous input patterns (sanitize + audit).
    # The sanitizer is used as a detection layer - if it alters the message,
    # we flag it for the audit log and continue with the original so the
    # agent's capabilities are not degraded.
    sanitized_message = sanitize_input(request.message)
    if sanitized_message != request.message:
        audit_logger.log_suspicious_activity(
            "unsanitized_input",
            get_client_ip(http_request),
            f"chat message contained potentially dangerous content",
        )
    
    try:
        # Check for special commands
        if request.message.lower().startswith("remember "):
            content = request.message[9:].strip()
            # Support "my name is X" format
            name_match = _NAME_PATTERN.search(content)
            if name_match:
                facts.remember("user's name", name_match.group(1))
                response = f"✅ Remembered: your name is {name_match.group(1)}"
            elif "=" in content:
                key, value = content.split("=", 1)
                facts.remember(key.strip(), value.strip())
                response = f"✅ Remembered: {key.strip()} = {value.strip()}"
            else:
                # Support "X is Y" format (e.g. "favorite color is blue")
                is_match = _IS_PATTERN.search(content)
                if is_match:
                    facts.remember(is_match.group(1).strip(), is_match.group(2).strip())
                    response = f"✅ Remembered: {is_match.group(1).strip()} = {is_match.group(2).strip()}"
                else:
                    response = "Usage: remember key = value, or say 'my name is X', or 'X is Y'."
        
        elif request.message.lower().startswith("recall "):
            key = request.message[7:].strip()
            # "recall my name" → look up "user's name"
            if _NAME_PATTERN.search(key) or key.lower() in ("my name", "my name please"):
                key = "user's name"
            value = facts.recall(key)
            if value:
                response = f"✅ {key}: {value}"
            else:
                response = f"I don't remember anything about '{key}'."
        
        elif request.message.lower() == "show facts":
            all_facts = facts.get_all_facts()
            if all_facts:
                response = "\n".join(f"- {k}: {v}" for k, v in all_facts.items())
            else:
                response = "No facts stored."
        
        elif request.message.lower().startswith("search "):
            query = request.message[7:].strip()
            response = rag.get_context(query)
        
        elif detected_name:
            # Name memory: "my name is X" stores the user's name as a fact so
            # it survives across conversations and can be recalled later.
            facts.remember("user's name", detected_name)
            response = f"Nice to meet you, **{detected_name}**! I've remembered your name."
            query_type = "name_introduction"
        
        elif is_name_question(request.message):
            # "what is my name?" recalls the stored name. MUST run before the
            # multi-agent routing in the else-branch below, which would
            # otherwise treat "what is my name?" (matches "what is") as a
            # research task and answer with a generic essay instead.
            stored_name = facts.recall("user's name")
            if "don't remember" in stored_name.lower():
                response = "I don't know your name yet. Tell me by saying something like \"My name is ...\""
            else:
                response = f"Your name is **{stored_name}**!"
            query_type = "name_question"
        
        else:
            # Check if user is asking about a stored fact
            all_facts = facts.get_all_facts()
            lower_input = request.message.lower()
            
            fact_answered = False
            for key, value in all_facts.items():
                key_lower = key.lower()
                if (key_lower in lower_input and 
                    any(phrase in lower_input for phrase in ["what", "know", "recall", "tell", "remind"])):
                    response = f"Your {key} is **{value}**."
                    fact_answered = True
                    break
            
            if not fact_answered:
                # Simple greeting handler - bypass agent for simple messages
                greetings = ["hi", "hello", "hey", "hola", "good morning", "good afternoon", "good evening", "howdy", "yo", "sup"]
                if is_document_question(request.message, rag):
                    # Clean answers about uploaded documents (RAG retrieval +
                    # LLM summary) instead of dumping raw chunks.
                    response = answer_from_documents(request.message, rag)
                    query_type = "document_question"
                    # Anchor this conversation so later messages like
                    # "what about the pricing column?" route back here.
                    _set_doc_followup(conversation_id, request.message)
                else:
                    # Doc-aware follow-up: message continues an earlier
                    # document question in this conversation.
                    followup_query = _doc_followup_query(conversation_id, request.message)
                    if followup_query:
                        response = answer_from_documents(followup_query, rag)
                        query_type = "document_question"
                    elif any(g in lower_input for g in greetings):
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
                            response = orchestrator.agents[agent_type].execute(task)
                            _record_agent_usage([agent_type])
                        else:
                            # Fall back to general ReAct agent (it injects this
                            # conversation's recent history + facts internally, so
                            # follow-ups about earlier messages work).
                            from llama_agent1 import run_agent
                            response = await run_agent(
                                request.message,
                                memory=memory,
                                facts=facts,
                                user_id=user_id,
                                conversation_id=conversation_id,
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
            user_id=request.user_id,
            success=True,
            response_time=elapsed,
            query_type=query_type,
        )
        sla_monitor.record_response_time(elapsed)
        alert_manager.check_alerts(business_metrics.get_metrics())
        
        return ChatResponse(
            response=str(response),
            timestamp=datetime.now().isoformat(),
            response_time=elapsed
        )
        
    except Exception as e:
        monitor.track_error()
        logger.log_error(e, context=f"User: {request.message}")
        # Enterprise monitoring: record the failure + SLA error
        elapsed = (datetime.now() - start_time).total_seconds()
        sla_monitor.record_error("agent_error", str(e))
        business_metrics.record_question(
            user_id=request.user_id,
            success=False,
            response_time=elapsed,
            query_type="error",
        )
        raise HTTPException(status_code=500, detail=str(e))


# ============ STREAMING CHAT (SSE) ============


@app.post("/chat/stream")
async def chat_stream(
    request: ChatRequest,
    user = Depends(optional_auth),
):
    """Chat with streaming response (Server-Sent Events)."""
    from llama_agent1 import run_agent_stream

    # Single unified memory/facts instances (shared with /chat and /memory),
    # so EVERY response - general agent, multi-agent, and special commands -
    # is saved to the same conversation history.
    memory = app.state.memory
    facts = app.state.facts
    rag = app.state.rag
    user_id = str(user["id"]) if user else "guest"
    # Per-user default conversation (never share the legacy "default" id).
    conversation_id = request.conversation_id or _default_conversation_id(user_id)

    async def event_generator():
        # Helper: record the assistant reply, emit it, then signal stream end.
        async def done(text: str):
            await _persist_message(user_id, conversation_id, "assistant", text)
            yield f"data: {escape_sse(text)}\n\n"
            yield "data: [DONE]\n\n"

        # Handle special commands first
        if request.message.lower().startswith("remember "):
            content = request.message[9:].strip()
            await _persist_message(user_id, conversation_id, "user", request.message)
            if "=" in content:
                key, value = content.split("=", 1)
                facts.remember(key.strip(), value.strip())
                reply = f"✅ Remembered: {key.strip()} = {value.strip()}"
            else:
                reply = "Usage: remember key = value"
            async for chunk in done(reply):
                yield chunk
            return

        elif request.message.lower().startswith("recall "):
            key = request.message[7:].strip()
            await _persist_message(user_id, conversation_id, "user", request.message)
            result = facts.recall(key)
            async for chunk in done(result):
                yield chunk
            return

        elif request.message.lower() == "show facts":
            await _persist_message(user_id, conversation_id, "user", request.message)
            all_facts = facts.get_all_facts()
            result = "\n".join(f"- {k}: {v}" for k, v in all_facts.items()) if all_facts else "No facts stored."
            async for chunk in done(result):
                yield chunk
            return

        elif request.message.lower().startswith("search "):
            query = request.message[7:].strip()
            await _persist_message(user_id, conversation_id, "user", request.message)
            result = rag.get_context(query)
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
            facts.remember("user's name", detected_name)
            reply = f"Nice to meet you, **{detected_name}**! I've remembered your name."
            async for chunk in done(reply):
                yield chunk
            return

        if is_name_question(request.message):
            await _persist_message(user_id, conversation_id, "user", request.message)
            stored_name = facts.recall("user's name")
            if "don't remember" in stored_name.lower():
                reply = "I don't know your name yet. Tell me by saying something like \"My name is ...\""
            else:
                reply = f"Your name is **{stored_name}**!"
            async for chunk in done(reply):
                yield chunk
            return

        # Check facts
        all_facts = facts.get_all_facts()
        lower_input = request.message.lower()
        for key, value in all_facts.items():
            key_lower = key.lower()
            if (key_lower in lower_input and
                any(phrase in lower_input for phrase in ["what", "know", "recall", "tell", "remind"])):
                await _persist_message(user_id, conversation_id, "user", request.message)
                reply = f"Your {key} is **{value}**."
                async for chunk in done(reply):
                    yield chunk
                return

        # Clean answers about uploaded documents (RAG + LLM summary)
        if is_document_question(request.message, rag):
            await _persist_message(user_id, conversation_id, "user", request.message)
            result = answer_from_documents(request.message, rag)
            # Anchor this conversation so later messages like
            # "what about the pricing column?" route back here.
            _set_doc_followup(conversation_id, request.message)
            async for chunk in done(result):
                yield chunk
            return

        # Doc-aware follow-up: message continues an earlier document
        # question in this conversation (e.g. "and the pricing column?").
        followup_query = _doc_followup_query(conversation_id, request.message)
        if followup_query:
            await _persist_message(user_id, conversation_id, "user", request.message)
            result = answer_from_documents(followup_query, rag)
            async for chunk in done(result):
                yield chunk
            return

        # Check if this is a specialized task for multi-agent
        from multi_agent import MultiAgentOrchestrator

        use_multi_agent, agent_type = route_multi_agent(request.message)

        if use_multi_agent:
            # Stream the agent-team run: tool activity first, then answer
            # tokens - the same experience as general chat, no blocking
            # spinner. Inject recent conversation context so follow-ups about
            # earlier messages work.
            await _persist_message(user_id, conversation_id, "user", request.message)
            orchestrator = MultiAgentOrchestrator()
            # Strip @Agent prefix so the agent sees the clean task.
            _, clean_message = detect_agent_prefix(request.message)
            task = await _with_context(clean_message, memory, user_id, conversation_id)
            agent = orchestrator.agents[agent_type]
            full = ""
            try:
                for event in agent.execute_stream(task):
                    if event.get("type") == "tool":
                        yield f"data: {escape_sse(event['content'])}\n\n"
                    else:
                        full += event["content"]
                        yield f"data: {escape_sse(event['content'])}\n\n"
            except Exception as e:
                full = f"⚠️ Error from {agent_type} agent: {e}"
                yield f"data: {escape_sse(full)}\n\n"
            await _persist_message(user_id, conversation_id, "assistant", full)
            yield "data: [DONE]\n\n"
        else:
            # Stream from general ReAct agent (it reads + writes the SAME memory
            # instances, so the response is saved to this user's conversation).
            full_response = ""
            for token in run_agent_stream(
                request.message,
                memory=memory,
                facts=facts,
                user_id=user_id,
                conversation_id=conversation_id,
            ):
                full_response += token
                yield f"data: {escape_sse(token)}\n\n"
            # The agent already wrote both messages to the JSON memory store;
            # mirror them to PostgreSQL (durable history the sidebar reads) and
            # refresh the Redis caches so the sidebar/conversation views agree.
            await _mirror_message(user_id, conversation_id, "user", request.message)
            await _mirror_message(user_id, conversation_id, "assistant", full_response)
            yield "data: [DONE]\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


def escape_sse(text: str) -> str:
    """Escape text for SSE (replace newlines)."""
    return text.replace("\n", "\\n")


# ============ FACTS ENDPOINTS ============


@app.post("/remember")
async def remember_fact(request: RememberRequest):
    """Remember a fact"""
    try:
        facts = app.state.facts
        facts.remember(request.key, request.value)
        return {"status": "success", "message": f"Remembered: {request.key} = {request.value}"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/recall")
async def recall_fact(request: RecallRequest):
    """Recall a fact"""
    try:
        facts = app.state.facts
        value = facts.recall(request.key)
        return {"key": request.key, "value": value}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/facts")
async def list_facts():
    """List all facts"""
    try:
        facts = app.state.facts
        all_facts = facts.get_all_facts()
        return {"facts": all_facts}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.delete("/facts/{key}")
async def delete_fact(key: str):
    """Delete a fact"""
    try:
        facts = app.state.facts
        facts.forget(key)
        return {"status": "success", "message": f"Deleted: {key}"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ============ DOCUMENTS ENDPOINTS ============


@app.post("/documents/read")
async def read_document(file_path: str):
    """Read a document (with path traversal protection)"""
    try:
        # SECURITY: Restrict reads to the project's data directories.
        # Arbitrary relative paths (e.g. ../../etc/passwd) are rejected —
        # the realpath containment check below is authoritative and also
        # neutralizes symlinks pointing outside the allowed roots.
        from modules.security import is_path_within_roots

        allowed_roots = ["uploads", "data", "documents"]
        if not is_path_within_roots(file_path, allowed_roots):
            raise HTTPException(
                status_code=400,
                detail="Invalid file path: only files under uploads/, data/, or documents/ are allowed",
            )

        # Check if file exists and is readable
        if not os.path.isfile(file_path):
            raise HTTPException(
                status_code=404,
                detail=f"File not found: {file_path}",
            )
        
        rag = app.state.rag
        result = rag.add_document(file_path)
        return {"status": "success", "message": result}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/documents/upload")
async def upload_document(file: UploadFile = File(...), user: Optional[Dict] = Depends(optional_auth)):
    """Upload and read a document (with security validation)"""
    try:
        # SECURITY: Validate file upload
        from modules.security import validate_file_upload, sanitize_filename, get_upload_path
        
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
        
        # Sanitize filename and get safe path
        upload_dir = "uploads"
        os.makedirs(upload_dir, exist_ok=True)
        
        safe_filename = sanitize_filename(file.filename or "uploaded_file")
        file_path = get_upload_path(upload_dir, safe_filename)
        
        # Save file
        with open(file_path, "wb") as f:
            f.write(content)
        
        # Read with RAG
        rag = app.state.rag
        result = rag.add_document(file_path)
        
        # Enterprise: audit success + business metrics
        audit_logger.log_file_upload(safe_filename, username, success=True)
        business_metrics.record_document_upload(username)
        business_metrics.record_feature_usage("document_upload")
        
        return {"status": "success", "message": result, "file": safe_filename}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/documents/search")
async def search_documents(request: SearchRequest):
    """Search documents (raw retrieval, for power users)"""
    try:
        rag = app.state.rag
        results = rag.search(request.query, top_k=request.top_k)
        return {"query": request.query, "results": results}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/documents/ask")
async def ask_documents(request: SearchRequest):
    """Ask a question about uploaded documents and get a clean, LLM-written
    answer (instead of raw chunk JSON)."""
    try:
        rag = app.state.rag
        answer = answer_from_documents(request.query, rag)
        return {"query": request.query, "answer": answer}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/documents")
async def list_documents():
    """List loaded documents"""
    try:
        rag = app.state.rag
        docs = rag.list_documents()
        return {"documents": docs}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ============ MEMORY ENDPOINTS ============


@app.get("/memory")
async def get_memory(user = Depends(optional_auth)):
    """Get conversation memory (scoped to the current user)"""
    try:
        memory = app.state.memory
        user_id = str(user["id"]) if user else "guest"
        recent = memory.get_recent(20, user_id=user_id)
        stats = memory.get_stats()
        conversations = await _list_conversations_for(user_id, memory)
        return {
            "stats": stats,
            "recent_messages": recent,
            "conversations": conversations,
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.delete("/memory")
async def clear_memory(user = Depends(optional_auth)):
    """Clear memory for the current user (conversations + facts)"""
    from modules import pg_store, redis_store
    try:
        memory = app.state.memory
        facts = app.state.facts
        user_id = str(user["id"]) if user else "guest"
        memory.clear(user_id=user_id)
        facts.clear()
        # Clear the durable store + caches too so the sidebar matches.
        await pg_store.clear_user(user_id)
        await redis_store.invalidate_user(user_id)
        return {"status": "success", "message": f"Memory cleared for {user_id}"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ============ CONVERSATION ENDPOINTS ============
# Per-user conversation history used by the Next.js chat-history sidebar.
# Chats are created/resumed by conversation_id, so each conversation in the
# sidebar maps 1:1 to a persisted conversation in the memory store.


@app.get("/conversations")
async def list_conversations(user = Depends(optional_auth)):
    """List the current user's conversations (newest first).

    Read path: Redis cache -> PostgreSQL (durable store) -> JSON memory store.
    Legacy memory conversations are merged in so pre-mirror history is shown.
    """
    try:
        memory = app.state.memory
        user_id = str(user["id"]) if user else "guest"
        convs = await _list_conversations_for(user_id, memory)
        return {"conversations": convs}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/conversations/{conversation_id}")
async def get_conversation(conversation_id: str, user = Depends(optional_auth)):
    """Get one conversation including its full message history.

    Read path: Redis cache -> PostgreSQL -> JSON memory fallback (legacy
    conversations that predate the Postgres mirror).
    """
    from modules import pg_store, redis_store
    try:
        memory = app.state.memory
        user_id = str(user["id"]) if user else "guest"
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
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


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
        user_id = str(user["id"]) if user else "guest"
        title = request.title if request is not None and request.title else "New chat"
        conv = memory.create_conversation(user_id, title=title)
        # Mirror into PostgreSQL + drop the user's cached list so the sidebar
        # sees the new conversation immediately.
        await pg_store.ensure_conversation(user_id, conv["id"], title=title)
        await redis_store.invalidate_user(user_id)
        return conv
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.delete("/conversations/{conversation_id}")
async def delete_conversation(conversation_id: str, user = Depends(optional_auth)):
    """Delete a conversation (only the owner may delete it)"""
    from modules import pg_store, redis_store
    try:
        memory = app.state.memory
        user_id = str(user["id"]) if user else "guest"
        deleted_mem = memory.delete_conversation(user_id, conversation_id)
        deleted_pg = await pg_store.delete_conversation(user_id, conversation_id)
        await redis_store.invalidate_user(user_id)
        await redis_store.invalidate_conversation(pg_store.normalize_conversation_id(conversation_id))
        if not deleted_mem and not deleted_pg:
            raise HTTPException(status_code=404, detail="Conversation not found")
        return {"status": "success", "conversation_id": conversation_id}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ============ METRICS ENDPOINTS ============


@app.get("/metrics")
async def get_metrics_prometheus():
    """Prometheus-format metrics endpoint for scraping"""
    return metrics_endpoint(None)


@app.get("/metrics/json")
async def get_metrics_json():
    """JSON metrics endpoint for dashboards"""
    try:
        monitor = app.state.monitor
        logger = app.state.logger
        
        health = monitor.get_health()
        metrics = logger.get_metrics()
        
        return {"health": health, "metrics": metrics}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/monitoring/dashboard")
async def monitoring_dashboard():
    """
    Enterprise monitoring dashboard: business metrics, costs, SLA
    compliance, recent alerts, feature flags, and DB health.
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
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/agents/stats")
async def agent_team_stats(user = Depends(optional_auth)):
    """Real-time Agent Team statistics for the dashboard.

    Per-agent tasks handled / last-used are counted from real dispatches
    (chat multi-agent routing + MCP tool calls). Health numbers come from the
    live performance monitor. Nothing is simulated.
    """
    try:
        stats = getattr(app.state, "agent_stats", {})
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
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


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
    except Exception as e:
        raise HTTPException(
            status_code=503,
            detail=f"MCP server unreachable at {MCP_SERVER_URL}: {e}",
        )


@app.post("/mcp/tools")
async def mcp_tool_call(request: MCPToolRequest, user = Depends(optional_auth)):
    """Invoke a tool on the MCP Agent Server over the MCP protocol (SSE).

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
        _record_agent_usage(agent_keys)
    app.state.monitor.track_request()
    try:
        return await call_mcp_tool(request.name, request.arguments)
    except HTTPException:
        raise
    except Exception as e:
        app.state.monitor.track_error()
        raise HTTPException(status_code=502, detail=f"MCP tool call failed: {e}")
