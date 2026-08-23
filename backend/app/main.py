"""
DocMind backend — the /query endpoint ties retrieval (Phase 3's hybrid search + reranking)
to generation (any model in config.py's MODEL_REGISTRY) into one RAG request.
"""

import json
import re
import time
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from .config import settings
from .llm import build_prompt, generate_answer
from .retrieval import RetrievalPipeline

pipeline = RetrievalPipeline()

QUERY_LOG_PATH = Path("data/logs/queries.jsonl")
CITATION_RE = re.compile(r"\[(\d+(?:\s*,\s*\d+)*)\]")


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Load the embedding model, reranker, and BM25 index ONCE at startup — these are
    # expensive to load (seconds) and must not be reloaded on every request.
    pipeline.load()
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
    cited: bool  # whether the answer text actually referenced this source's [N] number


class QueryResponse(BaseModel):
    answer: str
    sources: list[SourceRef]
    model_used: str


def _extract_cited_numbers(answer: str) -> set[int]:
    """
    Parses citation numbers out of the answer text, e.g. [2] or [2, 3]. This exists because
    "the prompt asks the model to cite sources" is not the same as "the model actually did"
    — especially with smaller local models, which don't always follow instructions
    perfectly. Surfacing which retrieved sources were actually cited (vs retrieved but
    ignored) makes that gap visible instead of silently trusting the prompt did its job.

    Handles multi-number citations like "[2, 3]" — an earlier version of this regex only
    matched single-number brackets and silently missed grouped citations, which the model
    produces regularly in practice (caught by testing against a real logged answer, not a
    hand-picked example).
    """
    numbers = set()
    for match in CITATION_RE.findall(answer):
        numbers.update(int(n.strip()) for n in match.split(","))
    return numbers


def _log_query(
    query_text: str,
    model_used: str,
    chunks: list[dict],
    answer: str,
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
        "model_used": model_used,
        "answer": answer,
        "num_sources_retrieved": len(chunks),
        "num_sources_cited": len(cited_numbers),
        "cited_numbers": sorted(cited_numbers),
        "latency_seconds": round(latency_seconds, 2),
        "retrieved_heading_paths": [c["heading_path"] for c in chunks],
    }
    with QUERY_LOG_PATH.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")


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

    start = time.monotonic()
    chunks = pipeline.retrieve(request.query)
    prompt, source_refs = build_prompt(request.query, chunks)
    answer, model_used = generate_answer(prompt, model_name=request.model)
    latency = time.monotonic() - start

    cited_numbers = _extract_cited_numbers(answer)
    sources = [
        SourceRef(**ref, cited=(ref["number"] in cited_numbers)) for ref in source_refs
    ]

    _log_query(request.query, model_used, chunks, answer, cited_numbers, latency)

    return QueryResponse(answer=answer, sources=sources, model_used=model_used)
