# Lightweight base image with Python 3.11
FROM python:3.11-slim

# Install system dependencies (Node.js 20)
RUN apt-get update && apt-get install -y \
    curl \
    gnupg \
    && curl -fsSL https://deb.nodesource.com/setup_20.x | bash - \
    && apt-get install -y nodejs \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# Install gemini-cli nightly globally
RUN npm install -g @google/gemini-cli@nightly

# Working directory
WORKDIR /app

# Copy Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy agent code
COPY . .

# Create necessary directories
RUN mkdir -p logs tmp repos skills data workspace

# Start command
CMD ["python", "agent.py"]
