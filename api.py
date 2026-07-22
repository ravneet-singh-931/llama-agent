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
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import JSONResponse
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
)
from modules.websocket import get_ws_handler, MessageType, WSMessage

# Prometheus metrics
from modules.prometheus_metrics import (
    init_metrics,
    metrics_endpoint,
    prometheus_middleware,
)


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
    
    # Initialize database (if enabled)
    if config.features.enable_auth:
        try:
            db = get_db_manager()
            await db.init_db()
            print("✅ Database initialized")
        except Exception as e:
            print(f"⚠️  Database not available: {e}")
            print("   Running without persistence (memory-only mode)")
    
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
import os as _os
_environment = _os.getenv("ENVIRONMENT", "development")

if _environment == "production":
    # PRODUCTION: Only allow your actual domain(s)
    cors_origins = [
        "http://localhost:80",      # Nginx HTTP
        "http://localhost:443",     # Nginx HTTPS
        "http://localhost:8501",    # Streamlit direct
        "http://localhost:3000",    # Grafana
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

# Rate limiting middleware
from modules.security import rate_limiter

@app.middleware("http")
async def rate_limit_middleware(request: Request, call_next):
    """Rate limiting to prevent abuse"""
    # Skip rate limiting for health checks and metrics
    if request.url.path in ("/health", "/metrics", "/"):
        return await call_next(request)
    
    # Get client IP
    client_ip = request.client.host if request.client else "unknown"
    
    is_limited, message = rate_limiter.is_rate_limited(client_ip)
    if is_limited:
        return JSONResponse(
            status_code=429,
            content={"detail": message, "retry_after": 60},
        )
    
    return await call_next(request)

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
        ],
        "endpoints": {
            "chat": "/chat",
            "auth": "/auth/register, /auth/login",
            "websocket": "/ws/{user_id}",
            "memory": "/memory",
            "facts": "/facts",
            "documents": "/documents",
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
async def register(request: RegisterRequest):
    """Register a new user"""
    try:
        result = await user_manager.register(
            username=request.username,
            email=request.email,
            password=request.password,
            full_name=request.full_name,
        )
        return TokenResponse(
            access_token=result["access_token"],
            refresh_token=result["refresh_token"],
        )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/auth/login", response_model=TokenResponse)
async def login(request: LoginRequest):
    """Login with username/email and password"""
    try:
        result = await user_manager.login(
            username=request.username,
            password=request.password,
        )
        return TokenResponse(
            access_token=result["access_token"],
            refresh_token=result["refresh_token"],
        )
    except HTTPException:
        raise
    except Exception as e:
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
    username: str = "Anonymous",
):
    """
    WebSocket endpoint for real-time chat
    
    Connect: ws://localhost:8000/ws/{user_id}?username=YourName
    
    Messages (JSON):
    - Send: {"type": "chat", "data": {"content": "Hello!"}}
    - Receive: {"type": "response", "data": {"content": "Hi there!"}}
    """
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
            
            # Regular AI chat — include facts as context
            if not fact_answered:
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
        
        return ChatResponse(
            response=str(response),
            timestamp=datetime.now().isoformat(),
            response_time=elapsed
        )
        
    except Exception as e:
        monitor.track_error()
        logger.log_error(e, context=f"User: {request.message}")
        raise HTTPException(status_code=500, detail=str(e))


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
        # SECURITY: Basic path validation - reject obvious traversal attempts
        if ".." in file_path or file_path.startswith("/"):
            raise HTTPException(
                status_code=400,
                detail="Invalid file path: path traversal not allowed",
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
async def upload_document(file: UploadFile = File(...)):
    """Upload and read a document (with security validation)"""
    try:
        # SECURITY: Validate file upload
        from modules.security import validate_file_upload, sanitize_filename, get_upload_path
        
        # Read file content to check size and validate
        content = await file.read()
        file_size = len(content)
        
        is_valid, error = validate_file_upload(
            file.filename or "unknown",
            file_size,
            file.content_type,
        )
        if not is_valid:
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
