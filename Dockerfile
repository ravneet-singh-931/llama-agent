# ============================================
# Llama Agent - Full Stack Dockerfile
# Supports: API + Streamlit + MCP Agents
# ============================================

FROM python:3.10-slim

# Install system dependencies
RUN apt-get update && apt-get install -y \
    gcc \
    && rm -rf /var/lib/apt/lists/*

# Set working directory
WORKDIR /app

# Copy requirements first (for Docker layer caching)
COPY requirements.txt .

# Install Python dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Install MCP (requires Python 3.10+)
RUN pip install --no-cache-dir mcp

# Copy all code
COPY . .

# Create necessary directories
RUN mkdir -p logs data documents uploads

# Expose ports
# 8000 = REST API (FastAPI)
# 8501 = Streamlit Web UI
# 8765 = WebSocket
EXPOSE 8000 8501 8765

# Default command runs the API server
# Override with: docker run <image> streamlit run app.py
CMD ["uvicorn", "api:app", "--host", "0.0.0.0", "--port", "8000"]
