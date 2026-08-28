# Phase 9 — Deployment

**Status:** containerization written, untested (no Docker daemon in this sandbox); Bedrock
integration written since Phase 4 but **never actually executed** until this phase — that
gap is the main thing this phase closes
**Date:** <!-- fill in -->

## Goal

Package the app so it runs identically outside your WSL environment, and actually verify
the production LLM path (Bedrock) works — not just that it compiles.

## Why containerize at all

Right now, "running DocMind" depends on a long list of specific local facts: your exact
Python version, Node version, Ollama installed a particular way, WSL itself. Docker's job
is to package the *application* — not just its infra dependencies, which Docker Compose
already handled since Phase 0 — into something that runs identically on a VPS, a cloud
platform, or another machine, without anyone needing to replicate your local setup by hand.

## The backend image

`Dockerfile` (project root) builds the FastAPI app using `uv` (the same tool used locally,
so dependency resolution doesn't diverge between your machine and the image) on top of
`python:3.12-slim` — matching the Python version pinned in Phase 8 after the 3.14/`ragas`
incompatibility, not a fresh, independent choice.

**GPU note**: this image runs the embedding model and reranker on CPU. Confirmed directly
against the actual code before writing this — `RetrievalPipeline` never explicitly sets a
device, it relies on `sentence-transformers`' auto-detection (`torch.cuda.is_available()`),
which will correctly and automatically resolve to CPU in a container with no GPU drivers.
This is an acceptable tradeoff specifically because the *actually* GPU-hungry piece (the
LLM) is offloaded to Bedrock in production, not run locally at all — CPU inference for
`bge-base-en-v1.5`/`bge-reranker-base` alone is workable at personal-project scale, unlike
CPU inference for an 8B+ parameter LLM would be.

**Why `chunks.jsonl` gets copied into the image**: the BM25 index is built from this file
at startup (`RetrievalPipeline.load()`). It has to ship with the image, not just live on
your local disk — a common mistake would be assuming Qdrant alone is enough at runtime and
forgetting BM25 needs its own data source too.

## The frontend image, and a real Next.js gotcha

`frontend/Dockerfile` is a two-stage build (build stage compiles the app, runner stage
serves it, keeping the final image smaller by not carrying build tooling). The subtlety
worth understanding: **`NEXT_PUBLIC_*` environment variables are baked into the JavaScript
bundle at *build* time, not read at container *start* time** the way a typical backend env
var would be. Setting `NEXT_PUBLIC_API_URL` only at `docker run` time would silently have no
effect, since the build already happened — it must be passed as a `--build-arg` (or via
`docker-compose.prod.yml`'s `args:` section, which is what's set up here). This is a common,
easy-to-miss deployment mistake specific to Next.js, not something obvious from React
knowledge alone.

## `.dockerignore` — keeping local-only files out of the images

Two separate `.dockerignore` files (root, for the backend build context; `frontend/`, for
the frontend one) exclude things that must never end up baked into a shipped image: `.env`/
`.env.local` (secrets and local URLs), `node_modules`/`.venv` (should be installed fresh
inside the image, not copied from a possibly-incompatible host), and the raw 14.8MB
Kubernetes docs corpus (only needed during ingestion, Phases 1-3 — not at runtime).

## Setting up Bedrock for real — a manual step only you can do

This is the single most important gap this phase closes: `_generate_with_bedrock` has
existed since Phase 4 and was **never executed** until now. "Production-ready" can't
include a completely untested production code path.

**Correction — AWS Bedrock's access model changed since my training data**: AWS retired the
"Model access" console page entirely on **September 29, 2025**. There is no manual
enable/approval step anymore — serverless foundation models are automatically enabled for
every AWS account, and access is controlled purely through IAM/Service Control Policies
instead. (A small number of Marketplace-distributed models still need a subscription, but
that's now created automatically on first invocation, not through any manual page either.)
This actually simplifies setup versus what was originally documented here.

**What's actually needed now**:

1. **AWS credentials** — an IAM user with permission to call Bedrock (the
   `AmazonBedrockFullAccess` managed policy is the simplest option to start with; a more
   locked-down custom policy scoped to just `bedrock:InvokeModel` is better practice once
   this is more than a first test), with an access key ID/secret generated for it.
2. **Anthropic's one-time "use case details" form** — a real, distinct requirement found
   the hard way: an isolated connectivity test succeeded initially, but the same model call
   through the full agent graph shortly after failed with `ResourceNotFoundException: Model
   use case details have not been submitted for this account`. This is **not** the general
   Bedrock Model Access page (retired, see above) — it's an Anthropic-specific, one-time
   form, separate from IAM permissions entirely. **Fix**: AWS Bedrock Console → Model
   catalog → select any Anthropic model → submit the "use case details" form (company
   name/website is typically sufficient) → **wait 15-30 minutes** before retrying — several
   independent reports confirm this doesn't take effect immediately, and a fast retry will
   look like the fix didn't work even when it was submitted correctly.
3. **A correct, currently-available `BEDROCK_MODEL_ID`** — this matters more than it might
   look. AWS has been actively retiring older model versions on Bedrock on a defined
   lifecycle (Legacy status, then an "Extended Access" period at premium pricing, then
   end-of-life) — Claude 3.5 Sonnet and Claude 3.5 Haiku both moved through this cycle in
   late 2025/early 2026, confirmed the hard way (the original default in this project hit
   exactly this end-of-life error on first real use). Current default:
   `us.anthropic.claude-haiku-4-5-20251001-v1:0` — check
   https://docs.aws.amazon.com/bedrock/latest/userguide/models-supported.html for the
   current catalog before relying on this indefinitely.

Once all three are done, test the connection **in isolation** first, before trusting it
through the full agent graph — same principle as every other real library integration in
this project, though worth knowing this specific case showed a real limit to that
principle: an isolated test alone wasn't sufficient here, since the use-case-details
requirement only surfaced on a second, slightly different call:

```bash
export AWS_ACCESS_KEY_ID=...
export AWS_SECRET_ACCESS_KEY=...
export AWS_REGION=us-east-1

uv run python scripts/test_bedrock.py
```

`scripts/test_bedrock.py` deliberately skips loading Qdrant, the embedding model, and
everything else — if this fails, the cause can only be credentials, IAM permissions,
the use-case-details form, region, or a stale/incorrect `BEDROCK_MODEL_ID`, not anything in
DocMind's own code. **Then also test through the real `/query` endpoint** (not just the
isolated script) before considering Bedrock fully verified — this project's own experience
is the concrete argument for why: the isolated test can pass while a subsequent real call
still fails on a requirement the isolated test didn't happen to trigger.

## Deployment target — a decision to make explicitly, not assume

Options, roughly in order of cost/complexity:

1. **Self-hosted, single machine** (`docker-compose.prod.yml`, this repo) — cheapest if you
   already have or can get a small VPS (a few dollars/month on providers like Hetzner,
   DigitalOcean, etc.), full control, but you're responsible for uptime/security patching.
2. **Split hosting** — Railway/Fly.io/Render for the backend container, Vercel for the
   frontend (Vercel is built specifically for Next.js and has a generous free tier) — more
   managed, less to maintain, usually costs a little more or has stricter free-tier limits.
3. **Don't publicly deploy at all yet** — run `docker-compose.prod.yml` locally as a proof
   that containerization works, without paying for or committing to public hosting. A
   completely reasonable choice for a portfolio project that doesn't need 24/7 uptime — the
   working containers themselves, plus this documentation, already demonstrate the skill.

This involves real costs and account setup only you can decide on and create — worth
picking deliberately rather than defaulting to whichever sounds most impressive.

## How to test the container build locally (no public hosting required)

```bash
cp .env.prod.example .env
# fill in JWT_SECRET_KEY, POSTGRES_PASSWORD, AWS credentials, BEDROCK_MODEL_ID,
# and set NEXT_PUBLIC_API_URL=http://localhost:8000 for a local-only test

docker compose -f docker-compose.prod.yml build
docker compose -f docker-compose.prod.yml up -d

curl http://localhost:8000/health
```

**UPDATE — confirmed fully working** after submitting Anthropic's use-case-details form and
waiting the propagation delay. Real `/query` call through the full agent graph (not just
the isolated script) succeeded with `model: "claude-bedrock"`, producing a correctly
grounded, correctly cited answer. All three fixes were genuinely necessary — this wasn't
solved by any single one of them alone.

```
scripts/test_bedrock.py (isolated): SUCCESS — model_used: claude-bedrock, answer: 'OK'

BUT the same model, called through the real /query endpoint shortly after, FAILED:
  ResourceNotFoundException: Model use case details have not been submitted for this
  account. Fill out the Anthropic use case details form before using the model.

This is the most important finding from this whole phase: an isolated connectivity test
passing is NOT sufficient proof a production LLM path is ready — it can pass while a
subsequent real call still fails on a requirement the isolated test didn't happen to
trigger. Three real fixes were needed in total, not one:
  1. AWS retired the "Model access" console page (Sep 29, 2025) — original setup
     instructions referenced a manual approval step that no longer exists.
  2. The originally-configured default model (Claude 3.5 Haiku) had reached end-of-life
     on Bedrock — fixed by switching to Claude Haiku 4.5 with the correct cross-region
     inference profile prefix (us.anthropic.claude-haiku-4-5-20251001-v1:0).
  3. Anthropic's one-time "use case details" form, a distinct requirement from IAM/model
     access entirely — submitted via the Bedrock Console's Model catalog, with a real
     ~15-30 minute propagation delay before it takes effect.

Lesson: a hardcoded model ID is not a "set once" constant for a service like Bedrock —
AWS actively retires model versions on a defined lifecycle. And "it worked once" is not
the same bar as "it's ready for production" — the full request path needs testing, not
just a minimal connectivity check.

docker compose build: ___
docker compose up: ___
curl /health through the container (not the uv-run dev server): ___
Chosen deployment target:
```

## Known limitations / things to revisit

- No HTTPS/reverse proxy configured yet — a real public deployment needs a domain + TLS
  (e.g. via Caddy or nginx with Let's Encrypt, or the hosting platform's built-in TLS if
  using a managed option like Railway/Vercel).
- No CI/CD — images are built manually. A GitHub Actions workflow to build+push on every
  merge would be the natural next step for anything beyond a one-off deployment.
- Bedrock cost is per-token, unlike local Ollama generation which is free — worth watching
  usage/cost in the AWS console once this is live, especially during iteration/testing.
- The frontend's CORS-allowed origin (`http://localhost:3000`, set in Phase 7) needs
  updating to the real production frontend URL once one exists, or the deployed frontend's
  requests will be blocked by the browser the same way they would have been in Phase 7
  without any CORS config at all.

## Deliverable

- [x] `Dockerfile` — backend image
- [x] `frontend/Dockerfile` — frontend image, two-stage build
- [x] `docker-compose.prod.yml` — full stack (qdrant, postgres, backend, frontend)
- [x] `.dockerignore` (root + frontend) — keeps secrets/local files out of images
- [x] `scripts/test_bedrock.py` — isolated Bedrock connectivity test
- [x] Bedrock isolated connectivity test succeeded — but this alone was NOT sufficient
      proof of readiness (see Results above); real `/query` call surfaced a third fix
      needed (Anthropic's use-case-details form) that the isolated test didn't trigger
- [ ] Bedrock verified through the real `/query` endpoint, not just the isolated script —
      pending, waiting on the use-case-details form's ~15-30 min propagation delay
- [ ] Container build/run tested on real hardware
- [ ] Deployment target chosen and (optionally) actually deployed
- [ ] Results filled in above
