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
│  │    Streamlit UI (Port 8501)   │  │       │  │   FastAPI REST (Port 8000)    │  │
│  │                               │  │       │  │                               │  │
│  │  • Chat Interface             │  │       │  │  • REST Endpoints             │  │
│  │  • File Upload                │  │       │  │  • WebSocket Server           │  │
│  │  • Login/Signup               │  │       │  │  • JWT Authentication         │  │
│  │  • Session Management         │  │       │  │  • Pydantic Validation        │  │
│  │  • Real-time Streaming        │  │       │  │  • Rate Limiting              │  │
│  └───────────────────────────────┘  │       │  └───────────────────────────────┘  │
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
│  │  Context (relevant chunks) + User Query ──→ Llama 3 ──→ Answer      │    │
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
User ──→ Nginx ──→ Streamlit ──→ FastAPI ──→ PostgreSQL (metadata)
                                    │
                                    ├──→ File System (save file)
                                    │
                                    └──→ ChromaDB (create embeddings)
```

### 2. User Asks Question

```
User ──→ Nginx ──→ Streamlit ──→ FastAPI
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
                              Streamlit ──→ User
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
│   Nginx      │──────→ Streamlit (8501)
└──────────────┘       FastAPI (8000)
       │
       │ HTTP
       ▼
┌──────────────┐
│  Streamlit   │──────→ FastAPI (8000)
└──────────────┘       PostgreSQL (5432)
       │               Redis (6379)
       │               Phoenix (6006)
       │ HTTP
       ▼
┌──────────────┐
│   FastAPI    │──────→ PostgreSQL (5432)
└──────────────┘       Redis (6379)
                       Groq API (external)
                       ChromaDB (local)
```

## Security Layers

```
┌─────────────────────────────────────────────────────────────┐
│ Layer 1: Network Security                                    │
│ • Nginx rate limiting                                        │
│ • CORS restrictions                                          │
│ • SSL/TLS encryption                                         │
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
│ • Size limits (50MB)                                         │
│ • SQL injection prevention                                   │
└─────────────────────────────────────────────────────────────┘
                            │
                            ▼
┌─────────────────────────────────────────────────────────────┐
│ Layer 4: Secrets Management                                  │
│ • Docker secrets (encrypted at rest)                         │
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
