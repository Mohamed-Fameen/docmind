# Phase 3 — Embeddings & Vector Store

**Status:** scripts written, pending run against real corpus
**Date:** <!-- fill in -->

## Goal

Turn the 11,116 chunks from Phase 2 into searchable vectors, indexed in Qdrant, so that a
natural-language query can retrieve the most relevant chunks by meaning rather than keyword
overlap.

## Model choice: `BAAI/bge-base-en-v1.5`

- Open-source, runs locally on the RTX 3050 — zero API cost, no sending K8s docs to a
  third-party embedding API.
- Strong MTEB (retrieval benchmark) performance for its size class — "base" is a deliberate
  middle ground: `bge-large` is marginally better but slower/heavier, `bge-small` would give
  up retrieval precision we'd likely notice.
- English-only, matching our corpus (no need to pay the capacity cost of a multilingual model).
- Fixed output dimension: 768 numbers per chunk, regardless of chunk length.

## Design decisions

### 1. What text actually gets embedded

We embed `heading_path + chunk text` together (e.g. `"Pods > Using Pods: It's recommended
you use a controller instead."`), not the raw chunk text alone. A chunk's text can be
context-dependent — "It's recommended you use a controller instead" means little without
knowing it's talking about Pods. Prepending the heading path gives the embedding model the
context a human reader would get implicitly from seeing the page structure.

### 2. The query/passage asymmetry — BGE-specific, easy to miss

BGE models are trained with an asymmetric convention:
- **Passages/documents** (what we're indexing) → embedded with no special prefix.
- **Queries** (what a user asks at retrieval time) → embedded WITH the instruction prefix
  `"Represent this sentence for searching relevant passages: "`.

This isn't a stylistic choice — it's literally how the model was trained, and skipping it on
the query side doesn't crash anything, it just silently produces worse retrieval, since the
query vector and passage vectors are no longer in the representation space the model
actually learned to align. `embed_and_index.py` embeds passages (no prefix);
`test_retrieval.py` embeds queries (with prefix). Any future query-time code (Phase 4's
FastAPI endpoint) must follow the same rule.

### 3. Normalized vectors + Cosine distance

`normalize_embeddings=True` scales every vector to unit length. Combined with Qdrant's
`Distance.COSINE`, this means similarity is purely about the *direction* (meaning) of the
vector, not its magnitude — two chunks about the same topic should score as similar
regardless of how long either one is.

### 4. Deterministic point IDs

Each chunk's Qdrant point ID is a UUID5 derived from its `chunk_id` (not a random UUID or a
sequential counter). This means re-running the embedding script updates existing points
instead of creating duplicates — important since we'll likely re-embed after chunking
changes, or after swapping embedding models later.

## Pipeline

`ingestion/embed_and_index.py`:
1. Load all chunks from `data/processed/chunks.jsonl` (Phase 2's output).
2. Build the embedding text (heading_path + chunk text) for each chunk.
3. Batch-embed on GPU (`batch_size=64` — small enough to fit comfortably in the RTX 3050's
   VRAM, large enough to actually use its parallelism; a batch size of 1 would waste the GPU).
4. Create the Qdrant collection if it doesn't exist yet (768-dim, Cosine distance).
5. Upsert all points (vector + metadata payload) in batches — batching the upsert too,
   since a single request with 11k+ points risks hitting request size limits.

`ingestion/test_retrieval.py`:
- Embeds a handful of hand-picked test queries (with the query instruction prefix) and prints
  the top 3 Qdrant results for each, so we can eyeball whether retrieval is actually working
  before building the FastAPI layer on top of it.

## How to run

```bash
# Make sure Qdrant is running (from Phase 0's docker-compose)
docker compose -f docker/docker-compose.yml up -d

# Embed and index all chunks
uv run python ingestion/embed_and_index.py

# Sanity-check retrieval quality
uv run python ingestion/test_retrieval.py
```

## Results

```
Chunks embedded:           11,116
Embedding time (GPU):      ~3 minutes (174 batches of 64, RTX 3050)
Points in Qdrant collection: 11,116 (confirmed exact match after chunk_id uniqueness fix
                              — see "chunk_id collision" note below)
```

### Test retrieval spot-check

3 of 4 hand-picked test queries retrieved excellent top results:

- **"how do I set the current context in kubeconfig"** → top result was the exact
  `kubectl config use-context` synopsis (score 0.885).
- **"what does the --feature-gates flag do"** → top two results were the actual Feature
  Gates overview page and its glossary definition.
- **"how do I create a pod"** → top result was the Pods concept page's worked example with
  the actual YAML.

One query exposed a real limitation worth tracking rather than silently patching:

- **"what is a StatefulSet used for"** → top 3 were all narrow auto-generated API reference
  fragments (`StatefulSetList`, `StatefulSetStatus`, a feature-gate description) — none of
  which actually explain what a StatefulSet is *for*. The real answer (the conceptual
  overview page) didn't make the top 3.

**Why:** pure dense vector search has no notion of "this chunk is a conceptual explanation"
vs. "this chunk is a narrow field definition" — it only sees embedding similarity. A short,
topically-concentrated API reference chunk can out-score a longer conceptual explanation on
raw cosine similarity, even when it's the wrong *kind* of answer for the question asked. This
is exactly the gap hybrid search + reranking (Phase 4) is meant to close — a cross-encoder
reranker reads the query and candidate together and can recognize "this is a sub-field
definition, not the concept" in a way vector similarity alone can't. Worth revisiting in
Phase 8's eval to confirm reranking actually fixes this specific case, not just assuming it
will.

## Bug found: chunk_id collisions (surfaced here, root-caused back in Phase 2)

First indexing run: 11,116 chunks embedded, but only 10,264 points landed in Qdrant. Root
cause: `chunk_id` was built from `{file}::{heading}::{piece_index}`, which isn't unique when
a file has two different sections sharing an identical heading name (common for generic
subsection titles like "See Also" or "Examples" repeated across different topics within one
long page). Two different chunks produced the same `chunk_id`, hence the same deterministic
Qdrant point ID, and the second silently overwrote the first — 852 chunks lost with zero
errors. **Fixed** by including each section's position within the file (`s{section_idx}`) in
the ID, which is guaranteed unique regardless of heading text repetition. Verified with a
synthetic file containing two genuinely different sections both titled "See Also."

**Second-order lesson:** deterministic point IDs only protect against duplicates if the ID
*generation scheme* itself doesn't change. Since the chunk_id format changed for every chunk
(not just the 852 that collided), a follow-up re-embed without clearing the collection first
left 21,380 points — the old scheme's 10,264 plus the new scheme's 11,116, with nothing
actually overwritten, since no new ID matched any old one. Added a `--recreate` flag to
`embed_and_index.py` for exactly this situation: whenever chunk_id generation logic changes,
not just chunk content, the collection needs a clean rebuild, not an incremental upsert.

## Known limitations / things to revisit

- No hybrid search yet (dense vector only, no BM25/keyword search) — pure-semantic search
  can occasionally miss exact-match queries like specific flag names or error codes, where
  keyword matching would actually do better than embeddings. This gets addressed in Phase 4.
- No reranking yet — top-k vector search results aren't being re-scored by a cross-encoder.
  The "StatefulSet" test query above is a concrete example of why this matters, not just a
  theoretical concern. Also Phase 4.
- Embedding text currently only uses `heading_path + text`. Worth revisiting whether
  including `content_type` or `source_file` context in the embedded text (not just as
  metadata) would help — but that's a Phase 8 (eval) question, not a Phase 3 one.
- Possible retrieval-time improvement worth testing in Phase 8: filtering or boosting by
  `content_type` (e.g. prefer `concept`/`task` chunks over `auto_generated` reference chunks
  for broad "what is X" style questions, and the reverse for specific flag/field lookups).

## Deliverable

- [x] `ingestion/embed_and_index.py` written, with a `--recreate` flag for clean rebuilds
- [x] `ingestion/test_retrieval.py` written
- [x] Run against the real 11,116-chunk corpus — confirmed exact point count match
- [x] Results filled in above
- [x] Test retrieval spot-checked for quality — 3/4 excellent, 1/4 exposed a real limitation
      (documented above) worth revisiting in Phase 8
