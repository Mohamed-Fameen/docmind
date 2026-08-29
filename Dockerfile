# Backend image — FastAPI app + retrieval pipeline (embedding model, reranker, BM25).
#
# Note on GPU: this image runs the embedding model and reranker on CPU by default. Most
# affordable hosting platforms (Railway, Fly.io, Render) don't offer GPU instances at
# reasonable cost, and CPU is workable for these specific models — bge-base-en-v1.5 and
# bge-reranker-base are small enough (compared to an LLM) that CPU inference is acceptable
# for personal-project-scale traffic, even though it's slower than the RTX 3050 locally.
# The LLM (the actually GPU-hungry piece) is offloaded to AWS Bedrock in production instead
# of running locally at all — see docs/09-deployment.md for why this matters more than the
# embedder/reranker's device.

FROM python:3.12-slim

WORKDIR /app

# System deps: psycopg2 needs libpq at build time; curl is handy for container health checks.
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq-dev \
    gcc \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Install uv (same tool used for local dev — keeps dependency resolution identical between
# your machine and this image, rather than introducing a second, potentially-divergent
# dependency installation path).
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /usr/local/bin/

# Copy dependency files first (before the rest of the source) so Docker's layer cache can
# skip reinstalling dependencies on every rebuild when only application code changed.
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

COPY backend/ ./backend/
COPY data/processed/chunks.jsonl ./data/processed/chunks.jsonl
# ^ needed at runtime: the BM25 index is built from this file at startup (see
# backend/app/retrieval.py) — it must ship with the image, not just live on your local disk.

ENV PATH="/app/.venv/bin:$PATH"

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=10s --start-period=60s --retries=3 \
    CMD curl -f http://localhost:8000/health || exit 1

CMD ["uvicorn", "backend.app.main:app", "--host", "0.0.0.0", "--port", "8000"]
