# ============================================
# Llama Agent - Advanced Dockerfile
# ============================================

# Step 1: Use Python 3.10 slim image
FROM python:3.10-slim

# Step 2: Set working directory
WORKDIR /app

# Step 3: Install system dependencies (for security tools)
RUN apt-get update && apt-get install -y \
    gcc \
    && rm -rf /var/lib/apt/lists/*

# Step 4: Copy requirements first (for caching)
COPY requirements.txt .

# Step 5: Install Python dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Step 6: Copy the rest of the code
COPY . .

# Step 7: Run tests during build (optional quality gate)
RUN pytest tests/ -v || true

# Step 8: Default command
CMD ["python", "llama_agent1.py"]
