.PHONY: setup check up down backend

# Install Python dependencies
setup:
	uv sync

# Run the Phase 0 environment sanity check
check:
	uv run python ingestion/verify_setup.py

# Start local infra (Qdrant, Redis, Postgres)
up:
	docker compose -f docker/docker-compose.yml up -d

# Tear down local infra
down:
	docker compose -f docker/docker-compose.yml down

# Run the FastAPI backend with hot reload
backend:
	uv run uvicorn backend.app.main:app --reload
