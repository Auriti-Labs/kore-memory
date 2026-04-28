FROM python:3.12-slim AS base

LABEL maintainer="Auriti Labs <info@auritidesign.it>"
LABEL org.opencontainers.image.source="https://github.com/Auriti-Labs/kore-memory"
LABEL org.opencontainers.image.description="Kore Memory — the memory layer that thinks like a human"

# Prevent Python from writing .pyc and enable unbuffered output
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# Install only production deps first (cache layer)
COPY pyproject.toml hatch_build.py README.md LICENSE ./
COPY kore_memory/ kore_memory/
COPY assets/ assets/

RUN pip install --no-cache-dir ".[semantic,mcp,watcher]"

# Runtime config
ENV KORE_HOST=0.0.0.0 \
    KORE_PORT=8765 \
    KORE_LOCAL_ONLY=0 \
    KORE_DB_PATH=/data/memory.db

VOLUME /data
EXPOSE 8765 8766

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import httpx; r=httpx.get('http://127.0.0.1:8765/health'); r.raise_for_status()"

# Default: run REST API server
CMD ["kore", "--host", "0.0.0.0", "--port", "8765"]
