# ============================================================================
# 📚 LEARNER GUIDE: Main Dockerfile — Dockerfile (Python Backend)
# ============================================================================
# WHAT IS A DOCKERFILE?
# ---------------------
# A Dockerfile is a recipe to build a Docker IMAGE (a portable, runnable package).
# It contains step-by-step instructions: base image, install dependencies, copy code,
# expose ports, and set the default command.
#   - Image = Blueprint (like a class)
#   - Container = Running instance (like an object)
#
# WHAT DOES THIS DOCKERFILE DO?
# ------------------------------
# Builds ONE image that can run 3 different services (via different commands):
#   1. API (FastAPI) → `uvicorn api:app --host 0.0.0.0 --port 8000` (default CMD)
#   2. Streamlit UI → `streamlit run app.py --server.port 8501 ...` (override in compose)
#   3. MCP Server → `python mcp_agents_server.py --transport sse ...` (override)
#
# WHY ONE IMAGE FOR 3 SERVICES?
# ------------------------------
# - All 3 share same Python code and dependencies (requirements.txt)
# - Saves build time and storage (one image, not three)
# - In k8s, same image is used for api, web, mcp-server Deployments (different commands)
#
# DOCKER CONCEPTS:
# ----------------
# - FROM: Base image (python:3.11-slim = lightweight Python 3.11)
# - RUN: Execute command during build (install gcc, pip install, etc.)
# - WORKDIR: Set working directory inside image (/app)
# - COPY: Copy files from host to image
# - ENV: Set environment variable inside image
# - EXPOSE: Document which ports the container listens on (doesn't actually publish)
# - CMD: Default command when container starts (can be overridden)
# - Layer caching: Each instruction creates a layer; Docker caches layers for speed
#
# HOW IT CONNECTS:
# ----------------
# - Built by: `docker compose build` or `docker build -t ravneet231/llama-agent:latest .`
# - Used by: docker-compose.yml (api, web, mcp-server services) and k8s (api, web, mcp)
# - CI builds and pushes to Docker Hub (ravneet231/llama-agent:v7, :latest)
#
# FLOW:
# -----
# 1. docker build -t my-image .
# 2. Docker reads Dockerfile, executes each instruction, creates layers
# 3. Final image contains Python + dependencies + code + model cache
# 4. docker run my-image → Runs CMD (uvicorn api:app)
# 5. docker run my-image streamlit run app.py → Overrides CMD
# 6. Verify: docker images | grep llama-agent
#            docker run --rm -it ravneet231/llama-agent:latest python -c "import groq; print('ok')"
# ============================================================================

# Base image: Python 3.11 slim (Debian-based, small, ~50MB)
#   - `python:3.11-slim` = Python 3.11 + minimal Debian (no extra tools)
#   - Alternative: `python:3.11` (full, larger), `python:3.11-alpine` (even smaller, but needs musl)
# Python 3.11: 3.10 is EOL (Oct 2026) and pillow 13 / streamlit 1.66 need >= 3.11
FROM python:3.11-slim

# Install system dependencies (needed for Python packages that compile C extensions)
#   - `apt-get update` = Update package list
#   - `apt-get install -y gcc` = Install GCC compiler (for building some pip packages)
#   - `rm -rf /var/lib/apt/lists/*` = Clean up apt cache (saves image size)
#   - Without gcc, some packages (e.g., psycopg2, bcrypt) would fail to install
RUN apt-get update && apt-get install -y \
    gcc \
    && rm -rf /var/lib/apt/lists/*

# Non-root user (v3.5.5). uid 1000 matches the usual host user, so the
# docker-compose bind mounts (./data, ./logs, ./uploads) stay writable.
# The code in /app stays owned by root: the app can't modify its own code.
RUN groupadd --gid 1000 app \
    && useradd --uid 1000 --gid 1000 --create-home --home-dir /home/app --shell /usr/sbin/nologin app
ENV HOME=/home/app

# Set working directory inside image to /app
#   - All subsequent COPY, RUN, CMD will be relative to /app
#   - If /app doesn't exist, Docker creates it
WORKDIR /app

# Copy requirements first (for Docker layer caching optimization)
#   - Docker caches layers: If requirements.txt hasn't changed, it reuses cached pip install layer
#   - This makes rebuilds FAST when only code changes (not dependencies)
#   - `COPY requirements.txt .` = Copy from host (.) to image (/app/requirements.txt)
COPY requirements.txt .

# Install Python dependencies from requirements.txt
#   - `--no-cache-dir` = Don't cache pip downloads (saves image size)
#   - `-r requirements.txt` = Install all packages listed in file
#   - This layer is cached unless requirements.txt changes
#   - Note: mcp<2 and fastmcp are now in requirements.txt (pinned), so no separate install needed
RUN pip install --no-cache-dir -r requirements.txt

# RAG reranker model (ONNX cross-encoder, ~23 MB, no PyTorch)
#   - Pinned revision + hash check (scripts/download_reranker.py)
#   - Its own layer: re-downloaded only when the script changes
#   - RERANKER_MODEL_DIR tells modules/rag_optimizations.py where it is
ENV RERANKER_MODEL_DIR=/opt/models/reranker
COPY scripts/download_reranker.py /tmp/download_reranker.py
RUN python /tmp/download_reranker.py /opt/models/reranker && rm /tmp/download_reranker.py

# Copy all code from host to image
#   - `COPY . .` = Copy everything from host current dir (.) to image /app
#   - Respects .dockerignore (excludes .git, __pycache__, node_modules, etc.)
#   - This layer changes often (when code changes), so it's after pip install for caching
COPY . .

# Pre-download the ChromaDB ONNX embedding model (all-MiniLM-L6-v2) at BUILD time
#   - Why? At runtime, first boot would download ~80MB model (slow, especially on k8s)
#   - By downloading at build time, runtime pods start FAST (model already in image)
#   - CI has fast network, so build is quick; model lands in /home/app/.cache/chroma
#   - `CHROMA_CACHE_DIR` = Where ChromaDB looks for cached models at runtime
#   - `ONNXMiniLM_L6_V2().embed_with_retries(['warmup'])` = Trigger download
#   - `rm -rf /tmp/chromadb*` = Clean up temp files
#   - `true` = Ensure command succeeds even if cleanup fails
ENV CHROMA_CACHE_DIR=/home/app/.cache/chroma
# Warm the model cache as the app user so it lands in /home/app/.cache
USER 1000:1000
RUN python -c "from chromadb.utils.embedding_functions import ONNXMiniLM_L6_V2; ONNXMiniLM_L6_V2().embed_with_retries(['warmup'])" \
    && rm -rf /tmp/chromadb* 2>/dev/null; true
USER root

# Fail the build if the reranker model can't load or ranks a relevant
# passage below an irrelevant one (catches a broken model or tokenizer).
RUN python -c "from modules.rag_optimizations import OnnxCrossEncoder as E; q='What is the capital of France?'; s=E('/opt/models/reranker').predict([(q, 'Paris is the capital and largest city of France.'), (q, 'Bananas are a yellow fruit rich in potassium.')]); assert s[0] > s[1], s; print('reranker ok', s)"

# Create necessary directories inside image
#   - `mkdir -p` = Create dirs, no error if exists
#   - `logs`, `data`, `documents`, `uploads` = Used by app for storage
#   - These are also mounted as volumes in docker-compose.yml for persistence
# Folders the app writes to at runtime (found by a live check on the pods
# and a grep of the code): data/chromadb (local Chroma), memory/ (conversation
# and fact JSON), backups/ + migrations/ (database_improvements.py),
# config/*.json (feature flags), logs/, uploads/. Only these belong to the app user.
RUN mkdir -p logs data documents uploads memory backups migrations \
    && chown -R 1000:1000 logs data documents uploads memory backups migrations \
    && chown 1000:1000 config \
    && (chown 1000:1000 config/*.json 2>/dev/null || true)

# Expose ports (documentation, doesn't actually publish)
#   - `EXPOSE 8000` = API (FastAPI)
#   - `EXPOSE 8501` = Streamlit
#   - `EXPOSE 8765` = WebSocket (though API uses 8000 for WS too)
#   - To actually publish, use `docker run -p 8000:8000` or `ports:` in compose
EXPOSE 8000 8501 8765

# Default command when container starts (can be overridden)
#   - `CMD ["uvicorn", "api:app", ...]` = Run FastAPI via Uvicorn
#   - `api:app` = Module `api.py`, variable `app` (FastAPI instance)
#   - `--host 0.0.0.0` = Listen on all interfaces (needed for Docker networking)
#   - `--port 8000` = Listen on port 8000
#   - Override examples:
#     - `docker run <image> streamlit run app.py --server.port 8501 ...` (for web)
#     - `docker run <image> python mcp_agents_server.py --transport sse ...` (for mcp)
# Run as the non-root user (k8s also enforces runAsNonRoot)
# pip is only used at build time; upgrade it so the image ships no known pip advisories
RUN python -m pip install --no-cache-dir --upgrade pip==26.2.2
USER 1000:1000
CMD ["uvicorn", "api:app", "--host", "0.0.0.0", "--port", "8000", "--timeout-graceful-shutdown", "120"]

# 💡 LEARNER TIP: Docker Layer Caching
#   Order matters! Put least-changing instructions first (FROM, RUN apt-get, COPY requirements, RUN pip)
#   Put most-changing last (COPY . .) so rebuilds are fast.
#   Check layers: docker history ravneet231/llama-agent:latest
#   Build: docker build -t test .
#   Run API: docker run -p 8000:8000 test
#   Run Streamlit: docker run -p 8501:8501 test streamlit run app.py --server.port 8501 --server.address 0.0.0.0
