# Phase 5 — Agentic Layer

**Status:** done — full graph verified end-to-end on real hardware, including a confirmed
working retry loop
**Date:** <!-- fill in -->

## Goal

Stop treating every query the same way. Phase 4's pipeline always runs retrieve → rerank →
generate, regardless of whether the query needs retrieval at all, and never recovers if the
first retrieval attempt comes back weak. Phase 5 adds a decision layer around that pipeline.

## Why this matters, concretely

- **"thanks!"** doesn't need a hybrid search across 11,116 chunks — that's pure wasted
  latency (and on CPU-bound local generation, latency is already the tightest constraint).
- **"it doesn't work"** is too vague to search meaningfully — retrieval returns *something*,
  and the model may confidently answer a question that was never actually asked.
- A genuinely hard question where the **first retrieval attempt is weak** currently just
  returns a mediocre answer with no recovery — worth trying again with a reformulated query
  before giving up.

## Why LangGraph, not plain if/else

For three branches, a plain Python conditional would work. The reason to use LangGraph
anyway: it models this as an explicit state machine — a shared `state` dict flows through
named **nodes** (plain functions), and **edges** (including conditional ones) decide which
node runs next. This is worth it specifically because of the retry loop: retrieve → check
confidence → rewrite → retrieve again is a genuine *cycle*, not just a branch, and LangGraph
handles that natively (`StateGraph` supports edges looping back to earlier nodes) in a way
that's harder to keep readable as nested if/else. This exact shape (classify → act → verify
→ retry) is standard in production agentic systems generally, not a DocMind-specific pattern.

## The graph

```
classify -> DIRECT     -> direct_reply         -> END
         -> CLARIFY    -> ask_clarification    -> END
         -> RETRIEVE   -> retrieve_and_generate -> check_confidence -> [confident] -> END
                                  ^                                  -> [low, retry 0] -+
                                  +-------------- rewrite_query <-----------------------+
```

Six nodes: `classify`, `direct`, `clarify`, `retrieve_generate`, `check_confidence`,
`rewrite_query`. The retry path is capped at one attempt (`retry_count == 0` check) —
unbounded retries would risk a slow, indefinitely-looping request on top of already-slow
CPU-bound local generation.

## Node 1: classify — routing without a heavyweight decision

A single constrained LLM call: "reply with exactly one word: RETRIEVE, DIRECT, or CLARIFY."
Parsing is deliberately lenient and defaults toward the safe option:

```python
if "DIRECT" in upper: classification = "direct"
elif "CLARIFY" in upper: classification = "clarify"
else: classification = "retrieve"
```

**Why default to `retrieve` on anything ambiguous**, rather than, say, defaulting to
`direct`: an ungrounded direct answer risks confidently stating something wrong (the model's
pretrained knowledge could reflect a stale Kubernetes version's behavior). A retrieval that
turns out to be unnecessary just costs a bit of latency — asymmetric failure costs, so the
default should lean toward the cheaper mistake. This mirrors a lesson already learned the
hard way in Phase 4: small local models don't always follow instructions precisely (recall
the citation-formatting inconsistency), so parsing their output needs to plan for
non-compliance rather than assume a clean one-word reply every time.

## Node 2 & 3: direct reply and clarification

`direct` generates a short, non-technical response with no retrieval at all — appropriate
for greetings/small talk, explicitly instructed not to invent technical Kubernetes claims.

`clarify` is a **canned response, not an LLM call** — deliberately, for two reasons: it's
faster (no generation latency at all), and a clarifying question doesn't need to be
dynamically generated to do its job. A more context-aware clarifying question (referencing
specifically what was ambiguous about the user's message) is a reasonable future enhancement,
noted rather than built now.

## Node 4: retrieve_and_generate — unchanged from Phase 4

This node is exactly Phase 4's pipeline (`pipeline.retrieve` → `build_prompt` →
`generate_answer`), just now called conditionally instead of unconditionally, and callable a
second time on retry with a different query.

## Node 5: check_confidence — a heuristic, not a second LLM judge

This node decides "should we retry?" using two cheap textual signals rather than a second LLM
call asking "was that answer good?":

1. **Uncertainty phrases** — does the answer contain language like "the sources don't
   contain," "not enough information," "cannot determine"? The Phase 4 prompt already
   instructs the model to say so directly when it can't answer from the sources — this
   checks whether it actually did.
2. **Zero citations despite having retrieved chunks** — a sneakier red flag than explicit
   uncertainty language: if 5 chunks were retrieved but the answer cites none of them, that's
   a sign the answer likely isn't well grounded in what was actually retrieved, even if it
   doesn't *sound* uncertain.

**Why not a second LLM call to judge the first one** (a common pattern in more elaborate
agentic systems): latency. Local generation is already CPU-bound and slow (Phase 4's real
VRAM constraint pushed generation off the GPU entirely) — doubling LLM calls on every single
query to get a confidence judgment would roughly double response time for every request, not
just the ones that actually need a retry. A cheap heuristic that catches the two most common
failure signals is a better tradeoff here; revisit if Phase 8's eval shows this heuristic
misses too much.

## Node 6: rewrite_query — the actual recovery step

If confidence is low and no retry has happened yet, this node asks the LLM to rewrite the
original query with more precise Kubernetes terminology, then loops back to
`retrieve_and_generate` with the new query. `original_query` is preserved separately in state
for logging, so it's always possible to see both what was originally asked and what the agent
tried instead.

## What's now visible in the API response that wasn't before

`/query` now returns `classification` and `retries` alongside the answer — not just for
debugging, but because these are exactly the kind of signal the planned model-comparison
work (3B vs larger models) will need: does a bigger model retry less often? Does it classify
more accurately? Without surfacing this, that comparison would have no data to work with.

## Testing approach — what could and couldn't be verified here

The dev sandbox used to write this code has no network access, so `langgraph` couldn't be
installed to actually run the compiled `StateGraph` end-to-end. What *was* verified in
isolation, since this logic doesn't depend on LangGraph itself:
- Classification parsing defaults safely to `retrieve` on ambiguous model output
- The confidence heuristic correctly flags both explicit uncertainty language and the
  subtler "zero citations despite retrieved chunks" case
- Query rewrite quote-stripping

What still needs verifying on real hardware: the actual graph wiring (conditional edges,
the retry loop actually looping and actually stopping after one attempt), and real end-to-end
behavior for all three query types (a greeting, a vague query, and a query that triggers a
genuine retry).

## Results

All four test cases run against real Qdrant + Ollama (`llama3.2-3b-local`):

```
Test 1 — "thanks!"
  classification: direct | retries: 0
  answer: "You're welcome! How can I help you with Kubernetes today?"
  -> correct: no retrieval triggered, fast canned-feeling response

Test 2 — "it doesn't work"
  classification: clarify | retries: 0
  answer: (canned clarifying question)
  -> correct: no retrieval, no LLM call at all for this node

Test 3 — "how do I set the current context in kubeconfig"
  classification: retrieve | retries: 0
  answer: correctly cited [1] (kubectl config use-context > Synopsis), sources 2-5
          correctly marked cited: false
  -> matches Phase 4's working behavior exactly

Test 4 — "how do I bake a chocolate cake"
  classification: clarify | retries: 0
  -> SEE "A real gap" BELOW — this is not actually correct, just coincidentally reasonable

Test 5 — "how do I configure the QuantumScheduler feature in Kubernetes 1.34" (QuantumScheduler
          is not a real Kubernetes feature — deliberately fabricated to force a low-confidence
          case)
  classification: retrieve | retries: 1
  answer: "Unfortunately, the provided sources do not contain information on configuring
          QuantumScheduler... you may need to look elsewhere."
  sources: 5 retrieved, all genuinely about scheduler configuration (topically close, since
           dense+BM25 correctly found the closest real content), all correctly marked
           cited: false
  -> FULL retry loop validated: first attempt came back uncertain, rewrite_query fired,
     retrieve_generate ran a second time, check_confidence ran again, retry_count==1 correctly
     routed to "done" instead of a second retry. Critically: even after retrying, the model
     still correctly declined to hallucinate a fabricated feature — the Phase 4 grounding
     instructions held up under the added agentic pressure to "try harder" and produce an
     answer.
```

## A real gap, found and documented rather than glossed over

Test 4 ("how do I bake a chocolate cake") landed on `clarify`, which produces a
reasonable-*looking* response (it redirects back to Kubernetes topics) — but for the wrong
reason. The classifier only has three categories: RETRIEVE, DIRECT, CLARIFY. There's no
category for "clearly off-topic, not vague at all." The model apparently folded
"unrelated-to-Kubernetes" into "vague" by necessity, since neither of the other two labels
fit either. The output happens to be defensible, but the underlying classification was
arguably wrong, not just imprecisely worded — worth being honest about rather than treating
the coincidentally-fine output as evidence the classifier handled this correctly.

**Not fixing this now** — a fourth category (`OFF_TOPIC` or similar) is a reasonable future
addition, but three categories were enough to unblock the actual goal of this phase (prove
the classify → route → retry mechanism works at all), and expanding categories is exactly
the kind of thing worth validating against a broader test set in Phase 8 rather than
guessing at from one example.

## Finding 2: same-heading chunks were indistinguishable in the API response — fixed

Test 5's sources included two entries with byte-identical `heading_path` and `doc_url`
("Configure Multiple Schedulers > Define a Kubernetes Deployment for the scheduler").
Diagnosed against the real chunk data rather than assumed:

```
chunk_id: ...configure-multiple-schedulers.md::s3::...::0  (piece 0)
chunk_id: ...configure-multiple-schedulers.md::s3::...::1  (piece 1)
```

Not a retrieval bug — Phase 2 legitimately split one long section into two pieces, and the
reranker correctly found both relevant. But the API gave no way to tell the two sources apart
without reading full chunk text. **Fixed**: `SourceRef` now includes a `text_snippet` (first
150 chars of the chunk), so two same-heading sources are visibly distinguishable — this also
happens to be exactly what a Phase 7 citation hovercard will need, so building it now avoids
redoing this work later.

## Known limitations / things to revisit

- The classify step adds one more LLM call to every single query (even ones that end up
  needing retrieval) — worth measuring in Phase 8 whether this latency cost is worth the
  benefit for queries that were always going to need retrieval anyway. A cheaper heuristic
  pre-filter (e.g. message length, presence of a question mark) could potentially skip the
  LLM classification call for obvious cases.
- The confidence heuristic is pattern-matching on English phrases the model was prompted to
  use — if the underlying prompt wording changes, this heuristic needs to change with it, or
  it'll silently stop catching real low-confidence cases.
- Only one retry is allowed, and it always uses the same rewrite strategy (ask for more
  precise terminology). A more sophisticated version could try a genuinely different
  strategy on retry (e.g. broaden instead of narrow, or fall back to BM25-only search).

## Deliverable

- [x] `backend/app/agent.py` — LangGraph state machine (classify, direct, clarify,
      retrieve+generate, check confidence, rewrite query)
- [x] `backend/app/citations.py` — shared citation-parsing logic (used by both the API layer
      and the agent's confidence check)
- [x] `backend/app/main.py` updated to route through the agent graph
- [x] Node logic (classification parsing, confidence heuristic, rewrite parsing) unit-tested
      in isolation
- [x] Full graph run against real Qdrant + Ollama on actual hardware — all 5 test cases,
      including a deliberately engineered retry-loop test that confirmed the cycle fires and
      terminates correctly
- [x] Two real findings from testing: a classifier category gap (documented, not yet fixed)
      and a source-display ambiguity (fixed with `text_snippet`)
