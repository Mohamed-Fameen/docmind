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

## Roadmap (post-Phase 9)

Two larger architectural directions, deliberately deferred until the core 9-phase build is
done end-to-end — noted here so the reasoning isn't lost, not because they're an afterthought:

**1. Multi-source / multi-domain retrieval.** Right now DocMind is hard-wired to one corpus
(Kubernetes docs) and one Qdrant collection. The retrieval architecture is already naturally
decoupled from the rest of the pipeline (a "data source" is really just: a chunking strategy
+ a Qdrant collection + a BM25 index), so this generalizes to supporting several independent
corpora — e.g. Kubernetes, Docker, Spring Boot — each in its own collection, with either (a)
explicit user selection of which source to query, or (b) automatic routing based on detected
query intent. The harder requirement is adding a new source without downtime: the ingestion
pipeline (Phases 1-3) needs to be re-runnable against a new corpus into a *new* collection
while the app keeps serving queries against existing collections, rather than requiring a
full app restart or re-index of everything.

**2. Systematic model comparison (3B local vs 8B+/hosted).** The model registry
(`backend/app/config.py`) already supports switching generation models per-request — the
next step is a proper eval harness (building on Phase 8's RAGAS work) that runs the *same*
retrieved context through several models (e.g. `llama3.2-3b-local` vs a larger Bedrock model)
and scores the answers, to get an actual measured answer to "how much does a bigger model
improve quality once retrieval is already doing the heavy lifting" — rather than an assumed
number. This is also the mechanism for backing up any "model X is Y% better" claim with real
data instead of a guess.

## Status

🚧 Phase 0 in progress — environment scaffolding.
