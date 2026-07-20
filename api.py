"""
Llama Agent - REST API (FastAPI)
Run with: uvicorn api:app --reload
"""

import asyncio
import os
import sys
from datetime import datetime
from typing import Optional, List, Dict
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, UploadFile, File
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel

# Add current directory to path
sys.path.insert(0, os.path.dirname(__file__))


# ============ MODELS ============

class ChatRequest(BaseModel):
    """Chat request model"""
    message: str
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
    key: str
    value: str


class RecallRequest(BaseModel):
    """Recall fact request"""
    key: str


class SearchRequest(BaseModel):
    """Search documents request"""
    query: str
    top_k: Optional[int] = 3


class HealthResponse(BaseModel):
    """Health check response"""
    status: str
    uptime: str
    version: str
    modules: Dict[str, str]


# ============ APP LIFETIME ============

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup and shutdown events"""
    # Startup
    print("🚀 Llama Agent API starting...")
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
    
    print("✅ All modules loaded!")
    print("🌐 API ready at http://localhost:8000")
    
    yield
    
    # Shutdown
    print("👋 Shutting down...")


# ============ CREATE APP ============

app = FastAPI(
    title="🤖 Llama Agent API",
    description="AI Agent with Memory, RAG, and 14 Tools",
    version="2.0.0",
    lifespan=lifespan
)

# CORS middleware (allows web apps to call the API)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ============ ROUTES ============

@app.get("/")
async def root():
    """Root endpoint"""
    return {
        "name": "🤖 Llama Agent API",
        "version": "2.0.0",
        "docs": "/docs",
        "endpoints": {
            "chat": "/chat",
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
        version="2.0.0",
        modules={
            "memory": "active",
            "rag": "active",
            "monitoring": "active"
        }
    )


@app.post("/chat", response_model=ChatResponse)
async def chat(request: ChatRequest):
    """Chat with the agent"""
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
            # Regular AI chat
            from llama_agent1 import run_agent
            response = await run_agent(request.message)
        
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


@app.post("/documents/read")
async def read_document(file_path: str):
    """Read a document"""
    try:
        rag = app.state.rag
        result = rag.add_document(file_path)
        return {"status": "success", "message": result}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/documents/upload")
async def upload_document(file: UploadFile = File(...)):
    """Upload and read a document"""
    try:
        # Save uploaded file
        upload_dir = "uploads"
        os.makedirs(upload_dir, exist_ok=True)
        
        file_path = os.path.join(upload_dir, file.filename)
        with open(file_path, "wb") as f:
            content = await file.read()
            f.write(content)
        
        # Read with RAG
        rag = app.state.rag
        result = rag.add_document(file_path)
        
        return {"status": "success", "message": result, "file": file.filename}
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


@app.get("/metrics")
async def get_metrics():
    """Get agent metrics"""
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
