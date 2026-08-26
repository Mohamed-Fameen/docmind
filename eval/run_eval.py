"""
Phase 8 — Eval harness.

Runs every question in the test set through the REAL agent graph (same code path as the
live API — not a separate, simplified re-implementation), collects (question, answer,
retrieved_contexts[, ground_truth]), then scores with RAGAS.

Judge model: uses the local 3B Ollama model by default. This has a known, real limitation
worth understanding, not glossing over: a model judging its OWN generated answers is a
weaker signal than an independent judge would be (self-evaluation bias — a model isn't
necessarily good at spotting its own mistakes, especially a small one). Swap
JUDGE_MODEL/JUDGE_PROVIDER below to a Bedrock model once credentials are set up, for a more
trustworthy judge independent of what's being evaluated — this is also exactly where the
project's planned 3B-vs-8B comparison work would use a stronger, independent judge to score
both models' outputs fairly.

Usage:
    uv run python eval/run_eval.py
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# --- Workaround for a confirmed real dependency conflict ---------------------------------
# ragas's internal ragas/llms/base.py unconditionally imports ChatVertexAI from
# langchain_community at module load time, regardless of which LLM provider is actually
# being used. langchain_community (now explicitly deprecated/"being sunset" per its own
# DeprecationWarning) split Google's VertexAI integration out into a separate package in a
# later release and removed this file entirely — confirmed directly: `pip show
# langchain_community` reports 0.4.2, and `chat_models/vertexai.py` is genuinely absent from
# the installed package, not just relocated. Since we never construct ChatVertexAI (we only
# use Ollama/HuggingFace here), a harmless stub module satisfies ragas's import without
# needing the real, now-removed integration. This must run BEFORE `from ragas import
# evaluate`, since that's what triggers the broken import chain.
#
# NOTE: if a similar ModuleNotFoundError appears for a DIFFERENT deprecated provider (Cohere,
# legacy Bedrock, etc.) after this fix, ragas likely has more than one unconditional import
# of this kind in the same file — extend the stub list below rather than assume this is the
# only one; there's no way to know the full list without hitting each one in turn.
import types

for _stub_path, _stub_class in [
    ("langchain_community.chat_models.vertexai", "ChatVertexAI"),
]:
    if _stub_path not in sys.modules:
        _stub_module = types.ModuleType(_stub_path)
        setattr(_stub_module, _stub_class, type(_stub_class, (), {}))
        sys.modules[_stub_path] = _stub_module
# --- End workaround ------------------------------------------------------------------------

from datasets import Dataset
from langchain_huggingface import HuggingFaceEmbeddings
from langchain_ollama import ChatOllama
from ragas import RunConfig, evaluate
from ragas.embeddings import LangchainEmbeddingsWrapper
from ragas.llms import LangchainLLMWrapper
from ragas.metrics import answer_relevancy, context_precision, context_recall, faithfulness

from backend.app.agent import build_agent_graph
from backend.app.config import settings
from backend.app.retrieval import RetrievalPipeline
from eval.test_set import build_test_set

RESULTS_PATH = Path("eval/results.json")

# See module docstring — a local, independent-from-generation model would be a better judge
# than reusing the model under test, but this is a reasonable free starting point.
JUDGE_MODEL = "llama3.2:latest"


def run_pipeline(agent_graph, question: str) -> dict:
    """Runs one question through the real agent graph, same as a live /query call would."""
    initial_state = {
        "query": question,
        "original_query": question,
        "model": None,  # use the configured default model
        "history": [],  # eval treats every question as a fresh, single-turn conversation
        "classification": "",
        "chunks": [],
        "source_refs": [],
        "answer": "",
        "model_used": "",
        "retry_count": 0,
        "confidence": "",
    }
    result = agent_graph.invoke(initial_state)
    return result


def main():
    print("Loading retrieval pipeline (embedding model, reranker, BM25 index)...")
    pipeline = RetrievalPipeline()
    pipeline.load()
    agent_graph = build_agent_graph(pipeline)

    test_set = build_test_set()
    print(f"Running {len(test_set)} test questions through the real pipeline...")

    rows = []
    for i, case in enumerate(test_set, start=1):
        question = case["question"]
        print(f"  [{i}/{len(test_set)}] {question}")
        result = run_pipeline(agent_graph, question)

        rows.append(
            {
                "question": question,
                "answer": result["answer"],
                # RAGAS wants plain strings, not our chunk dicts — the chunk text is what
                # actually gets judged against, not any of our metadata.
                "contexts": [c["text"] for c in result["chunks"]],
                "ground_truth": case.get("ground_truth", ""),
                "classification": result["classification"],
                "retries": result["retry_count"],
            }
        )

    # Reference-free metrics — genuinely need no ground truth in this ragas version.
    print("\nScoring reference-free metrics (faithfulness, answer relevancy)...")
    judge_llm = LangchainLLMWrapper(ChatOllama(model=JUDGE_MODEL))
    judge_embeddings = LangchainEmbeddingsWrapper(
        HuggingFaceEmbeddings(model_name=settings.embedding_model)
    )

    # Every scoring job failed identically with `RuntimeError(Timeout should be used
    # inside a task)` the first time this ran for real — not a column-naming issue after
    # all (an earlier version of this comment guessed that; corrected here). This is a
    # low-level asyncio compatibility problem: ragas's internal executor uses a timeout
    # mechanism that needs a specific async task context, and something about the running
    # environment (likely Python 3.14 — extremely recent, asyncio internals change across
    # versions, and ragas's async code was almost certainly written/tested against older
    # Python) doesn't satisfy it. Forcing single-threaded, non-concurrent execution via
    # RunConfig is the least disruptive thing to try first — it avoids whatever concurrent
    # task-scheduling path is triggering the incompatibility, at the cost of the eval
    # running slower (one judge call at a time instead of in parallel).
    run_config = RunConfig(max_workers=1)

    reference_free_dataset = Dataset.from_list(
        [
            {"question": r["question"], "answer": r["answer"], "contexts": r["contexts"]}
            for r in rows
        ]
    )
    reference_free_results = evaluate(
        reference_free_dataset,
        metrics=[faithfulness, answer_relevancy],
        llm=judge_llm,
        embeddings=judge_embeddings,
        run_config=run_config,
    )

    # context_precision and context_recall BOTH need a ground_truth reference in this ragas
    # version — corrected after a real run: this project's docs originally (incorrectly)
    # listed context_precision as reference-free, based on outdated assumptions about the
    # metric's implementation. The installed version judges retrieved-context relevance
    # against the reference answer, not independently, so it needs the same 'reference'
    # column context_recall does. Only the subset of questions with a ground_truth get
    # scored on either metric.
    rows_with_gt = [r for r in rows if r["ground_truth"]]
    reference_based_results = None
    if rows_with_gt:
        print(
            f"\nScoring reference-based metrics (context precision, context recall) on "
            f"{len(rows_with_gt)} question(s) with a ground truth reference..."
        )
        reference_based_dataset = Dataset.from_list(
            [
                {
                    "question": r["question"],
                    "answer": r["answer"],
                    "contexts": r["contexts"],
                    "ground_truth": r["ground_truth"],
                    "reference": r["ground_truth"],  # some ragas metrics expect this name
                }
                for r in rows_with_gt
            ]
        )
        reference_based_results = evaluate(
            reference_based_dataset,
            metrics=[context_precision, context_recall],
            llm=judge_llm,
            embeddings=judge_embeddings,
            run_config=run_config,
        )

    # --- Report ---
    print("\n" + "=" * 70)
    print("AGGREGATE SCORES — reference-free (all questions)")
    print("=" * 70)
    ref_free_df = reference_free_results.to_pandas()
    for metric in ["faithfulness", "answer_relevancy"]:
        if metric in ref_free_df.columns:
            print(f"  {metric:20s}: {ref_free_df[metric].mean():.3f}")

    if reference_based_results is not None:
        print(f"\n" + "=" * 70)
        print(f"AGGREGATE SCORES — reference-based (n={len(rows_with_gt)})")
        print("=" * 70)
        ref_based_df = reference_based_results.to_pandas()
        for metric in ["context_precision", "context_recall"]:
            if metric in ref_based_df.columns:
                print(f"  {metric:20s}: {ref_based_df[metric].mean():.3f}")

    print("\n" + "=" * 70)
    print("PER-QUESTION BREAKDOWN (reference-free metrics)")
    print("=" * 70)
    print(f"(dataframe columns: {list(ref_free_df.columns)})")  # diagnostic — if this run
    # still fails, this line shows exactly what ragas actually returned, instead of a bare
    # KeyError with no context about what WAS available.
    question_col = "question" if "question" in ref_free_df.columns else "user_input"
    for _, row in ref_free_df.iterrows():
        label = row[question_col] if question_col in row else "(question column not found)"
        print(f"\nQ: {label}")
        for metric in ["faithfulness", "answer_relevancy"]:
            if metric in row:
                print(f"    {metric}: {row[metric]:.3f}")

    # Save full results for later comparison (e.g. before/after a prompt change, or 3B vs
    # a larger model) — printing to console alone loses this the moment the terminal closes.
    output = {
        "reference_free": ref_free_df.to_dict(orient="records"),
        "reference_based": (
            reference_based_results.to_pandas().to_dict(orient="records")
            if reference_based_results is not None
            else None
        ),
    }
    RESULTS_PATH.write_text(json.dumps(output, indent=2, default=str))
    print(f"\nFull results saved to {RESULTS_PATH}")


if __name__ == "__main__":
    main()
