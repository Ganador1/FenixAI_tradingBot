# syntax=docker/dockerfile:1.7

# ==============================================================================
# FenixAI Trading Bot - Multi-stage Dockerfile
# ==============================================================================
# Build: docker build -t fenix-trading-bot .
# Run: docker run -p 8000:8000 --env-file .env fenix-trading-bot
# ==============================================================================

ARG PYTHON_VERSION=3.12.13

# Stage 1: Builder - install Python dependencies
FROM python:${PYTHON_VERSION}-slim-bookworm AS builder

WORKDIR /app

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONUNBUFFERED=1

# Install build dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    gcc \
    libffi-dev \
    libssl-dev \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Create virtual environment
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# Copy dependency files first for caching
COPY pyproject.toml ./
COPY docker/constraints.txt ./docker/constraints.txt

# Resolve runtime dependencies from pyproject.toml without copying source code.
# This keeps dependency layers cacheable when only application code changes.
RUN python - <<'PY' > /tmp/requirements.txt
import tomllib

with open("pyproject.toml", "rb") as fh:
    project = tomllib.load(fh)["project"]

seen = set()
for dependency in project["dependencies"]:
    if dependency not in seen:
        print(dependency)
        seen.add(dependency)

optional = project.get("optional-dependencies", {})
for extra in ("llm", "vision", "monitoring"):
    for dependency in optional.get(extra, []):
        if dependency not in seen:
            print(dependency)
            seen.add(dependency)
PY

# Install Python dependencies with BuildKit cache support.
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install --upgrade pip wheel setuptools && \
    pip install -r /tmp/requirements.txt -c docker/constraints.txt

# ==============================================================================
# Stage 2: Runtime - Minimal production image
# ==============================================================================
FROM python:${PYTHON_VERSION}-slim-bookworm AS runtime

WORKDIR /app

# Install runtime dependencies only
RUN apt-get update && apt-get install -y --no-install-recommends \
    libssl3 \
    ca-certificates \
    curl \
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

# Create necessary directories
RUN mkdir -p /app/logs /app/data /app/htmlcov /app/data/hf-cache /app/data/sentence-transformers && \
    chown -R fenix:fenix /app

# Switch to non-root user
USER fenix

# Expose API port
EXPOSE 8000

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=20s --retries=3 \
    CMD curl -f http://localhost:8000/health || exit 1

# Default command - Start API server
CMD ["uvicorn", "src.api.server:app", "--host", "0.0.0.0", "--port", "8000"]
