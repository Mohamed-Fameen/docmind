"""
Standalone Bedrock connectivity test — deliberately isolated from the rest of the app
(no Qdrant, no embedding model, no retrieval pipeline) so a failure here can only mean
one thing: something about AWS credentials, region, or model access, not anything in
DocMind's own code. Run this BEFORE trusting the "claude-bedrock" registry entry through
the full agent graph.

Usage:
    uv run python scripts/test_bedrock.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.app.llm import generate_answer


def main():
    print("Testing Bedrock connectivity with a trivial prompt...")
    try:
        answer, model_used = generate_answer(
            "Reply with exactly the word: OK", model_name="claude-bedrock"
        )
    except Exception as e:
        print(f"\nFAILED: {type(e).__name__}: {e}")
        print("\nCommon causes, roughly in order of likelihood:")
        print("  1. AWS credentials not configured — check AWS_ACCESS_KEY_ID and")
        print("     AWS_SECRET_ACCESS_KEY are set (env vars, or ~/.aws/credentials).")
        print("  2. ANTHROPIC-SPECIFIC: 'Model use case details have not been submitted")
        print("     for this account' — a real, distinct one-time requirement, separate")
        print("     from IAM permissions and separate from the (now-retired) general")
        print("     Model Access page. Fix: AWS Bedrock Console > Model catalog > select")
        print("     any Anthropic model > submit the 'use case details' form. Takes up to")
        print("     15-30 minutes to actually propagate — a fast retry will look like it")
        print("     didn't work even after correctly submitting the form.")
        print("  3. IAM permissions — the credentials need bedrock:InvokeModel (or the")
        print("     AmazonBedrockFullAccess managed policy) attached.")
        print("  3. Wrong AWS_REGION — Bedrock model availability varies by region; the")
        print("     model ID in config.py must be available in whatever region is set.")
        print("  4. BEDROCK_MODEL_ID itself may be stale or entering end-of-life — check")
        print("     https://docs.aws.amazon.com/bedrock/latest/userguide/models-supported.html")
        print("     for the current, correct model ID and its lifecycle status before")
        print("     relying on it — AWS has been actively retiring older Claude model")
        print("     versions on Bedrock (e.g. Claude 3.5 Sonnet models moved to Legacy/")
        print("     end-of-life status in late 2025/early 2026), so a hardcoded default")
        print("     model ID can go stale over time even after working correctly once.")
        print("  5. A Marketplace-distributed model needing its (now automatic) first-use")
        print("     subscription — this should self-resolve on the first real invocation,")
        print("     but a transient failure on literally the first call is possible.")
        sys.exit(1)

    print(f"\nSUCCESS")
    print(f"  model_used: {model_used}")
    print(f"  answer: {answer!r}")


if __name__ == "__main__":
    main()
