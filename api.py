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
import os
import sys
from datetime import datetime
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


# ============ MODELS ============


class ChatRequest(BaseModel):
    """Chat request model"""
    message: str = Field(..., min_length=1, max_length=10000, description="Chat message")
    user_id: Optional[str] = "default"
    context: Optional[str] = None


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
    app.state.rag = SimpleRAG()
    app.state.logger = AgentLogger()
    app.state.monitor = PerformanceMonitor()
    app.state.config = config
    
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
    
    yield
    
    # Shutdown
    print("👋 Shutting down...")


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
    
    monitor.track_request()
    query_type = "general"
    
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
            if "=" in content:
                key, value = content.split("=", 1)
                facts.remember(key.strip(), value.strip())
                response = f"✅ Remembered: {key.strip()} = {value.strip()}"
            else:
                response = "Usage: remember key = value"
        
        elif request.message.lower().startswith("recall "):
            key = request.message[7:].strip()
            response = facts.recall(key)
        
        elif request.message.lower() == "show facts":
            all_facts = facts.get_all_facts()
            if all_facts:
                response = "\n".join(f"- {k}: {v}" for k, v in all_facts.items())
            else:
                response = "No facts stored."
        
        elif request.message.lower().startswith("search "):
            query = request.message[7:].strip()
            response = rag.get_context(query)
        
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
                # Try routing through multi-agent system for specialized tasks
                from multi_agent import MultiAgentOrchestrator
                
                # Keyword patterns for specialized agents
                code_keywords = ["write code", "function", "class", "debug", "fix bug",
                    "programming", "python", "javascript", "code review",
                    "refactor", "implement", "algorithm", "script", "api",
                    "database", "sql", "html", "css", "react", "django",
                    "flask", "fastapi", "code for", "program to"]
                writer_keywords = ["write an article", "write a blog", "write content",
                    "draft", "compose", "write an essay", "write a story",
                    "creative writing", "copywriting", "write a report",
                    "write documentation", "write a post", "write a letter"]
                planner_keywords = ["plan", "strategy", "roadmap", "steps to",
                    "how to start", "create a plan", "project plan",
                    "action plan", "timeline", "milestones", "phases"]
                analyzer_keywords = ["analyze", "analysis", "statistics", "trends",
                    "patterns", "data analysis", "evaluate", "assess",
                    "break down", "metrics", "insights"]
                research_keywords = ["research", "explain", "what is", "how does",
                    "tell me about", "compare", "difference between",
                    "pros and cons", "investigate", "explore"]
                
                task_lower = lower_input
                use_multi_agent = False
                agent_type = "research"  # default
                
                if any(kw in task_lower for kw in code_keywords):
                    use_multi_agent = True
                    agent_type = "coder"
                elif any(kw in task_lower for kw in writer_keywords):
                    use_multi_agent = True
                    agent_type = "writer"
                elif any(kw in task_lower for kw in planner_keywords):
                    use_multi_agent = True
                    agent_type = "planner"
                elif any(kw in task_lower for kw in analyzer_keywords):
                    use_multi_agent = True
                    agent_type = "analyzer"
                elif any(kw in task_lower for kw in research_keywords):
                    use_multi_agent = True
                    agent_type = "research"
                
                if use_multi_agent:
                    query_type = f"multi_agent:{agent_type}"
                    orchestrator = MultiAgentOrchestrator()
                    response = orchestrator.agents[agent_type].execute(request.message)
                else:
                    # Fall back to general ReAct agent
                    facts_context = facts.get_context_string()
                    enhanced_input = f"{request.message}\n\n{facts_context}\n\nIMPORTANT: Always respond in English."
                    from llama_agent1 import run_agent
                    response = await run_agent(enhanced_input)
        
        # Calculate response time
        elapsed = (datetime.now() - start_time).total_seconds()
        
        # Log interaction
        memory.add_message("user", request.message)
        memory.add_message("assistant", str(response))
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
    from modules.memory import ConversationMemory, FactMemory
    from modules.rag import SimpleRAG

    memory = ConversationMemory()
    facts = FactMemory()
    rag = SimpleRAG()

    async def event_generator():
        # Handle special commands first
        if request.message.lower().startswith("remember "):
            content = request.message[9:].strip()
            if "=" in content:
                key, value = content.split("=", 1)
                facts.remember(key.strip(), value.strip())
                yield f"data: {escape_sse(f'✅ Remembered: {key.strip()} = {value.strip()}')}\n\n"
            else:
                yield "data: Usage: remember key = value\n\n"
            yield "data: [DONE]\n\n"
            return

        elif request.message.lower().startswith("recall "):
            key = request.message[7:].strip()
            result = facts.recall(key)
            yield f"data: {escape_sse(result)}\n\n"
            yield "data: [DONE]\n\n"
            return

        elif request.message.lower() == "show facts":
            all_facts = facts.get_all_facts()
            if all_facts:
                result = "\n".join(f"- {k}: {v}" for k, v in all_facts.items())
            else:
                result = "No facts stored."
            yield f"data: {escape_sse(result)}\n\n"
            yield "data: [DONE]\n\n"
            return

        elif request.message.lower().startswith("search "):
            query = request.message[7:].strip()
            result = rag.get_context(query)
            yield f"data: {escape_sse(result)}\n\n"
            yield "data: [DONE]\n\n"
            return

        # Check facts
        all_facts = facts.get_all_facts()
        lower_input = request.message.lower()
        for key, value in all_facts.items():
            key_lower = key.lower()
            if (key_lower in lower_input and
                any(phrase in lower_input for phrase in ["what", "know", "recall", "tell", "remind"])):
                yield f"data: {escape_sse(f'Your {key} is **{value}**.')}\n\n"
                yield "data: [DONE]\n\n"
                return

        # Check if this is a specialized task for multi-agent
        from multi_agent import MultiAgentOrchestrator

        code_keywords = ["write code", "function", "class", "debug", "fix bug",
            "programming", "python", "javascript", "code review",
            "refactor", "implement", "algorithm", "script", "api",
            "database", "sql", "html", "css", "react", "django",
            "flask", "fastapi", "code for", "program to"]
        writer_keywords = ["write an article", "write a blog", "write content",
            "draft", "compose", "write an essay", "write a story",
            "creative writing", "copywriting", "write a report",
            "write documentation", "write a post", "write a letter"]
        planner_keywords = ["plan", "strategy", "roadmap", "steps to",
            "how to start", "create a plan", "project plan",
            "action plan", "timeline", "milestones", "phases"]
        analyzer_keywords = ["analyze", "analysis", "statistics", "trends",
            "patterns", "data analysis", "evaluate", "assess",
            "break down", "metrics", "insights"]
        research_keywords = ["research", "explain", "what is", "how does",
            "tell me about", "compare", "difference between",
            "pros and cons", "investigate", "explore"]

        task_lower = lower_input
        use_multi_agent = False
        agent_type = "research"

        if any(kw in task_lower for kw in code_keywords):
            use_multi_agent = True; agent_type = "coder"
        elif any(kw in task_lower for kw in writer_keywords):
            use_multi_agent = True; agent_type = "writer"
        elif any(kw in task_lower for kw in planner_keywords):
            use_multi_agent = True; agent_type = "planner"
        elif any(kw in task_lower for kw in analyzer_keywords):
            use_multi_agent = True; agent_type = "analyzer"
        elif any(kw in task_lower for kw in research_keywords):
            use_multi_agent = True; agent_type = "research"

        if use_multi_agent:
            # Multi-agent doesn't support streaming — collect and yield as one chunk
            orchestrator = MultiAgentOrchestrator()
            try:
                result = orchestrator.agents[agent_type].execute(request.message)
            except Exception as e:
                result = f"⚠️ Error from {agent_type} agent: {e}"
            yield f"data: {escape_sse(result)}\n\n"
        else:
            # Stream from general ReAct agent
            facts_context = facts.get_context_string()
            enhanced_input = f"{request.message}\n\n{facts_context}\n\nIMPORTANT: Always respond in English."

            for token in run_agent_stream(enhanced_input):
                yield f"data: {escape_sse(token)}\n\n"

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
    """Search documents"""
    try:
        rag = app.state.rag
        results = rag.search(request.query, top_k=request.top_k)
        return {"query": request.query, "results": results}
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
async def get_memory():
    """Get conversation memory"""
    try:
        memory = app.state.memory
        recent = memory.get_recent(20)
        stats = memory.get_stats()
        return {"stats": stats, "recent_messages": recent}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.delete("/memory")
async def clear_memory():
    """Clear all memory"""
    try:
        memory = app.state.memory
        facts = app.state.facts
        memory.clear()
        facts.clear()
        return {"status": "success", "message": "Memory cleared"}
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
    try:
        return await call_mcp_tool(request.name, request.arguments)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"MCP tool call failed: {e}")
