# Phase 4 — Backend RAG Core

**Status:** done — end-to-end verified with a real query, correctly grounded answer
**Date:** <!-- fill in -->

## Goal

Tie Phase 3's retrieval (dense vector search) together with keyword search and reranking,
then generate an actual grounded answer — the first point where DocMind answers a real
question end-to-end, not just returns raw chunks.

## The full request lifecycle

```
query
  │
  ├─► dense_search   (Qdrant, top 20)  ─┐
  ├─► bm25_search     (in-memory, top 20)─┤─► reciprocal_rank_fusion (top 10)
  │                                       │
  │                                       ▼
  │                                  rerank (cross-encoder, top 5)
  │                                       │
  │                                       ▼
  │                                 build_prompt (chunks + citation numbers)
  │                                       │
  │                                       ▼
  └──────────────────────────────► generate_answer (Ollama dev / Claude prod)
                                         │
                                         ▼
                                answer + source list
```

## Why hybrid search (dense + BM25), not dense alone

Dense (vector) search is good at *meaning*, bad at *exactness*. A query like "what does
`--allow-metric-labels` do" asks the embedding model to represent an exact flag name as a
vector — vector similarity can drift, and a genuinely different-but-related flag might score
close enough to get retrieved instead.

**BM25** (the modern refinement of TF-IDF) is the opposite: it scores chunks by how often
query terms literally appear, weighted by how rare each term is across the whole corpus (so
"the" doesn't dominate) and normalized by document length. It has zero concept of meaning —
purely statistical term matching — but if `--allow-metric-labels` appears verbatim in a
chunk, BM25 finds it almost perfectly regardless of how the query is phrased.

Running both and merging catches both failure modes: paraphrased/conceptual queries (dense
wins) and exact-term lookups like flag names, error codes, API field names (BM25 wins).

## Why Reciprocal Rank Fusion (RRF) to merge them

Dense scores (cosine similarity, roughly 0-1) and BM25 scores (unbounded, corpus-dependent)
live on completely incomparable scales — you can't just average them. RRF sidesteps this by
using **rank position** instead of raw score: `score = Σ 1/(k + rank)` across all the ranked
lists a chunk appears in (k=60 is a standard constant that flattens the curve so rank 1 vs
rank 2 doesn't dominate too aggressively).

Verified with a hand-checkable example before trusting it on real data:
```
dense = ['A', 'B', 'C', 'D']   # A is #1
bm25  = ['B', 'C', 'A', 'E']   # B is #1

fused = ['B', 'A', 'C', 'D', 'E']
```
B wins even though A was #1 in dense search — because B was strong in *both* lists (#2
dense, #1 BM25), while A was strong in only one. That's the actual point of fusion: reward
consistency across retrieval methods, not just a single method's top pick. Manual math:
`A = 1/61 + 1/63 = 0.03227`, `B = 1/62 + 1/61 = 0.03252` — B edges out A by a small but
consistent margin, exactly matching the fused output order.

## Why reranking — and why it's a structurally different kind of model

This directly follows up on Phase 3's "StatefulSet" finding, where dense search alone
returned narrow API sub-field definitions instead of the conceptual answer.

- **Bi-encoder** (our embedding model, `bge-base-en-v1.5`): encodes the query and each chunk
  *completely separately*, then compares the resulting vectors. This is what makes search
  over 11,116 chunks fast — every chunk's vector was precomputed once, back in Phase 3.
- **Cross-encoder** (our reranker, `bge-reranker-base`): encodes the query *and* a candidate
  chunk *together*, in one pass, so the model can directly attend between the two texts.
  Much more accurate at judging true relevance — but slow, since nothing about it can be
  precomputed; it has to run fresh for every (query, candidate) pair.

This is why reranking only ever runs on a small candidate set (10, after RRF fusion), not
the whole corpus — cheap retrieval narrows the field, expensive reranking picks the true
best from what's left. This exact pattern (bi-encoder for cheap recall, cross-encoder for
precision) is standard in production retrieval systems, not a DocMind-specific choice.

## Prompt design — the actual anti-hallucination mechanism

Retrieval alone doesn't stop an LLM from making things up — the prompt has to explicitly
constrain it. `build_prompt` numbers each retrieved chunk (`[1]`, `[2]`, ...) and instructs
the model to (a) answer only from the provided sources, (b) cite them inline by number, and
(c) say so directly if the sources don't have the answer, rather than falling back to
whatever the model "remembers" from pretraining (which could easily be a stale or wrong
Kubernetes version's behavior).

## Dev/prod LLM swap — revised to a model registry after a real VRAM crash

The original design was a simple `ENV=dev` → Ollama, `ENV=prod` → Anthropic API branch.
Running it for real exposed why that's too rigid: with `bge-base-en-v1.5` and
`bge-reranker-base` both resident in VRAM, asking Ollama to *also* load `llama3.1:8b`
produced a real crash — `cudaMalloc failed: out of memory` — on an RTX 3050. The actual fix
(use a smaller local model) is simple, but it exposed a real requirement: **generation
models need to be switchable, not hardcoded to one per environment**, both to work around
hardware constraints during dev and to support comparing model quality later (see README's
Roadmap section for the planned 3B-vs-8B+ eval work).

`config.py`'s `MODEL_REGISTRY` replaces the dev/prod branch with named, selectable entries:

```python
MODEL_REGISTRY = {
    "llama3.2-3b-local": {"provider": "ollama", "ollama_model": "llama3.2:3b", ...},
    "llama3.1-8b-local":  {"provider": "ollama", "ollama_model": "llama3.1:8b", ...},
    "claude-bedrock":     {"provider": "bedrock", "bedrock_model_id": "...", ...},
}
```

`generate_answer(prompt, model_name=None)` resolves `model_name` (or the configured
default) against the registry and dispatches to the matching provider function, returning
`(answer, model_name_used)` — the model name is always returned alongside the answer, since
any future comparison work needs to know exactly which model produced which answer, not just
what was requested. `/query` accepts an optional `"model"` field to override the default per
request, and `GET /models` lists everything currently registered.

**Bedrock uses the Converse API**, not the older model-specific `invoke_model` API — Converse
gives a single request/response shape across model families (Anthropic, Llama, etc. on
Bedrock), so `_generate_with_bedrock` doesn't need per-model-family branching. AWS
credentials are picked up via boto3's default credential chain (env vars, `~/.aws/
credentials`, or an IAM role) — deliberately not handled explicitly in application code.

**Local model VRAM budgeting, empirically confirmed:** a 3B model (~2GB) coexists in VRAM
alongside the embedding model + reranker on an RTX 3050; an 8B model (~4.9GB) does not — this
isn't a theoretical guideline, it's the exact crash observed. Treat local 8B+ models as
something to run when embeddings/reranker are unloaded, or on a machine with more VRAM, not
as a default alongside the rest of the pipeline.

## A FastAPI detail worth understanding, not just copying

`/query`'s handler is a plain `def`, not `async def`. FastAPI runs synchronous route
functions in a thread pool automatically. This matters here because embedding, BM25 scoring,
and cross-encoder reranking are all CPU/GPU-bound *blocking* calls — declaring the route
`async def` while calling blocking code inside it would block the single event loop thread,
stalling every other in-flight request on the server, not just the current one. This is a
common production FastAPI mistake, not a stylistic detail.

## Startup-time model loading

`RetrievalPipeline.load()` runs once via FastAPI's `lifespan` context manager, not per
request — it loads the embedding model, the reranker, and builds the BM25 index (from
`data/processed/chunks.jsonl`, same source as Phase 3's embedding step). Reloading these on
every request would add seconds of latency to every single query.

## How to run

```bash
# 1. Make sure Qdrant is up and populated (Phase 3)
docker compose -f docker/docker-compose.yml up -d

# 2. Make sure Ollama is running with the model pulled (Phase 0)
ollama pull llama3.1:8b

# 3. Start the backend
uv run uvicorn backend.app.main:app --reload

# 4. Test it
curl -X POST http://localhost:8000/query \
  -H "Content-Type: application/json" \
  -d '{"query": "how do I set the current context in kubeconfig"}'
```

## Results

```
Sample query: "how do I set the current context in kubeconfig"

Sample answer: "You can set the current context in kubeconfig using the command
`kubectl config use-context CONTEXT_NAME`. This is specified in [1]."

Sources returned (5, all correctly topical — no irrelevant results):
  [1] kubectl config use-context > Synopsis           (the exact correct answer)
  [2] Command line tool (kubectl) > ...currently selected context
  [3] kubectl config rename-context > Synopsis
  [4] kubectl config set-context > Options
  [5] kubectl config set-context > Synopsis

model_used: "llama3.2-3b-local"
Generation: CPU (OLLAMA_FORCE_CPU=true) — see VRAM section below for why
```

Answer is correctly grounded (cites [1], which is genuinely the right source) and doesn't
pad with invented details. All 5 retrieved chunks are topically tight — the hybrid search +
RRF + reranking pipeline correctly identified this as a narrow, specific lookup query and
returned narrow, specific results (contrast with Phase 3's "StatefulSet" query, which needed
reranking specifically to *avoid* over-narrow results for a broad conceptual question — both
behaviors being correct for their respective query types is a good sign the pipeline is
doing real work, not just returning whatever's closest in one dimension).

## Real-world VRAM debugging (worth understanding, not just the fix)

Getting this pipeline running end-to-end surfaced a genuine hardware constraint, diagnosed
in stages rather than guessed at:

1. First crash: `llama3.1:8b` (the default at the time) OOM'd — `cudaMalloc failed: out of
   memory`. Reasonable first guess: 8B is just too big for a small GPU.
2. Switched default to a 3B model (`llama3.2:latest`) — but it **also** OOM'd, which
   contradicted the "8B is just big" theory and demanded an actual measurement rather than
   another guess.
3. `nvidia-smi` confirmed the real constraint: an RTX 3050 **4GB** (laptop variant), with
   `bge-base-en-v1.5` + `bge-reranker-base` alone already consuming **1747MiB** — meaning a
   3B model (needing ~1.8GB+ for weights alone, before KV cache) genuinely didn't have room,
   independent of which model was chosen.
4. **Fix:** force Ollama generation onto CPU (`options={"num_gpu": 0}` in the `ollama.chat`
   call), keeping the embedding model + reranker on GPU where they matter most for latency
   (they run on every single query; generation happens once per query and tolerates being
   slower). This isn't a workaround for a mistake — it's the correct architecture for this
   specific hardware, and it's exactly why local generation is the *dev* path while Bedrock
   is the intended production path (a hosted model doesn't share your GPU at all).

Lesson for interviews: the first crash's obvious explanation ("8B model too big") turned out
to be incomplete — the second crash on a *smaller* model is what actually forced a real
measurement instead of a plausible-sounding assumption, which is the difference between
guessing at a fix and diagnosing a system.

## Enhancements added after real-usage testing

Testing the pipeline with an open-ended query ("Teach me how to learn kubernetes") surfaced
gaps worth closing rather than just noting:

**Citation validation.** The prompt asks the model to cite sources with `[N]`, but "the
prompt asks for it" isn't the same as "the model does it correctly." A real test case showed
the model citing sources `[2]`, `[2, 3]`, and `[3, 4]` while never citing `[1]` — a plausible,
relevant source that just went unused. `/query` now parses the answer's actual citations and
returns a `cited: bool` flag per source, so retrieved-but-ignored sources are visible instead
of silently indistinguishable from ones that genuinely informed the answer. Building this
caught its own bug immediately: the first version of the citation regex only matched
single-number brackets (`[2]`) and silently missed grouped citations (`[2, 3]`) — caught by
testing against the real logged answer rather than a hand-picked clean example, the same
pattern that's caught every other real bug in this project so far.

**Query logging.** Every `/query` call now appends to `data/logs/queries.jsonl` — timestamp,
query, model used, answer, retrieved heading paths, citation counts, latency. This is
deliberately built now rather than deferred to Phase 8, since an eval harness needs a corpus
of real query/answer pairs to work with, and manually recreating test queries later throws
away exactly the kind of real, sometimes-surprising examples (like the one above) that make
for a better eval set than hand-picked "easy" questions.

## Backlog — considered, not yet implemented

- **Stronger anti-hallucination prompt wording.** Current instruction says "answer using
  ONLY the sources below" — worth testing whether an explicit rule like "if you mention a
  specific named resource (a course, tool, or tutorial), it must appear verbatim in the
  sources above" measurably reduces ungrounded specifics, once Phase 8's eval can actually
  measure "measurably" rather than spot-checking one query at a time.
- **LLM call timeouts.** Neither the Ollama nor Bedrock call currently has an explicit
  timeout — a hung local model or a slow Bedrock response would block that request
  indefinitely. Worth adding once real latency numbers (from the new query log) show what a
  reasonable timeout threshold actually is, rather than guessing a number.
- **Per-stage latency instrumentation.** Right now only total request latency is logged —
  breaking this down by stage (dense search / BM25 / fusion / rerank / generation) would
  show where time actually goes, which matters more once comparing local CPU generation
  against hosted Bedrock latency.
- **Out-of-domain decline test.** Not yet verified: does the pipeline correctly say "I don't
  know" for a clearly out-of-scope query (e.g. "how do I bake a cake"), or does a small local
  model ignore the grounding instruction and hallucinate a plausible-sounding non-answer?
  Worth a specific test case, not an assumption either way.

- No streaming yet — the whole answer comes back in one response. Streaming is planned for
  Phase 7 (frontend), since it matters most for perceived responsiveness in a chat UI.
- No conversation memory yet — every `/query` call is stateless, no session/history. Phase 6.
- No agentic control flow yet — the pipeline always retrieves, even for questions that don't
  need it (e.g. "what's 2+2" would still trigger a full hybrid search + rerank + LLM call).
  Phase 5 adds the decision layer that can skip retrieval or re-retrieve on low confidence.
- `dense_top_n`, `bm25_top_n`, `fused_top_n`, `final_top_k` (20/20/10/5) are reasonable
  starting guesses, not tuned values — Phase 8's RAGAS eval is where these actually get
  validated against real query/answer pairs.

## Deliverable

- [x] `backend/app/config.py` — centralized settings + model registry
- [x] `backend/app/retrieval.py` — hybrid search + RRF fusion + reranking
- [x] `backend/app/llm.py` — prompt construction + registry-based provider dispatch
- [x] `backend/app/main.py` — `/query` and `/models` FastAPI endpoints
- [x] RRF fusion logic verified with a hand-checkable example
- [x] End-to-end run against real Qdrant + Ollama — working, correctly grounded answer
- [x] Results filled in above
- [x] Real VRAM constraint diagnosed and resolved (CPU-forced local generation)
