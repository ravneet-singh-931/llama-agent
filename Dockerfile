# ============================================
# Llama Agent - FULL STACK Dockerfile
# ============================================

FROM python:3.10-slim

# Install system dependencies
RUN apt-get update && apt-get install -y \
    gcc \
    && rm -rf /var/lib/apt/lists/*

# Set working directory
WORKDIR /app

# Copy requirements first (for caching)
COPY requirements.txt .

# Install Python dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Copy all code
COPY . .

# Create necessary directories
RUN mkdir -p logs data documents uploads

# Expose ports
EXPOSE 8080 8501 8000

# Default command (can be overridden)
CMD ["python", "llama_agent1.py"]
