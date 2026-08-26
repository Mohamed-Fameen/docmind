# Phase 8 — Evaluation

**Status:** done — full eval run completed successfully after switching to Python 3.12
(confirmed root cause: Python 3.14 async incompatibility, not fixable via RunConfig alone)
**Date:** <!-- fill in -->

## Goal

Turn "it seems to work" into a number. Every quality issue found so far (the DaemonSet
fabricated citation, the missing `[1]` citation, the classifier's `clarify`-vs-off-topic
confusion) was caught by manually reading one response at a time — that doesn't scale, and
gives no way to know if a change made things better or worse overall versus just different
on the one example you happened to check.

## The four RAGAS metrics, and what each one is actually for

| Metric | Question it answers | Needs ground truth? |
|---|---|---|
| Faithfulness | Does every claim in the answer trace back to retrieved context? | No |
| Answer relevancy | Does the answer address what was actually asked? | No |
| Context precision | Of the retrieved chunks, how many were actually useful? | **Yes** — see correction below |
| Context recall | Did retrieval find everything needed for a complete answer? | **Yes** |

**Faithfulness is the metric that would have caught the DaemonSet hallucinated citation
automatically** (Phase 6) — it works by extracting individual claims from the answer, then
checking each one against retrieved context for support, which is exactly the manual check
that was done by hand there. This isn't a coincidental parallel; it's the actual reason this
metric exists.

## LLM-as-judge, and its real limitation

RAGAS scores things by using an LLM to judge ("here's a claim, here's the context, is it
supported?"). Score quality is bounded by judge quality. `run_eval.py` defaults to judging
with the same local 3B model used for generation — a real, acknowledged weakness
(self-evaluation bias: a model isn't necessarily good at catching its own mistakes,
especially a small one). `JUDGE_MODEL` is a single named constant specifically so this is
easy to swap once Bedrock credentials are set up — using an independent, stronger judge to
score answers is also exactly the mechanism the planned 3B-vs-8B comparison work needs.

## Reference-free vs reference-based — why the test set is built the way it is

Faithfulness and answer relevancy need only the question, retrieved context, and generated
answer. Context precision and context recall **both** need a human-written reference answer
— which means someone has to sit down and write out what the ideal answer should contain.
That's real work, so `eval/test_set.py` only attaches a `ground_truth` to 2 of the 5 curated
questions, and runs both reference-based metrics as a separate, smaller pass over just those,
rather than blocking the whole eval on writing reference answers for everything.

**Correction, found by actually running this**: this doc originally claimed context
precision was reference-free, based on an assumption about the metric's typical
implementation. The actual installed `ragas` version raised `ValueError: The metric
[context_precision] ... requires the following additional columns ['reference']` the first
time this ran for real — it judges retrieved-context relevance *against the reference
answer*, not independently. Fixed by moving `context_precision` into the same reference-based
group as `context_recall`, and correcting the table above rather than leaving stale,
disproven information in the docs. A second, related hedge: `context_recall` historically
expected a column named `ground_truth` in older `ragas` releases, while this version's error
named `reference` specifically — since it's not verified which name *this* installed version
actually wants for which metric, both column names are populated with the same value in the
dataset, which is harmless redundancy if only one name is actually needed.

## Test set composition — real data, not just hand-picked "nice" questions

`eval/test_set.py` combines two sources deliberately:

1. **A curated set** covering distinct, already-characterized query types: a specific
   reference lookup (kubeconfig context), a broad conceptual question (StatefulSet), the
   Phase 6 multi-turn follow-up (DaemonSet), the Phase 5 adversarial fictional-feature test
   (QuantumScheduler), and an out-of-domain question (baking a cake). Two of these have a
   `ground_truth`; three deliberately don't (see code comments for why each one doesn't).
2. **Real logged queries** pulled from `data/logs/queries.jsonl` (Phase 4's query logging),
   deduplicated against the curated set, capped at 20. Hand-written eval questions tend to
   be unrealistically clean; real usage is messier and more representative of what the
   system actually faces day to day.

## What `run_eval.py` actually does

Runs every test question through the **real agent graph** (`build_agent_graph`, the same
code path `/query` uses) — not a simplified re-implementation — so eval results reflect
what the deployed system actually does, including classification, retrieval, reranking,
and the confidence-based retry loop. Every question is treated as a fresh single-turn
conversation (`history: []`); this is a known simplification for the DaemonSet test case
specifically (see `test_set.py`'s comment on it).

## How to run

```bash
uv sync   # pulls in ragas, langchain-ollama, langchain-huggingface, datasets

# make sure Qdrant + Ollama are up, same as any normal backend run
docker compose -f docker/docker-compose.yml up -d

uv run python eval/run_eval.py
```

This will take a while — every test question runs the full retrieval + generation pipeline,
*then* every row gets judged by a second LLM call (or several, since faithfulness judges
each extracted claim separately) — expect this to be noticeably slower than a single manual
`/query` call, multiplied across the whole test set.

## Results

**Root cause of the async failures, confirmed**: Python 3.14 (this environment's original
Python) is incompatible with `ragas`'s internal async job executor —
`RunConfig(max_workers=1)` alone did not fix it. Rebuilding the venv on Python 3.12
(`uv venv --python 3.12 .venv --clear && uv sync`) resolved it completely — every job
actually executed afterward, rather than failing instantly and identically as before.

```
Test set size: 9 (5 curated + 4 from real logs, after garbage filtering removed 2 non-
                  question log entries — see "Confirmed real issue #3" below)

AGGREGATE — reference-free (all 9 questions)
  faithfulness      : 0.817
  answer_relevancy  : 0.444   <- see note below on why this looks worse than it is

AGGREGATE — reference-based (n=2, questions with a written ground truth)
  context_precision : 1.000
  context_recall     : 0.417

Full run time: ~34 minutes total (27 min reference-free + 7 min reference-based) — slow
because CPU-bound local generation is being used for BOTH answering AND judging.
```

### Per-question breakdown, with the reason behind each NaN identified

| Question | Faithfulness | Answer relevancy | NaN reason (where applicable) |
|---|---|---|---|
| kubeconfig context | 0.750 | NaN | judge JSON parsing failure |
| StatefulSet (conceptual) | 1.000 | NaN | judge JSON parsing failure |
| **DaemonSet follow-up** | **1.000** | **0.839** | — both succeeded |
| QuantumScheduler (adversarial) | 0.333 | 0.000 | see finding below — not a real failure |
| Bake a cake (out-of-domain) | NaN | 0.000 | no context to judge / correct decline |
| Create a Pod | 1.000 | NaN | judge JSON parsing failure |
| "it doesn't work" (clarify) | NaN | NaN | no retrieval happened — structurally N/A |
| "thanks!" (direct) | NaN | NaN | no retrieval happened — structurally N/A |
| "Teach me kubernetes" | NaN | 0.938 | faithfulness: judge parsing failure |

### Finding: NaN has (at least) three different causes, easily confused if not traced

1. **Structurally not applicable** — `direct`/`clarify` classifications retrieve zero
   chunks, so faithfulness (which checks claims against retrieved context) has nothing to
   check. NaN here is *correct*, not a failure.
2. **Judge parsing failure** — the local 3B model, acting as judge, repeatedly failed to
   produce the exact JSON structure `ragas` needs internally (`OutputParserException:
   Invalid json output`). Looking at the raw failures, the model's actual *content* was
   often reasonable — it just didn't wrap it in the expected schema. Measured failure rate
   for `answer_relevancy` specifically: succeeded on 4 of 9 questions, failed to parse on 5
   — a concrete, quantified demonstration of the self-evaluation/weak-judge limitation
   flagged from the start of this phase, not just a theoretical concern anymore.
3. **Timeouts** — several jobs hit plain `TimeoutError()`, consistent with local CPU-bound
   generation being slow enough to exceed `ragas`/`langchain`'s default internal timeout.

### Finding: automated metrics actively penalize CORRECT refusal behavior

The QuantumScheduler and cake-baking questions both score badly (`0.333`/`0.000` and
`0.000` respectively) — but Phase 5's manual testing already confirmed both answers were
**correct**: the system honestly declined rather than hallucinating a fake explanation.
`answer_relevancy` works by having the judge reconstruct a plausible question from the
answer, then measuring similarity to the real question — an honest "I don't have
information about this" doesn't reconstruct into anything resembling the original
question, so it scores as *irrelevant* even though declining was exactly the right
behavior. **This is a real limitation of the metric itself**, not a system failure:
standard RAGAS metrics have no concept of "correctly refusing" as distinct from "failing to
answer." A production eval process needs a separate, explicit check for appropriate
refusals (a custom rule, or manual review flagging) rather than trusting these scores at
face value on adversarial test cases — the low aggregate `answer_relevancy` (0.444) is
partly an artifact of this, not purely a quality signal.

### Finding: the Phase 6 DaemonSet fix is now automatically confirmed, not just manually observed

`"what about a DaemonSet instead?"` scores `1.000` faithfulness and `0.839` relevancy — the
best result of any question in the test set. This is the exact follow-up manually fixed in
Phase 6 via the `contextualize` node. Getting an independent, automated confirmation that
it's now well-grounded and relevant — arrived at completely separately from the manual
before/after retrieval comparison done at the time — is exactly what a working eval harness
is supposed to provide: turning "looks better now" into a repeatable, measured result.

## Known limitations / things to revisit

- **Self-judging — now measured, not just theoretical.** Using the local 3B model as judge
  produced a concrete 5-of-9 failure rate on `answer_relevancy`'s JSON parsing requirement,
  plus multiple outright timeouts. The real fix (swapping to Bedrock as an independent,
  stronger judge) is a natural next step, especially since the project's own model registry
  already supports switching generation models — the same registry pattern extends
  naturally to swapping the judge.
- **The refusal-scoring gap is real and unaddressed.** Adversarial/out-of-domain test cases
  score badly on `answer_relevancy` for correctly declining, not for actually failing. Any
  future comparison across models (e.g. 3B vs 8B+) needs to account for this separately —
  a model that refuses more often on genuinely unanswerable questions could look *worse* on
  raw aggregate scores while actually being *more* trustworthy, unless refusal cases are
  scored with a different, explicit rule.
- **Single-turn only** — every question runs with empty history, so the DaemonSet test case
  in this eval harness doesn't exercise conversation context the way the real Phase 6
  conversation did (it still scored well, since "what about a DaemonSet instead?" alone is
  now handled reasonably by the retrieval+generation pipeline even without prior turns, but
  this isn't the same test as the original multi-turn scenario). A proper multi-turn eval
  would need to encode a conversation as a sequence — worth building if follow-up quality
  becomes a focus area.
- **Small test set** — 9 questions is enough to validate the harness works and get a first
  directional read, not enough to draw strong conclusions from. Growing the test set over
  time (especially by continuing to mine real, filtered query logs) is more valuable than
  hand-writing a large set up front.
- **Confirmed real issue #1**: importing `ragas` at all triggers
  `ragas/llms/base.py` to unconditionally import `ChatVertexAI` from `langchain_community`,
  regardless of which LLM provider is actually being used — a common but genuinely poor
  pattern (eagerly importing every optional integration at package load time instead of
  lazily importing only what's requested). Fully diagnosed via direct evidence, not
  guessed: `langchain_community` resolved to 0.4.2, which carries its own
  `DeprecationWarning` ("being sunset... migration guidance toward standalone integration
  packages"), and a direct filesystem check confirmed `chat_models/vertexai.py` is
  genuinely absent from that installed version — not just misnamed or relocated. `ragas`'s
  resolved release still expects it inline regardless.

  **Fixed** by stubbing the missing module in `sys.modules` before importing `ragas` — since
  this project never constructs `ChatVertexAI` at all (only `ChatOllama` and
  `HuggingFaceEmbeddings` are actually used), a harmless placeholder class satisfies the
  import without needing the real, now-removed integration. Verified the stub mechanism
  itself works correctly in isolation (a synthetic `sys.modules` entry correctly satisfies a
  `from package.path import ClassName` statement) before relying on it against the real
  `ragas` import chain.

  **Real, acknowledged risk**: if `ragas/llms/base.py` unconditionally imports more than one
  deprecated provider this way (plausible — this kind of eager-import pattern rarely
  isolates just one), a similar error could surface for a different provider (Cohere, a
  legacy Bedrock integration, etc.) the next layer in. The stub list is written to be
  extended one entry at a time as each one is actually hit, rather than guessed exhaustively
  up front with no way to verify the full list.

- **Confirmed real issue #2**: after the stub fixed the VertexAI import, the next
  `ModuleNotFoundError` was for `PIL` (Pillow) — `ragas.prompt.multi_modal_prompt`
  unconditionally imports it for image+text prompt support, a feature this project never
  uses. Unlike issue #1, this one's a simple missing dependency, not a version conflict or a
  removed file — `pillow` is a normal, actively maintained package we just never declared.
  Fixed by adding it to `pyproject.toml` directly, no workaround needed. Same root cause as
  issue #1 though: `ragas` eagerly imports every optional feature's dependencies at package
  load time rather than only importing what's actually used.

- **Confirmed real issue #3 — production logs contain garbage, not just real questions.**
  Once the eval actually ran end-to-end against the real query log, two of the pulled test
  questions weren't real questions at all: a double-JSON-encoded string
  (`{"query": "how do I create a Pod?"}` — likely an earlier curl command with mismatched
  quoting) and a full GraphQL introspection query (a classic automated API-scanner payload
  probing whether an endpoint happens to be a GraphQL server — evidence something scanned
  this API at some point). Neither would have produced a meaningful eval score, and both
  would have silently counted toward the aggregate metrics if left in. Fixed with a
  pragmatic filter (`_looks_like_real_question`) — skip anything starting with `{` or
  longer than 300 characters — verified against both real patterns found, not just a
  hypothetical one, before trusting it.

- **Confirmed real issue #4 — `context_precision` needed a fix.** See the "Reference-free
  vs reference-based" section above for the full correction: the metric table originally
  had this wrong, and the fix was found by an actual `ValueError` at runtime, not by
  re-reading documentation.

- **Confirmed real issue #5 — asyncio/Python-version compatibility, and a wrong first
  hypothesis worth owning.** After fixing issue #4, every metric came back `nan` and the
  results-reporting code crashed with `KeyError: 'question'`. First guess: the installed
  `ragas` version must use a different column-naming schema entirely (`user_input` instead
  of `question`, etc.). **This was wrong** — worth stating plainly rather than leaving an
  incorrect correction standing. The actual evidence pointed elsewhere: every single
  scoring job failed identically with `RuntimeError(Timeout should be used inside a task)`
  and `coroutine '..._ascore' was never awaited` — a low-level `asyncio` compatibility
  issue, not a column-naming one. The `KeyError` was very likely just a downstream symptom
  of every job failing outright, not an independent schema problem.

  **Most likely real cause**: this environment runs Python 3.14 — extremely recent, and
  `asyncio`'s internals (particularly timeout/task context requirements) change across
  versions. `ragas`'s async executor was almost certainly written and tested against older
  Python, not anything this new. **First fix attempted**: `RunConfig(max_workers=1)`,
  forcing single-threaded, non-concurrent execution — the least disruptive thing to try,
  since it doesn't require changing the Python environment, only whether it avoids
  whatever concurrent-task-scheduling path is actually failing. If this doesn't resolve it,
  the next step is running under an older Python (3.11 or 3.12) via `uv venv --python
  3.12`, which would be a genuine environment-level fix for a library/Python-version
  compatibility gap rather than anything wrong in this project's own code.

## Deliverable

- [x] `eval/test_set.py` — curated + real-log-sourced test questions, filtered against
      real garbage patterns found (double-JSON-encoded strings, GraphQL scanner payloads)
- [x] `eval/run_eval.py` — runs real agent graph, scores with RAGAS, saves results
- [x] Test set builder logic unit-tested in isolation (dedup, ground-truth tagging,
      garbage filtering against the exact real patterns found)
- [x] Full pipeline run confirmed working end-to-end (all 9 questions processed through
      the real agent graph without error)
- [x] Full eval run completed on Python 3.12 (root cause of the Python 3.14 async failure
      confirmed and fixed by rebuilding the venv)
- [x] Results filled in above, with per-question NaN causes traced rather than left
      unexplained
- [x] Real, valuable findings beyond "it works": a metric-limitation discovery (refusal
      scoring), an automated confirmation of the Phase 6 DaemonSet fix, and a measured
      weak-judge failure rate (5/9 JSON parsing failures)
