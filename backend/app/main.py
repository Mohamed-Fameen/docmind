"""
DocMind backend — the /query endpoint ties retrieval (Phase 3's hybrid search + reranking)
to generation (any model in config.py's MODEL_REGISTRY) into one RAG request.
"""

from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from .config import settings
from .llm import build_prompt, generate_answer
from .retrieval import RetrievalPipeline

pipeline = RetrievalPipeline()


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Load the embedding model, reranker, and BM25 index ONCE at startup — these are
    # expensive to load (seconds) and must not be reloaded on every request.
    pipeline.load()
    yield


app = FastAPI(title="DocMind API", version="0.1.0", lifespan=lifespan)


class QueryRequest(BaseModel):
    query: str
    model: str | None = None  # registry key from config.MODEL_REGISTRY; defaults if omitted


class SourceRef(BaseModel):
    number: int
    heading_path: str
    doc_url: str


class QueryResponse(BaseModel):
    answer: str
    sources: list[SourceRef]
    model_used: str


@app.get("/health")
def health():
    return {"status": "ok", "phase": "4 — backend RAG core"}


@app.get("/models")
def list_models():
    """
    Lists every registered generation model, so a frontend (or a comparison script) can
    present real, currently-configured options rather than a hardcoded guess.
    """
    return {
        "default": settings.default_model,
        "available": {
            name: {"provider": cfg["provider"], "description": cfg.get("description", "")}
            for name, cfg in settings.MODEL_REGISTRY.items()
        },
    }


@app.post("/query", response_model=QueryResponse)
def query(request: QueryRequest):
    """
    Note: this is a sync `def`, not `async def`. FastAPI runs sync route functions in a
    threadpool automatically — appropriate here since embedding, BM25 scoring, and
    reranking are all CPU/GPU-bound blocking calls, not I/O-bound async-friendly ones.
    Declaring this `async def` while calling blocking code inside it would instead block
    the whole event loop, stalling every other in-flight request.
    """
    if request.model and request.model not in settings.MODEL_REGISTRY:
        available = ", ".join(settings.MODEL_REGISTRY.keys())
        raise HTTPException(
            status_code=400,
            detail=f"Unknown model '{request.model}'. Available: {available}",
        )

    chunks = pipeline.retrieve(request.query)
    prompt, source_refs = build_prompt(request.query, chunks)
    answer, model_used = generate_answer(prompt, model_name=request.model)

    return QueryResponse(
        answer=answer,
        sources=[SourceRef(**ref) for ref in source_refs],
        model_used=model_used,
    )
