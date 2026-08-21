"""
DocMind backend — placeholder entrypoint for Phase 0.

This just proves the FastAPI app boots and Docker/uv wiring works end-to-end.
Real retrieval logic gets added starting Phase 4.
"""

from fastapi import FastAPI

app = FastAPI(title="DocMind API", version="0.1.0")


@app.get("/health")
def health():
    return {"status": "ok", "phase": "0 — environment setup"}
