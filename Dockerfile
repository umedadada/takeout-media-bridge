# Dockerfile for takeout-media-bridge
# Build stage
FROM python:3.11-slim

# Install system dependencies (if any)
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Set working directory
WORKDIR /app

# Copy requirements and install
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# Copy the source code
COPY . .

# Set entrypoint (default runs the CLI)
ENTRYPOINT ["python", "bridge.py"]
