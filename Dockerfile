# syntax=docker/dockerfile:1.7

# ==============================================================================
# FenixAI Trading Bot - Multi-stage Dockerfile
# ==============================================================================
# Build: docker build -t fenix-trading-bot .
# Run: docker run -p 8000:8000 --env-file .env fenix-trading-bot
# ==============================================================================

ARG PYTHON_VERSION=3.12.13
ARG PYTHON_IMAGE_DIGEST=sha256:d50fb7611f86d04a3b0471b46d7557818d88983fc3136726336b2a4c657aa30b

# Stage 1: Builder - install Python dependencies
FROM python:${PYTHON_VERSION}-slim-bookworm@${PYTHON_IMAGE_DIGEST} AS builder

WORKDIR /app

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONUNBUFFERED=1

# Install build dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    gcc \
    libffi-dev \
    libssl-dev \
    && rm -rf /var/lib/apt/lists/*

# Create virtual environment
RUN python -m venv /opt/venv
ENV VIRTUAL_ENV=/opt/venv \
    PATH="/opt/venv/bin:$PATH"

# Copy the frozen dependency graph first for reproducible, cacheable installs.
COPY pyproject.toml uv.lock ./

# Install Python dependencies with BuildKit cache support.
RUN --mount=type=cache,target=/root/.cache/uv \
    pip install --no-cache-dir "uv==0.9.17" && \
    uv sync --active --frozen --no-dev --no-install-project \
      --extra llm --extra vision --extra monitoring

# ==============================================================================
# Stage 2: Runtime - Minimal production image
# ==============================================================================
FROM python:${PYTHON_VERSION}-slim-bookworm@${PYTHON_IMAGE_DIGEST} AS runtime

WORKDIR /app

# Install runtime dependencies only
RUN apt-get update && apt-get install -y --no-install-recommends \
    libssl3 \
    ca-certificates \
    libgl1 \
    libglib2.0-0 \
    libgomp1 \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --system --gid 1000 fenix \
    && useradd --system --create-home --uid 1000 --gid fenix fenix

# Copy virtual environment from builder
COPY --from=builder /opt/venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# Set Python environment variables
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app \
    HF_HOME=/app/data/hf-cache \
    SENTENCE_TRANSFORMERS_HOME=/app/data/sentence-transformers

# Copy application code
COPY --chown=fenix:fenix src/ ./src/
COPY --chown=fenix:fenix config/ ./config/
COPY --chown=fenix:fenix run_fenix.py ./run_fenix.py
COPY --chown=fenix:fenix run_nanofenixv3.py ./run_nanofenixv3.py
COPY --chown=fenix:fenix nanofenixv3/ ./nanofenixv3/
COPY --chown=fenix:fenix docker/entrypoint.sh /app/docker-entrypoint.sh

# Create necessary directories
RUN mkdir -p /app/logs /app/data /app/htmlcov /app/data/hf-cache /app/data/sentence-transformers && \
    chown -R fenix:fenix /app && \
    chmod 0555 /app/docker-entrypoint.sh

# Switch to non-root user
USER fenix

# Expose API port
EXPOSE 8000

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=20s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://localhost:8000/health', timeout=5).read()"]

ENTRYPOINT ["/app/docker-entrypoint.sh"]

# Default command - Start API + authenticated Socket.IO server
CMD ["uvicorn", "src.api.server:app_socketio", "--host", "0.0.0.0", "--port", "8000", "--no-server-header"]
