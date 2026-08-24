"""
Phase 5 — Agentic layer.

Wraps Phase 4's retrieve-and-generate pipeline in a LangGraph state machine that decides
WHETHER to retrieve at all, and can retry once with a rewritten query if the first attempt's
answer looks poorly grounded — rather than always running the same fixed retrieve->rerank->
generate sequence regardless of what was actually asked.

State machine (see the diagram shared alongside this doc):

    classify -> DIRECT     -> direct_reply         -> END
             -> CLARIFY    -> ask_clarification     -> END
             -> RETRIEVE   -> retrieve_and_generate -> check_confidence -> [confident] -> END
                                       ^                                -> [low, retry 0] -+
                                       +--------------- rewrite_query <---------------------+

Every node function has the signature (state: AgentState) -> dict (a partial state update —
LangGraph merges this into the running state, it doesn't need to return the whole thing).
"""

from typing import TypedDict

from langgraph.graph import END, StateGraph

from .citations import extract_cited_numbers
from .llm import build_prompt, generate_answer
from .retrieval import RetrievalPipeline

# Phrases that suggest the model itself flagged the retrieved context as insufficient —
# a direct textual signal, cheaper than a second LLM call to "judge" the first one. This is
# a heuristic, not a certainty; see docs/05-agentic.md for why an LLM-based judge was
# deliberately NOT used here (latency cost on CPU-bound local generation).
UNCERTAINTY_PHRASES = [
    "don't contain",
    "do not contain",
    "doesn't contain",
    "does not contain",
    "not enough information",
    "cannot determine",
    "can't determine",
    "i don't know",
    "no information",
    "not found in the sources",
    "unable to find",
    "the sources don't",
    "the sources do not",
]

CLASSIFY_PROMPT = """You are the router for a Kubernetes documentation assistant. Decide \
how to handle the user's message below.

Reply with EXACTLY ONE WORD and nothing else:
- RETRIEVE — the message is a real question about Kubernetes that needs looking up documentation
- DIRECT — the message is a greeting, thanks, or small talk needing no documentation (e.g. "hi", "thanks", "what can you do")
- CLARIFY — the message is too vague or ambiguous to search for (e.g. "help", "it doesn't work")

Message: "{query}"

One word answer:"""

DIRECT_REPLY_PROMPT = """You are a friendly Kubernetes documentation assistant. Respond \
briefly and naturally to this message, without inventing any technical claims about \
Kubernetes: "{query}" """

REWRITE_PROMPT = """The question below did not retrieve a confident answer from Kubernetes \
documentation. Rewrite it as a clearer, more specific search query using precise Kubernetes \
terminology. Reply with ONLY the rewritten query, at most 15 words, no explanation.

Original question: "{query}"

Rewritten query:"""

CONTEXTUALIZE_PROMPT = """Given the conversation so far and a follow-up message, rewrite \
the follow-up as a standalone question that makes full sense without seeing the earlier \
conversation. If the follow-up already stands alone, just repeat it unchanged. Reply with \
ONLY the rewritten question, no explanation.

CONVERSATION SO FAR:
{history}

FOLLOW-UP: "{query}"

STANDALONE QUESTION:"""

CLARIFY_FALLBACK = (
    "Could you say a bit more about what you'd like to know? For example, are you asking "
    "about a specific Kubernetes resource (like Pods or Deployments), a kubectl command, or "
    "troubleshooting a specific error?"
)


class AgentState(TypedDict):
    query: str              # current query — gets overwritten on retry with a rewritten one
    original_query: str     # preserved for logging, unaffected by rewrites
    model: str | None
    history: list[dict]     # prior conversation turns, oldest first — see llm.build_prompt
    classification: str
    chunks: list[dict]
    source_refs: list[dict]
    answer: str
    model_used: str
    retry_count: int
    confidence: str


def build_agent_graph(pipeline: RetrievalPipeline):
    """
    Takes the already-loaded RetrievalPipeline as a dependency (rather than importing a
    global from main.py) to avoid a circular import — main.py builds the pipeline, then
    builds this graph from it, not the other way around.
    """

    def classify_node(state: AgentState) -> dict:
        prompt = CLASSIFY_PROMPT.format(query=state["query"])
        response, _ = generate_answer(prompt, model_name=state["model"])
        upper = response.strip().upper()

        # Default to RETRIEVE on any unrecognized response — grounding an answer in real
        # docs is the safer failure mode than answering directly from the model's own
        # (possibly outdated) pretrained knowledge, if classification itself is ambiguous.
        if "DIRECT" in upper:
            classification = "direct"
        elif "CLARIFY" in upper:
            classification = "clarify"
        else:
            classification = "retrieve"

        return {"classification": classification}

    def direct_node(state: AgentState) -> dict:
        prompt = DIRECT_REPLY_PROMPT.format(query=state["query"])
        answer, model_used = generate_answer(prompt, model_name=state["model"])
        return {"answer": answer, "model_used": model_used, "chunks": [], "source_refs": []}

    def clarify_node(state: AgentState) -> dict:
        # Deliberately a canned response, not an LLM call — faster, fully deterministic, and
        # a clarifying question doesn't need to be dynamically generated to do its job.
        # Noted as a possible future enhancement (a context-aware clarifying question) in
        # docs/05-agentic.md rather than built now.
        return {"answer": CLARIFY_FALLBACK, "chunks": [], "source_refs": [], "model_used": "n/a"}

    def retrieve_and_generate_node(state: AgentState) -> dict:
        chunks = pipeline.retrieve(state["query"])
        prompt, source_refs = build_prompt(state["query"], chunks, history=state["history"])
        answer, model_used = generate_answer(prompt, model_name=state["model"])
        return {
            "chunks": chunks,
            "source_refs": source_refs,
            "answer": answer,
            "model_used": model_used,
        }

    def check_confidence_node(state: AgentState) -> dict:
        answer_lower = state["answer"].lower()
        cited = extract_cited_numbers(state["answer"])

        looks_uncertain = any(phrase in answer_lower for phrase in UNCERTAINTY_PHRASES)
        no_citations_despite_sources = len(state["chunks"]) > 0 and len(cited) == 0

        confidence = "low" if (looks_uncertain or no_citations_despite_sources) else "confident"
        return {"confidence": confidence}

    def rewrite_query_node(state: AgentState) -> dict:
        prompt = REWRITE_PROMPT.format(query=state["query"])
        rewritten, _ = generate_answer(prompt, model_name=state["model"])
        # Strip a wrapping quote pair if the model added one despite instructions not to.
        rewritten = rewritten.strip().strip('"').strip("'")
        return {"query": rewritten, "retry_count": state["retry_count"] + 1}

    def contextualize_query_node(state: AgentState) -> dict:
        """
        Rewrites a follow-up like "what about a DaemonSet instead?" into a standalone
        query like "how do I create a DaemonSet in Kubernetes" BEFORE retrieval runs.

        This exists because history was originally only threaded into the generation
        prompt, not the retrieval query — a real gap found by testing an actual follow-up
        question: retrieval on the raw elliptical text ("what about X instead?") returned
        only loosely-related generic chunks about X, never anything about how to actually
        DO the thing being asked, because retrieval never saw "instead of what". The model
        then partially hallucinated citation support to cover for the gap in what was
        actually retrieved — a good example of how a retrieval-side gap can surface as a
        generation-side hallucination two steps downstream.

        No-op (query unchanged) when there's no history — the common case, and the cost of
        an extra LLM call isn't worth paying for a query that was already self-contained.
        """
        if not state["history"]:
            return {}

        history_text = "\n".join(f"{h['role'].upper()}: {h['content']}" for h in state["history"])
        prompt = CONTEXTUALIZE_PROMPT.format(history=history_text, query=state["query"])
        rewritten, _ = generate_answer(prompt, model_name=state["model"])
        rewritten = rewritten.strip().strip('"').strip("'")
        return {"query": rewritten}

    def route_after_classify(state: AgentState) -> str:
        return state["classification"]

    def route_after_confidence(state: AgentState) -> str:
        if state["confidence"] == "low" and state["retry_count"] == 0:
            return "retry"
        return "done"

    graph = StateGraph(AgentState)
    graph.add_node("classify", classify_node)
    graph.add_node("direct", direct_node)
    graph.add_node("clarify", clarify_node)
    graph.add_node("contextualize", contextualize_query_node)
    graph.add_node("retrieve_generate", retrieve_and_generate_node)
    graph.add_node("check_confidence", check_confidence_node)
    graph.add_node("rewrite_query", rewrite_query_node)

    graph.set_entry_point("classify")
    graph.add_conditional_edges(
        "classify",
        route_after_classify,
        {"direct": "direct", "clarify": "clarify", "retrieve": "contextualize"},
    )
    graph.add_edge("direct", END)
    graph.add_edge("clarify", END)
    graph.add_edge("contextualize", "retrieve_generate")
    graph.add_edge("retrieve_generate", "check_confidence")
    graph.add_conditional_edges(
        "check_confidence",
        route_after_confidence,
        {"retry": "rewrite_query", "done": END},
    )
    graph.add_edge("rewrite_query", "retrieve_generate")

    return graph.compile()
