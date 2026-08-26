# Phase 6 — Memory & Auth

**Status:** done — bcrypt/passlib bug and the retrieval-side memory gap both found and
fixed via real testing, confirmed with a direct before/after comparison
**Date:** <!-- fill in -->

## Goal

Turn DocMind from a stateless single-shot Q&A endpoint into a real multi-user chat product:
persistent conversations, follow-up questions that resolve pronouns/references from earlier
turns, and per-user accounts so conversations don't leak between users.

## Why auth and memory are really one feature

"Whose conversation is this?" and "who is this user?" are the same question asked two ways.
You can't build per-user chat history without first knowing who the user is — so this phase
builds both together rather than as separate concerns.

## Deviation from the original plan: hand-rolled JWT auth, not Supabase

The original 9-phase plan named Supabase ("Postgres + Auth in one, fast to set up"). Built
it differently here, deliberately: Supabase Auth is a hosted third-party service, and every
other piece of infrastructure in this project runs locally in your own Docker Compose
(Qdrant, Redis, Postgres, Ollama) — introducing an external SaaS dependency for just this one
piece would break that consistency. More importantly, hand-rolling JWT auth (password
hashing, token issuance, protecting endpoints) teaches the actual mechanics directly, which
matters more for the backend-engineering skills this project is meant to build than wiring
up a managed service that hides them. The local Postgres container from Phase 0 had been
sitting completely unused until now — this phase finally uses it.

## Schema: three tables, minimal by design

```
users            id, email, hashed_password, created_at
conversations    id, user_id (FK), title, created_at
messages         id, conversation_id (FK), role, content, sources (JSON), created_at
```

- One conversation belongs to exactly one user — no shared/team conversations yet (would
  need a join table; deliberately out of scope).
- `messages.sources` is a JSON column storing the citations for assistant messages, so
  reloading history can still show what a past answer cited, not just its raw text.
- **No Alembic migrations yet.** `Base.metadata.create_all()` at startup creates missing
  tables but never alters existing ones — fine while the schema is small and still settling,
  not fine once there's real data and a schema change. Alembic is the right next step once
  that point is reached, not before.

## Auth mechanics — what's actually happening, not just which libraries are used

**Password hashing (bcrypt via passlib):** never store a password in plaintext or even
reversibly encrypted — only a one-way hash. bcrypt is deliberately *slow* (unlike, say, plain
SHA-256) — that slowness is the actual security property, since it makes brute-forcing a
stolen hash database far more computationally expensive.

**JWT (JSON Web Token):** a signed, not encrypted, blob. Anyone can decode and read a JWT's
contents — but only someone holding `jwt_secret_key` could have produced one with a *valid
signature*. That's what lets `get_current_user` trust a token's claims (just the user id
here) without a database round-trip on every single request: verifying the signature is
enough to know the token wasn't forged, as long as the secret key never leaks. This is also
exactly why `JWT_SECRET_KEY`'s dev default is explicitly marked insecure — anyone who knows
that default value could forge a valid token for any user id.

**`get_current_user` as a FastAPI dependency:** adding `current_user: User =
Depends(get_current_user)` to any route is what makes it "protected" — no decorator, no
middleware, just a dependency that raises 401 before the route body ever runs if the token
is missing, malformed, expired, or forged.

## Memory mechanics — how a follow-up question actually gets resolved

1. `/query` now accepts an optional `conversation_id`. Omit it to start a new conversation;
   include it to continue one.
2. **Ownership is checked, not assumed** — a `conversation_id` belonging to a different user
   is rejected with 404, not silently allowed. Without this check, any authenticated user
   could read anyone else's conversation just by guessing or reusing an id.
3. `_load_history` pulls the last `conversation_history_turns` messages (6 = 3 user/assistant
   pairs — enough to resolve most follow-ups without bloating every prompt with the entire
   conversation).
4. History flows into the agent graph's state, then into `build_prompt`, which prepends a
   `CONVERSATION SO FAR:` block before the sources.
5. **Before retrieval runs**, a `contextualize` node rewrites the raw follow-up into a
   standalone query using the conversation history — see "Real bug found: retrieval never
   saw conversation history" below for why this exists.
6. **Both turns get persisted after generation** — the user's question and the assistant's
   answer (with its citations) both become new `Message` rows, so the *next* call to this
   conversation sees this turn as history too.

## Real bug found: retrieval never saw conversation history

The first real follow-up test exposed a genuine design gap, not just a rough edge. Asking
"how do I create a Pod?" then, in the same conversation, "what about a DaemonSet instead?"
produced a poor answer — and tracing why revealed the actual cause.

**What happened:** conversation history was threaded into the *generation* prompt (so the
LLM would understand what "instead" meant when writing the answer) but never into the
*retrieval* query. Hybrid search ran on the literal text `"what about a DaemonSet
instead?"` — which, read with no context, isn't really a coherent search query (no verb, no
real subject) — and returned only generically DaemonSet-related chunks (a ReplicaSet
comparison, an API reference stub, a "what's next" page), none of which actually explained
how to *create* one. Contrast with the first query, which correctly retrieved a chunk with
a real worked YAML example, precisely because "how do I create a Pod?" is coherent on its
own.

**The downstream consequence was worse than a weak answer — it was a hallucinated
citation.** The model claimed *"you can use the `kubectl apply` command... as mentioned in
sources [2] and [5]"* — but source [5]'s actual text was just *"DaemonSetList is a
collection of daemon sets. \<hr\>"*, which says nothing about `kubectl apply` at all. The
citation validator built in Phase 4/5 didn't catch this, because it only checks whether
`[5]` appears somewhere in the answer text — it has no way to verify whether chunk 5's
*actual content* supports the *specific claim* sitting next to that citation number. That's
a deeper grounding problem than citation-presence checking, and it's the kind of thing
Phase 8's RAGAS faithfulness metric is meant to measure systematically — not something to
solve by hand here.

**Fixed the retrieval-side gap**: a new `contextualize` node runs between `classify` and
`retrieve_generate`, rewriting a follow-up into a standalone query ("what about a DaemonSet
instead?" → "how do I create a DaemonSet in Kubernetes") *before* the query ever reaches
retrieval. This is a no-op (skipped entirely) when there's no conversation history, so it
costs nothing for the common single-turn case.

**Not fully fixed**: the citation-content-mismatch problem (a source is cited, but doesn't
actually support the adjacent claim) remains open. Verifying that a citation's *content*
genuinely supports its *claim* would need something like an NLI-style entailment check or
RAGAS's faithfulness scoring — real work, correctly scoped for Phase 8's evaluation
harness, not something to bolt onto the agent graph as an afterthought.

## A deliberate anti-hallucination safeguard specific to multi-turn memory

The prompt explicitly instructs: *"Use the conversation so far... only to understand what
the user is referring to — never as a source of facts about Kubernetes itself."* Without this
line, a subtle compounding failure becomes possible: if the model said something slightly
wrong in an earlier turn, a naive prompt might let it treat its own earlier (possibly wrong)
statement as an established fact in the current turn, compounding the error across a
conversation instead of staying anchored to the actual retrieved sources every single time.

## Real bug found: passlib + modern bcrypt incompatibility

First real run hit `ValueError: password cannot be longer than 72 bytes` on
`hash_password("testpass123")` — an 11-character password nowhere near the 72-byte limit,
which was the tell that this wasn't actually about password length. Root cause: `passlib`
(last released in 2020, effectively unmaintained) runs an internal self-test against its
bcrypt backend on first use, and that self-test itself breaks against modern versions of the
`bcrypt` package (>=4.1) — the crash happens inside passlib's own test vector construction,
not on the real input, which is why the error message was so misleading.

**Fixed** by dropping `passlib` and calling `bcrypt` directly — a thinner, actively
maintained dependency, and arguably a better learning outcome anyway: `hash_password` and
`verify_password` now show exactly what's happening (encode to bytes, hash with a random
salt, compare with constant-time comparison) rather than going through an abstraction layer
that turned out to be broken. The 72-byte limit is still real (it's a genuine bcrypt
algorithm constraint, not a passlib artifact) — now enforced explicitly with a clear error
instead of silently truncating, since silent truncation would mean two different passwords
past the limit could hash identically without either user ever being told why.

## How to run

```bash
docker compose -f docker/docker-compose.yml up -d   # Postgres included
uv sync                                              # picks up passlib, pyjwt, email-validator, python-multipart
uv run uvicorn backend.app.main:app --reload
```

Register, then log in (note: `/auth/login` takes form-encoded data, not JSON — see the
`login()` docstring for why):

```bash
curl -X POST http://localhost:8000/auth/register \
  -H "Content-Type: application/json" \
  -d '{"email": "test@example.com", "password": "testpass123"}'

curl -X POST http://localhost:8000/auth/login \
  -d "username=test@example.com&password=testpass123"
```

Use the returned `access_token` for `/query`:

```bash
curl -X POST http://localhost:8000/query \
  -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json" \
  -d '{"query": "how do I create a Pod?"}'
```

Then send a follow-up in the **same conversation** (using the `conversation_id` from the
first response) and check whether the answer correctly resolves the implicit reference:

```bash
curl -X POST http://localhost:8000/query \
  -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json" \
  -d '{"query": "what about a DaemonSet instead?", "conversation_id": "<id from above>"}'
```

## Results

```
Register: succeeded (after the bcrypt fix) ✓
Login: token received ✓
Query with token, no conversation_id: new conversation created ✓ (id returned and reused
  correctly across both test calls)

Follow-up memory test — RETESTED after the contextualize fix, direct before/after comparison:

  BEFORE fix — retrieved for "what about a DaemonSet instead?":
    ReplicaSet > DaemonSet (comparison page)
    ReplicationController > DaemonSet (comparison page)
    DaemonSet > DaemonSet (bare API reference stub)
    DaemonSet > Whatsnext
    DaemonSet > DaemonSetList (bare API reference stub)
    -> none of these explain how to CREATE a DaemonSet; model partially hallucinated
       citation support to compensate (see "Real bug found" above)

  AFTER fix — retrieved for the same follow-up:
    DaemonSet > Introduction
    DaemonSet > Bare Pods
    DaemonSet > How Daemon Pods are scheduled
    Building a Basic DaemonSet > Define the DaemonSet   <- genuine creation tutorial,
                                                             wasn't retrieved at all before
    -> answer gave a real `kubectl apply -f .../basic-daemonset.yaml` command, correctly
       parallel to the Pod example from turn one; citation [3] genuinely supports its
       claim this time (verified by comparing citation text to source text_snippet)

CONFIRMED: the contextualize node fixed the actual retrieval-side gap, not just the
symptom. Minor remaining nuance: source [5] (the tutorial itself, almost certainly the
actual origin of the kubectl command) never got an explicit citation next to that specific
sentence — an incomplete citation, not a fabricated one. Milder than the pre-fix issue, not
fully resolved, consistent with the citation-content-grounding gap already flagged for
Phase 8.

Query with someone else's conversation_id: not yet tested
```

## Known limitations / things to revisit

- No Alembic migrations — fine now, will need addressing before any real schema change
  after real user data exists.
- No password reset / email verification flow — acceptable for a portfolio project, would
  be required for anything with real users.
- ~~`classify_node` and `direct_node` still don't receive conversation history~~ — **found
  as a real bug during actual UI testing (Phase 7), not just a theoretical gap, and fixed.**
  Without history, a coherent follow-up correction ("I did not ask about replicaset, only
  about deployments") read as vague in total isolation and got stuck classifying as
  `clarify` on every rephrasing — a real infinite-clarify loop a live user hit. Fixed by
  threading history into `classify_node`'s prompt too, with explicit instruction to use
  conversation context before deciding CLARIFY vs RETRIEVE. Verified against the exact
  reported conversation, then against a full 3-turn follow-up chain ("deployments" →
  "replicaset" → "compare both of them") — the last one correctly used both prior turns to
  resolve "both" with no antecedent of its own, retrieving the exact right comparison page.
  `direct_node` still doesn't receive history — lower priority, since a greeting/thanks
  genuinely doesn't need conversation context to handle correctly.
- Redis (originally planned for "session-level fast access") wasn't introduced in this
  phase — Postgres alone is sufficient at this project's scale, and adding a cache layer
  before there's a measured latency problem would be solving a problem that doesn't exist
  yet. Revisit if `_load_history`'s query time ever actually shows up as a bottleneck.
- Minor cosmetic issue found during the same real testing: answers sometimes append a
  redundant, occasionally mislabeled citation summary line after the main text (e.g. "[1]
  ReplicaSet > Introduction" when source [1] was actually a different page). Doesn't affect
  the main answer's correctness — the inline citations within the answer body are correct —
  just an odd trailing artifact. Worth a future prompt tweak (explicitly disallow repeating
  a citation summary after the answer), not urgent.
- **Citation-content grounding remains unverified** — the `[N]` citation validator confirms
  a number was used, never that the chunk's actual content supports the specific claim next
  to it. The QuantumScheduler-style "honest decline" cases from Phase 5 suggest the model
  usually gets this right, but the DaemonSet test here shows it doesn't always. This is
  exactly what Phase 8's RAGAS faithfulness scoring is for — flagging it here as a concrete,
  demonstrated reason that phase matters, not a hypothetical one.

## Deliverable

- [x] `backend/app/db.py` — SQLAlchemy engine, session, FastAPI dependency
- [x] `backend/app/models_db.py` — User, Conversation, Message ORM models
- [x] `backend/app/auth.py` — bcrypt password hashing (direct, not via passlib — see real
      bug found above), JWT issuance/verification, `get_current_user`
- [x] `backend/app/main.py` — `/auth/register`, `/auth/login`, `/query` now protected +
      persists history + loads prior turns for follow-up resolution
- [x] `backend/app/llm.py` — `build_prompt` accepts conversation history
- [x] `backend/app/agent.py` — history threaded through agent state; `contextualize` node
      rewrites follow-ups into standalone queries before retrieval
- [x] Register/login/query verified on real hardware; bcrypt/passlib bug found and fixed
- [x] Follow-up memory tested for real — found and fixed a genuine retrieval-side gap
      (history reached generation but not retrieval), and surfaced a still-open citation
      grounding gap correctly deferred to Phase 8
- [x] Retested the DaemonSet follow-up after the `contextualize` fix — confirmed working,
      direct before/after comparison shows the fix resolved the actual retrieval gap
- [ ] Cross-user conversation access rejection (404 case) not yet tested
