# Step 1: Start with a Python base image
# This is like choosing the "operating system" for your code
FROM python:3.10-slim

# Step 2: Set the working directory inside the container
# This is like saying "cd /app" — all commands run from here
WORKDIR /app

# Step 3: Copy requirements first (for caching)
# Docker caches this step — so if requirements don't change,
# Docker skips reinstalling them (saves time!)
COPY requirements.txt .

# Step 4: Install dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Step 5: Copy the rest of your code
COPY . .

# Step 6: Tell Docker what command to run when someone starts this container
CMD ["python", "llama_agent1.py"]
