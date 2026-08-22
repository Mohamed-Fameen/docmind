"""
Phase 3 — Retrieval sanity check

Embeds a handful of test queries and checks that the top results from Qdrant are actually
relevant, before we build anything else on top of this pipeline.

Important asymmetry: BGE models are trained so that QUERIES get an instruction prefix but
passages (documents) do NOT. embed_and_index.py embeds passages with no prefix — this script
must add the prefix on the query side, or the two sides of the retrieval aren't using the
representation the model was actually trained for, which quietly hurts result quality.

Usage:
    uv run python ingestion/test_retrieval.py
"""

import os

import torch
from qdrant_client import QdrantClient
from sentence_transformers import SentenceTransformer

EMBEDDING_MODEL = os.environ.get("EMBEDDING_MODEL", "BAAI/bge-base-en-v1.5")
QDRANT_HOST = os.environ.get("QDRANT_HOST", "localhost")
QDRANT_PORT = int(os.environ.get("QDRANT_PORT", "6333"))
QDRANT_COLLECTION = os.environ.get("QDRANT_COLLECTION", "k8s_docs")

# The specific instruction BGE was trained with for retrieval-style queries.
QUERY_INSTRUCTION = "Represent this sentence for searching relevant passages: "

TEST_QUERIES = [
    "how do I set the current context in kubeconfig",
    "what does the --feature-gates flag do",
    "how do I create a pod",
    "what is a StatefulSet used for",
]


def main():
    device = "cuda" if torch.cuda.is_available() else "cpu"
    model = SentenceTransformer(EMBEDDING_MODEL, device=device)
    client = QdrantClient(host=QDRANT_HOST, port=QDRANT_PORT)

    for query in TEST_QUERIES:
        query_vector = model.encode(
            QUERY_INSTRUCTION + query,
            normalize_embeddings=True,
        )

        results = client.query_points(
            collection_name=QDRANT_COLLECTION,
            query=query_vector.tolist(),
            limit=3,
        ).points

        print("=" * 70)
        print(f"QUERY: {query}")
        print("=" * 70)
        for i, r in enumerate(results, 1):
            print(f"\n[{i}] score={r.score:.4f}  |  {r.payload['heading_path']}")
            print(f"    url: {r.payload['doc_url']}")
            preview = r.payload["text"][:200].replace("\n", " ")
            print(f"    text: {preview}...")
        print()


if __name__ == "__main__":
    main()
