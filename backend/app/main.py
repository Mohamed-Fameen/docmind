"""
DocMind backend — the /query endpoint routes each request through the Phase 5 agent graph
(classify -> direct/clarify/retrieve, with a confidence-based retry loop), which internally
uses Phase 4's retrieval (hybrid search + reranking) and generation (model registry) building
blocks.
"""

import json
import time
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from .agent import build_agent_graph
from .citations import extract_cited_numbers
from .config import settings
from .retrieval import RetrievalPipeline

pipeline = RetrievalPipeline()
agent_graph = None  # built at startup, once the pipeline has loaded — see lifespan()

QUERY_LOG_PATH = Path("data/logs/queries.jsonl")


@asynccontextmanager
async def lifespan(app: FastAPI):
    global agent_graph
    # Load the embedding model, reranker, and BM25 index ONCE at startup — these are
    # expensive to load (seconds) and must not be reloaded on every request.
    pipeline.load()
    agent_graph = build_agent_graph(pipeline)
    QUERY_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    yield


app = FastAPI(title="DocMind API", version="0.1.0", lifespan=lifespan)


class QueryRequest(BaseModel):
    query: str
    model: str | None = None  # registry key from config.MODEL_REGISTRY; defaults if omitted


class SourceRef(BaseModel):
    number: int
    heading_path: str
    doc_url: str
    text_snippet: str  # distinguishes same-heading chunks that are genuinely different content
    cited: bool  # whether the answer text actually referenced this source's [N] number


class QueryResponse(BaseModel):
    answer: str
    sources: list[SourceRef]
    model_used: str
    classification: str  # "direct" | "clarify" | "retrieve" — visible for debugging/eval
    retries: int          # how many times the agent rewrote the query and re-retrieved


def _log_query(
    query_text: str,
    result: dict,
    cited_numbers: set[int],
    latency_seconds: float,
):
    """
    Appends every query to a local JSONL log — free eval fodder for Phase 8's RAGAS work
    and for comparing model quality (e.g. 3B local vs a hosted Bedrock model) later, since
    without this, building a test set means manually recreating queries from memory.
    """
    entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "query": query_text,
        "final_query": result["query"],  # differs from `query` if the agent rewrote it
        "classification": result["classification"],
        "retries": result["retry_count"],
        "model_used": result["model_used"],
        "answer": result["answer"],
        "num_sources_retrieved": len(result["chunks"]),
        "num_sources_cited": len(cited_numbers),
        "cited_numbers": sorted(cited_numbers),
        "latency_seconds": round(latency_seconds, 2),
        "retrieved_heading_paths": [c["heading_path"] for c in result["chunks"]],
    }
    with QUERY_LOG_PATH.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")


@app.get("/health")
def health():
    return {"status": "ok", "phase": "5 — agentic layer"}


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
    threadpool automatically — appropriate here since embedding, BM25 scoring, reranking,
    and LLM generation are all CPU/GPU-bound blocking calls, not I/O-bound async-friendly
    ones. Declaring this `async def` while calling blocking code inside it would instead
    block the whole event loop, stalling every other in-flight request.
    """
    if request.model and request.model not in settings.MODEL_REGISTRY:
        available = ", ".join(settings.MODEL_REGISTRY.keys())
        raise HTTPException(
            status_code=400,
            detail=f"Unknown model '{request.model}'. Available: {available}",
        )

    start = time.monotonic()

    initial_state = {
        "query": request.query,
        "original_query": request.query,
        "model": request.model,
        "classification": "",
        "chunks": [],
        "source_refs": [],
        "answer": "",
        "model_used": "",
        "retry_count": 0,
        "confidence": "",
    }
    result = agent_graph.invoke(initial_state)

    latency = time.monotonic() - start

    cited_numbers = extract_cited_numbers(result["answer"])
    sources = [
        SourceRef(**ref, cited=(ref["number"] in cited_numbers))
        for ref in result["source_refs"]
    ]

    _log_query(request.query, result, cited_numbers, latency)

    return QueryResponse(
        answer=result["answer"],
        sources=sources,
        model_used=result["model_used"],
        classification=result["classification"],
        retries=result["retry_count"],
    )
