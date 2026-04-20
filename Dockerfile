# Lightweight base image with Python 3.11
FROM python:3.11-slim

# Install system dependencies (Node.js for MCP)
RUN apt-get update && apt-get install -y \
    curl \
    gnupg \
    git \
    && curl -fsSL https://deb.nodesource.com/setup_20.x | bash - \
    && apt-get install -y nodejs \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# Working directory
WORKDIR /app

# Copy Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy entire project (including gemini-cli)
COPY . .

# Install and build gemini-cli
WORKDIR /app/gemini-cli
RUN npm install && npm run build && npm link

# Return to main directory
WORKDIR /app

# Create necessary directories
RUN mkdir -p logs tmp repos skills data workspace

# Start command
CMD ["python", "agent.py"]
