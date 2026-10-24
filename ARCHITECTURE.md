# Llama Agent - Architecture Diagram

## High-Level Architecture

```
                                    ┌─────────────────────┐
                                    │    User (Browser)    │
                                    │   Chrome/Firefox/etc │
                                    └──────────┬──────────┘
                                               │
                                               │ HTTPS
                                               ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                          LOAD BALANCER LAYER                                │
│                                                                             │
│  ┌───────────────────────────────────────────────────────────────────────┐  │
│  │                         Nginx (Port 80/443)                          │  │
│  │                                                                       │  │
│  │  • SSL Termination (HTTPS → HTTP)                                     │  │
│  │  • Rate Limiting (100 req/min per IP)                                 │  │
│  │  • Load Balancing (round-robin)                                       │  │
│  │  • Static File Caching                                                │  │
│  │  • CORS Headers                                                       │  │
│  └───────────────────────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────────────────────┘
                                               │
                          ┌────────────────────┴────────────────────┐
                          │                                         │
                          ▼                                         ▼
┌─────────────────────────────────────┐       ┌─────────────────────────────────────┐
│          APPLICATION LAYER           │       │          API LAYER                  │
│                                     │       │                                     │
│  ┌───────────────────────────────┐  │       │  ┌───────────────────────────────┐  │
│  │  Next.js UI (Port 3001)       │  │       │  │   FastAPI REST (Port 8000)    │  │
│  │  Streamlit UI (Port 8501)     │  │       │  │                               │  │
│  │                               │  │       │  │  • REST Endpoints             │  │
│  │  • Chat Interface             │  │       │  │  • WebSocket Server           │  │
│  │  • File Upload                │  │       │  │  • JWT Authentication         │  │
│  │  • Login/Signup               │  │       │  │  • Pydantic Validation        │  │
│  │  • Session Management         │  │       │  │  • Rate Limiting              │  │
│  │  • Real-time Streaming        │  │       │  └───────────────────────────────┘  │
│  └───────────────────────────────┘  │       │  ┌───────────────────────────────┐  │
│                                     │       │  │  MCP Agent Server (Port 8001) │  │
│                                     │       │  │  • SSE transport              │  │
│                                     │       │  │  • 5 specialized agents       │  │
│                                     │       │  └───────────────────────────────┘  │
└─────────────────────────────────────┘       └─────────────────────────────────────┘
                          │                                         │
                          └────────────────────┬────────────────────┘
                                               │
                                               ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                           AI PIPELINE LAYER                                  │
│                                                                             │
│  ┌─────────────────────────────────────────────────────────────────────┐    │
│  │                    Query Router (RouterQueryEngine)                  │    │
│  │                                                                       │    │
│  │  User Query ──→ Classify ──→ Route to:                               │    │
│  │                              ├─→ Document Search (if files uploaded)  │    │
│  │                              ├─→ Sub-question Decomposition (complex) │    │
│  │                              └─→ General Answer (simple questions)    │    │
│  └─────────────────────────────────────────────────────────────────────┘    │
│                                    │                                        │
│              ┌─────────────────────┼─────────────────────┐                  │
│              │                     │                     │                  │
│              ▼                     ▼                     ▼                  │
│  ┌──────────────────┐  ┌──────────────────┐  ┌──────────────────┐         │
│  │  Vector Search    │  │  Sub-questions   │  │  General LLM     │         │
│  │  (ChromaDB)       │  │  (LlamaIndex)    │  │  (Groq API)      │         │
│  │                   │  │                   │  │                   │         │
│  │  • Embed query    │  │  • Break into     │  │  • Direct answer  │         │
│  │  • Find similar   │  │    smaller parts  │  │  • No documents   │         │
│  │  • Cosine sim     │  │  • Search each    │  │  • Quick response │         │
│  └────────┬─────────┘  └────────┬─────────┘  └──────────────────┘         │
│           │                     │                                           │
│           └─────────────────────┼─────────────────────┘                     │
│                                 │                                           │
│                                 ▼                                           │
│  ┌─────────────────────────────────────────────────────────────────────┐    │
│  │                      Re-ranking (CrossEncoder)                       │    │
│  │                                                                       │    │
│  │  • Take top 10 results from search                                   │    │
│  │  • Re-score by relevance to original query                           │    │
│  │  • Return top 3 most relevant chunks                                 │    │
│  └─────────────────────────────────────────────────────────────────────┘    │
│                                 │                                           │
│                                 ▼                                           │
│  ┌─────────────────────────────────────────────────────────────────────┐    │
│  │                     Answer Generation (Groq API)                     │    │
│  │                                                                       │    │
│  │  Context (relevant chunks) + User Query ──→ Groq LLM ──→ Answer     │    │
│  └─────────────────────────────────────────────────────────────────────┘    │
└─────────────────────────────────────────────────────────────────────────────┘
                                               │
                                               ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                            DATA LAYER                                        │
│                                                                             │
│  ┌─────────────────────────┐  ┌─────────────────────────┐                 │
│  │   PostgreSQL (Port 5432) │  │    Redis (Port 6379)    │                 │
│  │                          │  │                          │                 │
│  │  • User Accounts         │  │  • Session Cache         │                 │
│  │  • Chat History          │  │  • Rate Limit Counters   │                 │
│  │  • API Keys              │  │  • WebSocket Pub/Sub     │                 │
│  │  • Document Metadata     │  │  • Query Cache           │                 │
│  └─────────────────────────┘  └─────────────────────────┘                 │
│                                                                             │
│  ┌─────────────────────────┐  ┌─────────────────────────┐                 │
│  │  ChromaDB (Vectors)      │  │   File System            │                 │
│  │                          │  │                          │                 │
│  │  • Document Embeddings   │  │  • Uploaded PDFs         │                 │
│  │  • Semantic Search       │  │  • Text Files            │                 │
│  │  • Cosine Similarity     │  │  • CSV Data              │                 │
│  │  • Persistent Storage    │  │  • Documents/            │                 │
│  └─────────────────────────┘  └─────────────────────────┘                 │
└─────────────────────────────────────────────────────────────────────────────┘
                                               │
                                               ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                       OBSERVABILITY LAYER                                    │
│                                                                             │
│  ┌─────────────────────────────────────────────────────────────────────┐    │
│  │                        Monitoring Stack                              │    │
│  │                                                                       │    │
│  │  Prometheus ──→ Grafana (Dashboards)                                 │    │
│  │  • Request metrics      • Visual graphs                              │    │
│  │  • Response times       • Alert rules                                 │    │
│  │  • Error rates          • Custom dashboards                           │    │
│  └─────────────────────────────────────────────────────────────────────┘    │
│                                                                             │
│  ┌─────────────────────────────────────────────────────────────────────┐    │
│  │                         Logging Stack                                │    │
│  │                                                                       │    │
│  │  Promtail ──→ Loki ──→ Grafana                                       │    │
│  │  • Ship logs         • Store logs     • Query logs                    │    │
│  │  • From containers   • Searchable     • Visualize                     │    │
│  └─────────────────────────────────────────────────────────────────────┘    │
│                                                                             │
│  ┌─────────────────────────────────────────────────────────────────────┐    │
│  │                       AI Observability                               │    │
│  │                                                                       │    │
│  │  Phoenix (Arize AI)                                                  │    │
│  │  • Trace every RAG step                                              │    │
│  │  • Debug wrong answers                                               │    │
│  │  • See AI thinking process                                           │    │
│  │  • OpenTelemetry spans                                               │    │
│  └─────────────────────────────────────────────────────────────────────┘    │
└─────────────────────────────────────────────────────────────────────────────┘
```

## Data Flow

### 1. User Uploads Document

```
User ──→ Nginx ──→ Next.js/Streamlit ──→ FastAPI ──→ PostgreSQL (metadata)
                                    │
                                    ├──→ File System (save file)
                                    │
                                    └──→ ChromaDB (create embeddings)
```

### 2. User Asks Question

```
User ──→ Nginx ──→ Next.js/Streamlit ──→ FastAPI
                                    │
                                    ▼
                              Query Router
                                    │
                    ┌───────────────┼───────────────┐
                    │               │               │
                    ▼               ▼               ▼
              Vector Search   Sub-questions   General LLM
                    │               │               │
                    └───────────────┼───────────────┘
                                    │
                                    ▼
                              Re-ranking
                                    │
                                    ▼
                           Answer Generation
                                    │
                                    ▼
                              Next.js/Streamlit ──→ User
```

### 3. Observability Flow

```
AI Pipeline ──→ PhoenixTracer ──→ OpenTelemetry Spans ──→ Phoenix Dashboard
                    │
                    └──→ Prometheus Metrics ──→ Grafana Dashboard
```

## Container Communication

```
┌──────────────┐
│   Nginx      │──────→ Next.js (3001)
└──────────────┘       Streamlit (8501)
       │               FastAPI (8000)
       │ HTTP
       ▼
┌──────────────┐
│  Next.js /   │──────→ FastAPI (8000)
│  Streamlit   │       PostgreSQL (5432)
└──────────────┘       Redis (6379)
       │               Phoenix (6006)
       │ HTTP
       ▼
┌──────────────┐
│   FastAPI    │──────→ PostgreSQL (5432)
└──────────────┘       Redis (6379)
       │               Groq API (external)
       │               ChromaDB (local)
       │ SSE
       ▼
┌──────────────┐
│ MCP Server   │──────→ Multi-Agent System ──→ Groq API
└──────────────┘
```

## Security Layers

```
┌─────────────────────────────────────────────────────────────┐
│ Layer 1: Network Security                                    │
│ • Nginx rate limiting                                        │
│ • CORS restrictions                                          │
│ • SSL/TLS encryption                                         │
│ • Security headers (CSP, HSTS, X-Frame-Options, etc.)       │
└─────────────────────────────────────────────────────────────┘
                            │
                            ▼
┌─────────────────────────────────────────────────────────────┐
│ Layer 2: Authentication                                      │
│ • JWT tokens (24h expiry)                                    │
│ • Password hashing (bcrypt)                                  │
│ • API key authentication                                     │
└─────────────────────────────────────────────────────────────┘
                            │
                            ▼
┌─────────────────────────────────────────────────────────────┐
│ Layer 3: Input Validation                                    │
│ • Pydantic models                                            │
│ • File type checking                                         │
│ • Size limits (10MB)                                         │
│ • SQL injection prevention                                   │
│ • Input sanitization + audit logging                         │
│ • Brute-force protection (IP + username lockout)             │
└─────────────────────────────────────────────────────────────┘
                            │
                            ▼
┌─────────────────────────────────────────────────────────────┐
│ Layer 4: Secrets Management                                  │
│ • Docker secrets (mounted as files, never in env or images)  │
│ • No secrets in git                                          │
│ • Environment variables                                      │
└─────────────────────────────────────────────────────────────┘
```

## Scalability

### Current (Single Server)

```
┌─────────────────────────────────────────────────────────────┐
│                     Single Server                             │
│                                                               │
│  ┌─────────┐ ┌─────────┐ ┌─────────┐ ┌─────────┐           │
│  │postgres │ │  redis  │ │  api    │ │  web    │           │
│  └─────────┘ └─────────┘ └─────────┘ └─────────┘           │
│                                                               │
│  ┌─────────┐ ┌─────────┐ ┌─────────┐ ┌─────────┐           │
│  │ nginx   │ │prometheus│ │ grafana │ │ phoenix │           │
│  └─────────┘ └─────────┘ └─────────┘ └─────────┘           │
│                                                               │
│  ┌─────────┐ ┌─────────┐                                     │
│  │ frontend│ │ mcp-srv │                                     │
│  └─────────┘ └─────────┘                                     │
└─────────────────────────────────────────────────────────────┘
```

### Future (Multi-Server)

```
┌──────────────────┐  ┌──────────────────┐  ┌──────────────────┐
│   Server 1       │  │   Server 2       │  │   Server 3       │
│   (App)          │  │   (Database)     │  │   (Monitoring)   │
│                  │  │                  │  │                  │
│  ┌─────┐ ┌─────┐│  │  ┌─────┐ ┌─────┐│  │  ┌─────┐ ┌─────┐│
│  │ api │ │ web ││  │  │pg   │ │redis││  │  │prom │ │graf ││
│  └─────┘ └─────┘│  │  └─────┘ └─────┘│  │  └─────┘ └─────┘│
└──────────────────┘  └──────────────────┘  └──────────────────┘
         │                     │                     │
         └─────────────────────┼─────────────────────┘
                               │
                        Load Balancer
```
