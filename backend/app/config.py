"""
Centralized settings, loaded from environment variables (.env in dev).

Model configuration is a REGISTRY, not a single dev/prod switch — see MODEL_REGISTRY below.
This is deliberate: the goal isn't just "use Ollama locally, Bedrock in prod", it's to be
able to run the same query against several models (different sizes, different providers)
and compare answers — which matters both for day-to-day dev (fit within your GPU's VRAM by
picking a smaller local model) and for the planned model-comparison eval work (Phase 8+):
quantifying how much a bigger model actually improves answer quality once retrieval is
already doing most of the heavy lifting.
"""

import os

from dotenv import load_dotenv

load_dotenv()


class Settings:
    # Qdrant
    qdrant_host: str = os.environ.get("QDRANT_HOST", "localhost")
    qdrant_port: int = int(os.environ.get("QDRANT_PORT", "6333"))
    qdrant_collection: str = os.environ.get("QDRANT_COLLECTION", "k8s_docs")

    # Embeddings / reranking — same embedding model used at index time (Phase 3) MUST be
    # used at query time too, since a bi-encoder's query and passage vectors only compare
    # meaningfully if they came from the same model.
    embedding_model: str = os.environ.get("EMBEDDING_MODEL", "BAAI/bge-base-en-v1.5")
    reranker_model: str = os.environ.get("RERANKER_MODEL", "BAAI/bge-reranker-base")

    # Retrieval tuning
    dense_top_n: int = 20   # candidates pulled from vector search before fusion
    bm25_top_n: int = 20    # candidates pulled from keyword search before fusion
    fused_top_n: int = 10   # candidates kept after RRF fusion, before reranking
    final_top_k: int = 5    # chunks actually sent to the LLM after reranking

    # --- Generation model registry ------------------------------------------------
    #
    # Each entry is a named, selectable model. `provider` determines which function in
    # llm.py handles it. Add new entries here as new models/providers come online — the
    # rest of the code never hardcodes a specific model, only registry keys.
    #
    # NOTE on the local models: a 3B model (~2GB) coexists in VRAM alongside the embedding
    # model + reranker on a card like the RTX 3050. An 8B model (~4.9GB) does not — this was
    # confirmed the hard way (a real `cudaMalloc failed: out of memory` crash) once all three
    # were asked to share a small GPU. Keep the 3B model as the default for local dev; treat
    # 8B+ models as something to run via a hosted provider (Bedrock) instead of locally,
    # unless you have significantly more VRAM.
    MODEL_REGISTRY: dict[str, dict] = {
        "llama3.2-3b-local": {
            "provider": "ollama",
            # Ollama's default `llama3.2` tag IS the 3B model (the explicit small variant
            # is tagged `:1b`) — there is no separate `llama3.2:3b` tag, which caused a real
            # 404 the first time this was run (`model 'llama3.2:3b' not found`). Run
            # `ollama list` to confirm the exact tag registered on your machine if this
            # still doesn't match.
            "ollama_model": "llama3.2:latest",
            "description": "Fast, low VRAM (~2GB), local. Default for day-to-day dev.",
        },
        "llama3.1-8b-local": {
            "provider": "ollama",
            "ollama_model": "llama3.1:8b",
            "description": (
                "Higher quality, ~4.9GB VRAM — will likely OOM on an RTX 3050 while the "
                "embedding model + reranker are also loaded. Best used when comparing "
                "against the 3B model on a machine with more VRAM, or with embeddings/"
                "reranker temporarily unloaded."
            ),
        },
        "claude-bedrock": {
            "provider": "bedrock",
            # Check https://docs.aws.amazon.com/bedrock/latest/userguide/models-supported.html
            # for current model IDs before deploying — these change as AWS adds/retires
            # model versions, and a stale ID here would fail at request time, not at startup.
            "bedrock_model_id": os.environ.get(
                "BEDROCK_MODEL_ID", "anthropic.claude-3-5-haiku-20241022-v1:0"
            ),
            "bedrock_region": os.environ.get("AWS_REGION", "us-east-1"),
            "description": "Hosted via AWS Bedrock. Requires AWS credentials configured.",
        },
    }

    # Which registry entry to use when a request doesn't specify one.
    default_model: str = os.environ.get("ACTIVE_MODEL", "llama3.2-3b-local")

    # Forces Ollama generation onto CPU rather than GPU. See the comment in llm.py's
    # _generate_with_ollama for why this defaults to True — a real VRAM capacity limit,
    # confirmed via an actual crash, not a cautious guess.
    ollama_force_cpu: bool = os.environ.get("OLLAMA_FORCE_CPU", "true").lower() == "true"


settings = Settings()
