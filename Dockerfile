# Dockerfile for the NoSlipQuant MCP server (stdio transport).
# Glama builds this image, runs security checks, and fronts it with the Gateway.
FROM python:3.11-slim

# System build dependencies needed by prophet (cmdstan), scikit-learn,
# solders, curl-cffi and pillow.
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
        gcc \
        g++ \
        libcurl4-openssl-dev \
        libssl-dev \
        ca-certificates \
    && rm -rf /var/lib/apt/lists/*

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Install Python dependencies first so the layer is cached across code changes.
COPY services/trader/requirements.txt /app/services/trader/requirements.txt
RUN pip install --upgrade pip \
    && pip install -r /app/services/trader/requirements.txt

# Copy the application source.
COPY . /app

# The MCP server communicates over stdio (JSON-RPC on stdin/stdout).
ENTRYPOINT ["python", "-u", "services/trader/mcp_server.py"]
