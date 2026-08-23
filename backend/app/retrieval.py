"""
Retrieval pipeline: dense (vector) search + sparse (BM25) search, fused with Reciprocal
Rank Fusion, then narrowed further with a cross-encoder reranker.

See docs/04-backend.md for the reasoning behind each stage. Summary:
- Dense search catches semantic/paraphrased matches, misses exact terms (flag names, codes).
- BM25 catches exact terms, misses paraphrases — the two cover each other's blind spots.
- RRF merges two differently-scaled ranking systems using rank position, not raw scores.
- Reranking is a slower but far more accurate final pass over a small candidate set — a
  cross-encoder looks at the query and chunk TOGETHER, unlike the bi-encoder embeddings
  used for the fast initial search.
"""

import json
import re
from pathlib import Path

from qdrant_client import QdrantClient
from rank_bm25 import BM25Okapi
from sentence_transformers import CrossEncoder, SentenceTransformer

from .config import settings

CHUNKS_PATH = Path("data/processed/chunks.jsonl")
QUERY_INSTRUCTION = "Represent this sentence for searching relevant passages: "

_TOKEN_RE = re.compile(r"[a-z0-9]+")


def _tokenize(text: str) -> list[str]:
    return _TOKEN_RE.findall(text.lower())


class RetrievalPipeline:
    """
    Holds everything that's expensive to load (embedding model, reranker, BM25 index) so
    it's built once at app startup, not per-request. See main.py's lifespan handler.
    """

    def __init__(self):
        self.chunks_by_id: dict[str, dict] = {}
        self.chunk_ids_ordered: list[str] = []
        self.bm25: BM25Okapi | None = None
        self.embedder: SentenceTransformer | None = None
        self.reranker: CrossEncoder | None = None
        self.qdrant: QdrantClient | None = None

    def load(self):
        print("Loading retrieval pipeline...")

        chunks = []
        with CHUNKS_PATH.open(encoding="utf-8") as f:
            for line in f:
                chunks.append(json.loads(line))

        self.chunks_by_id = {c["chunk_id"]: c for c in chunks}
        self.chunk_ids_ordered = [c["chunk_id"] for c in chunks]

        print(f"  Building BM25 index over {len(chunks)} chunks...")
        tokenized_corpus = [_tokenize(f"{c['heading_path']} {c['text']}") for c in chunks]
        self.bm25 = BM25Okapi(tokenized_corpus)

        print(f"  Loading embedding model: {settings.embedding_model}")
        self.embedder = SentenceTransformer(settings.embedding_model)

        print(f"  Loading reranker: {settings.reranker_model}")
        self.reranker = CrossEncoder(settings.reranker_model)

        self.qdrant = QdrantClient(host=settings.qdrant_host, port=settings.qdrant_port)

        print("Retrieval pipeline ready.")

    def dense_search(self, query: str, top_n: int) -> list[str]:
        """Returns chunk_ids ranked by vector similarity. Query gets the BGE instruction
        prefix; passages were indexed WITHOUT it (Phase 3) — this asymmetry is required,
        not optional, for the two sides to compare meaningfully."""
        query_vector = self.embedder.encode(
            QUERY_INSTRUCTION + query, normalize_embeddings=True
        )
        results = self.qdrant.query_points(
            collection_name=settings.qdrant_collection,
            query=query_vector.tolist(),
            limit=top_n,
        ).points
        return [r.payload["chunk_id"] for r in results]

    def bm25_search(self, query: str, top_n: int) -> list[str]:
        """Returns chunk_ids ranked by BM25 keyword score."""
        scores = self.bm25.get_scores(_tokenize(query))
        # argsort descending, take top_n
        top_indices = sorted(range(len(scores)), key=lambda i: -scores[i])[:top_n]
        return [self.chunk_ids_ordered[i] for i in top_indices]

    @staticmethod
    def reciprocal_rank_fusion(
        ranked_lists: list[list[str]], k: int = 60, top_n: int = 10
    ) -> list[str]:
        """
        Merge multiple ranked chunk_id lists into one, using rank position rather than
        raw scores — necessary because dense (cosine similarity) and BM25 scores live on
        completely different, incomparable scales.
        """
        scores: dict[str, float] = {}
        for ranked_list in ranked_lists:
            for rank, chunk_id in enumerate(ranked_list, start=1):
                scores[chunk_id] = scores.get(chunk_id, 0.0) + 1.0 / (k + rank)
        fused = sorted(scores, key=lambda cid: -scores[cid])
        return fused[:top_n]

    def rerank(self, query: str, candidate_ids: list[str], top_k: int) -> list[dict]:
        """
        Re-scores candidates with a cross-encoder that sees the query and chunk text
        together, then returns the top_k full chunk dicts (with a 'rerank_score' added).
        This is the expensive step — that's exactly why it only runs on ~10 candidates,
        not all 11,116 chunks.
        """
        candidates = [self.chunks_by_id[cid] for cid in candidate_ids]
        pairs = [(query, c["text"]) for c in candidates]
        scores = self.reranker.predict(pairs)

        scored = list(zip(candidates, scores))
        scored.sort(key=lambda pair: -pair[1])

        results = []
        for chunk, score in scored[:top_k]:
            chunk_with_score = {**chunk, "rerank_score": float(score)}
            results.append(chunk_with_score)
        return results

    def retrieve(self, query: str) -> list[dict]:
        """Full pipeline: hybrid search -> RRF fusion -> rerank -> top final_top_k chunks."""
        dense_ids = self.dense_search(query, settings.dense_top_n)
        bm25_ids = self.bm25_search(query, settings.bm25_top_n)
        fused_ids = self.reciprocal_rank_fusion(
            [dense_ids, bm25_ids], top_n=settings.fused_top_n
        )
        return self.rerank(query, fused_ids, top_k=settings.final_top_k)
