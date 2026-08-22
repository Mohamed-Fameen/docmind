"""
Phase 3 — Embeddings & Vector Store

Embeds every chunk from Phase 2 using BAAI/bge-base-en-v1.5 (GPU-accelerated if available)
and indexes them into Qdrant for similarity search.

Key design choices (see docs/03-embeddings.md for the full reasoning):
1. We embed `heading_path + chunk text` together, not just the raw chunk text — a chunk
   that reads "It's recommended you use a controller instead" is meaningless alone, but
   "Pods > Using Pods: It's recommended..." gives the embedding model real context.
2. BGE models are trained with an asymmetric convention: QUERIES get an instruction prefix
   ("Represent this question for retrieving supporting documents: "), passages do NOT. This
   script only embeds passages (documents), so no prefix is added here — but any query-time
   code (Phase 4+) MUST add that prefix, or retrieval quality silently degrades.
3. Vectors are normalized (`normalize_embeddings=True`) so cosine similarity is a simple dot
   product — this is what Qdrant's Cosine distance metric expects.
4. Point IDs are deterministic (UUID5 derived from chunk_id), so re-running this script
   updates existing points instead of creating duplicates.

Usage:
    uv run python ingestion/embed_and_index.py
"""

import argparse
import json
import os
import uuid
from pathlib import Path

import torch
from qdrant_client import QdrantClient
from qdrant_client.models import Distance, PointStruct, VectorParams
from sentence_transformers import SentenceTransformer

# --- Config -------------------------------------------------------------

CHUNKS_PATH = Path("data/processed/chunks.jsonl")

EMBEDDING_MODEL = os.environ.get("EMBEDDING_MODEL", "BAAI/bge-base-en-v1.5")
EMBEDDING_DIM = 768  # bge-base-en-v1.5's fixed output dimension

QDRANT_HOST = os.environ.get("QDRANT_HOST", "localhost")
QDRANT_PORT = int(os.environ.get("QDRANT_PORT", "6333"))
QDRANT_COLLECTION = os.environ.get("QDRANT_COLLECTION", "k8s_docs")

BATCH_SIZE = 64

# Namespace for deterministic point IDs — any fixed UUID works, this just needs to be
# stable across runs so the same chunk_id always maps to the same point ID.
ID_NAMESPACE = uuid.UUID("6f9c1b2e-1e0a-4b8b-9b1a-2f6d3a7c5e10")


def load_chunks() -> list[dict]:
    if not CHUNKS_PATH.exists():
        raise SystemExit(
            f"{CHUNKS_PATH} not found. Run ingestion/chunk_docs.py first (Phase 2)."
        )
    chunks = []
    with CHUNKS_PATH.open(encoding="utf-8") as f:
        for line in f:
            chunks.append(json.loads(line))
    return chunks


def chunk_id_to_point_id(chunk_id: str) -> str:
    """Deterministic UUID from a chunk_id string, so re-runs update rather than duplicate."""
    return str(uuid.uuid5(ID_NAMESPACE, chunk_id))


def build_embedding_text(chunk: dict) -> str:
    """
    What we actually feed the embedding model — heading context + chunk body, not the
    chunk body alone. See module docstring point 1.
    """
    return f"{chunk['heading_path']}: {chunk['text']}"


def ensure_collection(client: QdrantClient, recreate: bool = False):
    existing = [c.name for c in client.get_collections().collections]

    if QDRANT_COLLECTION in existing:
        if recreate:
            print(f"--recreate passed: dropping existing collection '{QDRANT_COLLECTION}' ...")
            client.delete_collection(QDRANT_COLLECTION)
        else:
            print(f"Collection '{QDRANT_COLLECTION}' already exists — reusing it.")
            print(
                "  NOTE: deterministic point IDs only prevent duplicates if chunk_id's "
                "generation scheme hasn't changed. If you've changed chunking logic that "
                "affects chunk_id (not just chunk content), re-run with --recreate for a "
                "guaranteed-clean rebuild, or stale points from the old ID scheme will "
                "linger alongside the new ones — this happened for real during Phase 3 dev "
                "(a chunk_id format fix left the collection with 21,380 points instead of "
                "the expected 11,116, since none of the new IDs matched the old ones)."
            )
            return

    client.create_collection(
        collection_name=QDRANT_COLLECTION,
        vectors_config=VectorParams(size=EMBEDDING_DIM, distance=Distance.COSINE),
    )
    print(f"Created collection '{QDRANT_COLLECTION}' (dim={EMBEDDING_DIM}, distance=Cosine)")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--recreate",
        action="store_true",
        help=(
            "Drop and recreate the Qdrant collection before indexing, for a guaranteed-"
            "clean rebuild. Needed whenever chunk_id's generation logic changed (not just "
            "chunk content) — see ensure_collection() for why this matters."
        ),
    )
    args = parser.parse_args()

    device = "cuda" if torch.cuda.is_available() else "cpu"
    print(f"Using device: {device}")
    if device == "cpu":
        print("  (No GPU detected — embedding will still work, just slower.)")

    print(f"Loading embedding model: {EMBEDDING_MODEL} ...")
    model = SentenceTransformer(EMBEDDING_MODEL, device=device)

    chunks = load_chunks()
    print(f"Loaded {len(chunks)} chunks from {CHUNKS_PATH}")

    texts = [build_embedding_text(c) for c in chunks]

    print(f"Embedding {len(texts)} chunks in batches of {BATCH_SIZE} ...")
    embeddings = model.encode(
        texts,
        batch_size=BATCH_SIZE,
        normalize_embeddings=True,  # required for Qdrant's Cosine distance to behave correctly
        show_progress_bar=True,
        convert_to_numpy=True,
    )

    client = QdrantClient(host=QDRANT_HOST, port=QDRANT_PORT)
    ensure_collection(client, recreate=args.recreate)

    print("Upserting into Qdrant ...")
    points = [
        PointStruct(
            id=chunk_id_to_point_id(chunk["chunk_id"]),
            vector=embedding.tolist(),
            payload={
                "chunk_id": chunk["chunk_id"],
                "source_file": chunk["source_file"],
                "section_heading": chunk["section_heading"],
                "heading_path": chunk["heading_path"],
                "text": chunk["text"],
                "doc_url": chunk["doc_url"],
                "k8s_version": chunk["k8s_version"],
                "content_type": chunk["content_type"],
                "auto_generated": chunk["auto_generated"],
            },
        )
        for chunk, embedding in zip(chunks, embeddings)
    ]

    # Upsert in batches too — a single request with 11k+ points can hit request size limits
    for i in range(0, len(points), BATCH_SIZE):
        batch = points[i : i + BATCH_SIZE]
        client.upsert(collection_name=QDRANT_COLLECTION, points=batch)

    print(f"Indexed {len(points)} points into '{QDRANT_COLLECTION}'.")

    count = client.count(collection_name=QDRANT_COLLECTION, exact=True).count
    print(f"Collection now contains {count} points total.")


if __name__ == "__main__":
    main()
