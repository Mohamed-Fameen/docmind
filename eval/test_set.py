"""
Phase 8 — Eval test set.

Combines two sources deliberately, not just one:

1. A small CURATED set covering distinct query types we've already characterized by hand
   across Phases 4-6 (a specific reference lookup, a broad conceptual question, a follow-up
   that needs conversation context, a fictional-feature adversarial test, an out-of-domain
   question). These are exactly the cases we already know the expected behavior for — a
   good sanity check that RAGAS's automated scores actually agree with what manual
   inspection already found, before trusting the scores on anything new.

2. REAL logged queries from data/logs/queries.jsonl (Phase 4's query logging), if present.
   Manually-written eval questions tend to be "nice" — clearly-phrased, obviously in-scope.
   Real usage is messier, and messier is more representative of what the system will
   actually face. A few curated cases have a `ground_truth` for context_recall; logged
   queries don't (no one wrote a reference answer for them), so they only feed the
   reference-free metrics.
"""

import json
from pathlib import Path

QUERY_LOG_PATH = Path("data/logs/queries.jsonl")

# ground_truth is optional and ONLY used for context_recall — every other metric
# (faithfulness, answer_relevancy, context_precision) works without one.
CURATED_TEST_SET = [
    {
        "question": "how do I set the current context in kubeconfig",
        "ground_truth": (
            "Use the command `kubectl config use-context CONTEXT_NAME` to set the "
            "current context in a kubeconfig file."
        ),
    },
    {
        "question": "what is a StatefulSet used for",
        "ground_truth": (
            "A StatefulSet manages the deployment and scaling of a set of Pods, providing "
            "guarantees about the ordering and uniqueness of these Pods, including stable, "
            "unique network identifiers and stable, persistent storage — used for "
            "applications that need one or more of these guarantees, such as databases."
        ),
    },
    {
        # No ground_truth on purpose — this is the multi-turn/context-dependent case from
        # Phase 6. A meaningful reference answer here would require encoding the prior turn
        # too, which the current single-turn eval harness doesn't model. Noted as a known
        # gap below rather than faked.
        "question": "what about a DaemonSet instead?",
    },
    {
        # Adversarial: the QuantumScheduler test from Phase 5. No ground_truth — the
        # "correct" answer is an honest decline, which context_recall (needs a reference
        # answer to compare against) isn't really designed to score anyway. This case is
        # really testing faithfulness (does it avoid inventing a fake explanation) more
        # than any reference-based metric.
        "question": "how do I configure the QuantumScheduler feature in Kubernetes 1.34",
    },
    {
        # Out-of-domain — tests whether the system stays honest about scope rather than
        # forcing a Kubernetes-flavored non-answer.
        "question": "how do I bake a chocolate cake",
    },
]


def _looks_like_real_question(question: str) -> bool:
    """
    Real production logs contain more than genuine user questions — confirmed directly by
    running this eval for real: the actual log contained a double-JSON-encoded string (a
    stray '{"query": "..."}' — likely an earlier curl command with mismatched quoting) and
    a full GraphQL introspection query (a classic automated API-scanner payload, sent
    indiscriminately to probe whether an endpoint happens to be a GraphQL server — evidence
    something scanned this API). Neither is a real question, and both would have silently
    polluted the eval set if not filtered.

    This is a pragmatic heuristic, not a rigorous classifier: skip anything that looks like
    structured data (starts with '{') or is implausibly long for a genuine question (a real
    GraphQL introspection payload is thousands of characters; real questions in this
    project's actual logs have all been well under 300).
    """
    stripped = question.strip()
    if stripped.startswith("{"):
        return False
    if len(stripped) > 300:
        return False
    return True


def load_logged_queries(limit: int = 20) -> list[dict]:
    """
    Pulls real questions out of the query log, deduplicated, most recent first, capped at
    `limit` so a long dev session doesn't silently make every eval run enormous and slow.
    """
    if not QUERY_LOG_PATH.exists():
        return []

    seen_questions = set()
    entries = []
    with QUERY_LOG_PATH.open(encoding="utf-8") as f:
        lines = f.readlines()

    for line in reversed(lines):  # most recent first
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        question = entry.get("query", "").strip()
        if not question or question in seen_questions:
            continue
        if not _looks_like_real_question(question):
            continue
        seen_questions.add(question)
        entries.append({"question": question})
        if len(entries) >= limit:
            break

    return entries


def build_test_set() -> list[dict]:
    logged = load_logged_queries()
    curated_questions = {c["question"] for c in CURATED_TEST_SET}
    # Avoid testing the same question twice if it happens to also appear in the real log.
    logged_deduped = [q for q in logged if q["question"] not in curated_questions]
    return CURATED_TEST_SET + logged_deduped


if __name__ == "__main__":
    test_set = build_test_set()
    print(f"Test set size: {len(test_set)}")
    print(f"  curated: {len(CURATED_TEST_SET)}")
    print(f"  from real logs: {len(test_set) - len(CURATED_TEST_SET)}")
    for case in test_set:
        has_gt = "ground_truth" in case
        print(f"  [{'GT' if has_gt else '  '}] {case['question']}")
