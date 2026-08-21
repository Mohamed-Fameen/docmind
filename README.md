# DocMind

An agentic RAG assistant over the Kubernetes documentation — built end-to-end as a learning
project covering data ingestion, hybrid retrieval, local + hosted LLM inference, evaluation,
and full-stack deployment.

## Why this project

Most RAG tutorials stop at "embed and stuff into a prompt." DocMind goes further:

- **Hybrid retrieval** — dense (vector) + sparse (BM25) search, combined and reranked
- **Agentic control flow** — the system decides whether to retrieve, re-retrieve, or answer
  directly (via LangGraph), instead of always doing a fixed retrieve-then-generate pass
- **Local-first development** — embeddings, reranking, and dev-time generation run locally on
  a consumer GPU (RTX 3050) via `sentence-transformers` and Ollama, at zero API cost
- **Production swap** — generation switches to a hosted model (Claude Haiku / GPT-4o-mini) in prod
- **Real evaluation** — RAGAS-based scoring (faithfulness, relevance, context precision/recall),
  not just eyeballing outputs
- **A real product shape** — multi-user auth, persistent chat history, streaming UI, citations

## Build log

Every phase is documented as it's built — see [`docs/`](./docs). This is the actual record of
decisions made, not a retrospective writeup, so it doubles as an interview-ready project history.

| Phase | Doc |
|---|---|
| 0. Environment setup | [docs/00-setup.md](./docs/00-setup.md) |
| 1. Data acquisition | docs/01-data-acquisition.md |
| 2. Loading & chunking | docs/02-chunking.md |
| 3. Embeddings & vector store | docs/03-embeddings.md |
| 4. Backend RAG core | docs/04-backend.md |
| 5. Agentic layer | docs/05-agentic.md |
| 6. Memory & auth | docs/06-memory-auth.md |
| 7. Frontend | docs/07-frontend.md |
| 8. Evaluation | docs/08-eval.md |
| 9. Deployment | docs/09-deployment.md |

## Project structure

```
docmind/
├── data/
│   ├── raw/                # cloned kubernetes/website markdown source
│   └── processed/          # chunked, metadata-tagged JSONL
├── ingestion/               # fetch, chunk, embed scripts
├── backend/                 # FastAPI app (retrieval + agentic RAG pipeline)
├── frontend/                 # Next.js chat UI
├── eval/                     # RAGAS evaluation scripts + test question set
├── docker/                   # docker-compose for Qdrant / Redis / Postgres
└── docs/                     # phase-by-phase build log
```

## Quickstart (dev)

```bash
# 1. Clone and enter
git clone <your-fork-url> docmind && cd docmind

# 2. Install Python deps (using uv — see docs/00-setup.md for why)
uv sync

# 3. Copy env template and fill in as needed (defaults work for local dev)
cp .env.example .env

# 4. Start local infra (Qdrant, Redis, Postgres)
docker compose -f docker/docker-compose.yml up -d

# 5. Pull local LLM for dev-time generation
ollama pull llama3.1:8b

# 6. Run backend
uv run uvicorn backend.app.main:app --reload
```

## Status

🚧 Phase 0 in progress — environment scaffolding.
