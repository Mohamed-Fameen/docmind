"""
LLM generation: builds a grounded prompt from retrieved chunks, then dispatches to whichever
model was requested (or the configured default) via the registry in config.py.

The prompt design is the actual anti-hallucination mechanism here — retrieval alone doesn't
stop an LLM from making things up if the prompt doesn't explicitly forbid it.
"""

from .config import settings


def build_prompt(query: str, chunks: list[dict]) -> tuple[str, list[dict]]:
    """
    Returns (prompt, source_refs). source_refs maps citation numbers to the metadata a
    frontend would need to render a clickable source link (Phase 7).
    """
    context_blocks = []
    source_refs = []
    for i, chunk in enumerate(chunks, start=1):
        context_blocks.append(f"[{i}] {chunk['heading_path']}\n{chunk['text']}")
        source_refs.append(
            {
                "number": i,
                "heading_path": chunk["heading_path"],
                "doc_url": chunk["doc_url"],
            }
        )

    context = "\n\n".join(context_blocks)

    prompt = f"""You are a Kubernetes documentation assistant. Answer the user's question \
using ONLY the information in the sources below. Cite sources inline using their number in \
brackets, like [1]. If the sources don't contain enough information to answer, say so \
directly rather than guessing or using outside knowledge.

SOURCES:
{context}

QUESTION: {query}

ANSWER:"""

    return prompt, source_refs


def _generate_with_ollama(prompt: str, model_config: dict) -> str:
    import ollama

    # Forcing CPU-only generation here is a deliberate tradeoff, not an oversight: even the
    # 3B model still OOM'd when competing with bge-base-en-v1.5 + bge-reranker-base for VRAM
    # on a small GPU (confirmed via a real `cudaMalloc failed` crash with ~1.8GB unable to
    # allocate — there was essentially no free VRAM left once the encoder models were
    # resident). Since actual "production-tier" generation goes through Bedrock anyway (not
    # local Ollama), local dev generation being CPU-bound and slower is an acceptable
    # tradeoff for a stable pipeline, not a real limitation. Flip OLLAMA_FORCE_CPU=false in
    # .env if you have more VRAM headroom (a desktop GPU, or a machine dedicated to this).
    num_gpu = 0 if settings.ollama_force_cpu else -1  # -1 = let Ollama decide automatically

    response = ollama.chat(
        model=model_config["ollama_model"],
        messages=[{"role": "user", "content": prompt}],
        options={"num_gpu": num_gpu},
    )
    return response["message"]["content"]


def _generate_with_bedrock(prompt: str, model_config: dict) -> str:
    """
    Uses Bedrock's Converse API — a unified interface across model families (Anthropic,
    Llama, etc. all use the same request/response shape), rather than the older
    invoke_model API which requires a different JSON body per model family.

    Credentials: picked up automatically by boto3's default credential chain (environment
    variables, ~/.aws/credentials, or an IAM role if running on AWS infra) — deliberately
    NOT passed explicitly here, since hardcoding credential handling in application code is
    a common source of leaked keys.
    """
    import boto3

    client = boto3.client("bedrock-runtime", region_name=model_config["bedrock_region"])
    response = client.converse(
        modelId=model_config["bedrock_model_id"],
        messages=[{"role": "user", "content": [{"text": prompt}]}],
        inferenceConfig={"maxTokens": 1024},
    )
    return response["output"]["message"]["content"][0]["text"]


_PROVIDERS = {
    "ollama": _generate_with_ollama,
    "bedrock": _generate_with_bedrock,
}


def generate_answer(prompt: str, model_name: str | None = None) -> tuple[str, str]:
    """
    Returns (answer_text, model_name_used) — the model name is returned alongside the
    answer so callers (and eventually an eval harness comparing models) always know exactly
    which model produced a given answer, not just what was requested.
    """
    model_name = model_name or settings.default_model
    model_config = settings.MODEL_REGISTRY.get(model_name)

    if model_config is None:
        available = ", ".join(settings.MODEL_REGISTRY.keys())
        raise ValueError(f"Unknown model '{model_name}'. Available: {available}")

    provider = model_config["provider"]
    generate_fn = _PROVIDERS.get(provider)
    if generate_fn is None:
        raise ValueError(f"No handler registered for provider '{provider}'")

    answer = generate_fn(prompt, model_config)
    return answer, model_name
