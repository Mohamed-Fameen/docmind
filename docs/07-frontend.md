# Phase 7 — Frontend

**Status:** written, typechecked as far as possible without network access (no `npm
install` was run — this sandbox can't reach the npm registry, same constraint as every
other external dependency in this project); genuinely unverified against real installed
packages
**Date:** <!-- fill in -->

## Goal

A real chat UI: login/register, a message thread with citations, and a model picker — the
first point where DocMind is something a person clicks around in, not just something
queried with `curl`.

## Core concepts, for anyone new to frontend work

**A frontend is just another HTTP client.** Everything built in Phases 4-6 only understands
HTTP requests — you've been sending them by hand with `curl`. The frontend is a program that
turns clicks and typing into those same requests, and turns the JSON responses into chat
bubbles instead of printed text. Nothing about the backend's actual logic changes.

**React components** are small, reusable functions that describe "what should be on
screen" in a hybrid HTML/JavaScript syntax called JSX. `MessageBubble`, `LoginForm`, and
`ChatWindow` are all components — React's job is to re-render only the pieces that
actually changed when data changes, instead of manually rewriting HTML by hand.

**State** is the same concept as backend state (data that changes over time), scoped to a
component instead of a server — e.g. "the current list of messages" or "is a request
loading right now." `useState` is how a component says "remember this, and re-render me
when it changes."

**Next.js** adds file-based routing (a file at `app/foo/page.tsx` becomes the `/foo` URL)
and a dev/build toolchain on top of plain React — mostly about not hand-wiring bundler
config for a project this size.

**Tailwind CSS** replaces separate `.css` files with utility classes composed directly in
markup (`className="rounded-lg bg-blue-50 p-3"`) — a stylistic choice to avoid a second
layer of files to keep in sync with components, not a technical requirement.

## A real, necessary backend change: CORS

Browsers block a page on one origin (`localhost:3000`, the Next.js dev server) from calling
an API on a different origin (`localhost:8000`, FastAPI) unless the server explicitly
allows it. This isn't something fixable from the frontend side — `backend/app/main.py` now
has `CORSMiddleware` allowing `http://localhost:3000` specifically (not `"*"`, since
allowing every origin would let any website make authenticated requests using a logged-in
user's browser). Without this, the very first `fetch()` call from the frontend would fail
before ever reaching a route.

## Deliberate scoping decision: no token-by-token streaming yet

Real chat apps show text appearing progressively as it's generated. This isn't built here,
and it's worth explaining why rather than treating it as a silently-cut corner: the agent
graph runs several sequential LLM calls before producing a final answer (classify → maybe
contextualize → generate → confidence check → maybe rewrite and retry) — there's no single
token stream to show progress from until whichever step turns out to be last, and which
step that is varies per query. Real streaming would need the backend restructured to emit
stage events, which is separate, substantial work. Instead, `ChatWindow` shows an explicit
"Thinking..." state during the wait — honest given CPU-bound local generation can take
30-90+ seconds, without pretending to have built something that isn't there. Noted as a
backlog item, not silently skipped.

## Architecture

- **`lib/api.ts`** — every backend call lives here, typed against the actual Pydantic
  response shapes (`SourceRef`, `QueryResponse`, `ModelsResponse`). Every other component
  calls these functions rather than calling `fetch()` directly, so the request/response
  shapes only need to match the backend in exactly one place.
- **`lib/AuthContext.tsx`** — React Context sharing the JWT token across components without
  "prop drilling" (manually passing it down through every component in between, including
  ones that don't care about it). Token lives in `localStorage` — a real, acknowledged
  tradeoff: readable by any JS on the page (an XSS risk an httpOnly cookie wouldn't have),
  acceptable for a dev/portfolio project, not for one handling real user data.
- **`components/LoginForm.tsx`** — a single form that toggles between login and register
  rather than two separate pages, since the fields are identical.
- **`components/ChatWindow.tsx`** — owns the message list, current conversation id, and
  loading state; sends the `conversation_id` from each response back on the next request so
  follow-ups actually land in the same conversation (exercising the Phase 6 memory work).
- **`components/MessageBubble.tsx`** — renders one message. Assistant messages use
  `react-markdown` so citation-formatted text (`[1]`, code blocks) actually renders instead
  of showing raw markdown syntax. Each source shows a `cited: true/false` badge — directly
  surfacing the Phase 5 citation-tracking work in the UI, not just the API response.
- **`components/ModelSelector.tsx`** — fetches `GET /models` live rather than hardcoding a
  model list — if the backend's registry changes, this updates automatically with no
  frontend code change needed.

## How to run

```bash
cd frontend
npm install
npm run dev
```

Then, in a separate terminal, start the backend (must include the CORS fix above):

```bash
uv run uvicorn backend.app.main:app --reload
```

Open http://localhost:3000, register a new account, and try a real conversation —
including a follow-up question in the same thread, to confirm conversation memory actually
works end-to-end through the UI, not just via `curl`.

## Results

<!-- Fill in after running -->

```
npm install: succeeded? any version conflicts or errors?
npm run dev: started cleanly?
Register/login via the UI: worked?
Sending a query: got a response, rendered with citations correctly?
Follow-up in the same conversation: correctly used conversation history?
Model selector: populated with the real registry, switching models worked?
```

## Known limitations / things to revisit

- No token-by-token streaming (see scoping decision above) — a real future enhancement,
  requiring backend changes to emit stage/token events, not just a frontend change.
- `localStorage` token storage is a real, acknowledged security tradeoff — fine for
  dev/portfolio use, not for production with real user data.
- No conversation list/history browsing UI yet — the app only shows the current session's
  messages; past conversations exist in Postgres (Phase 6) but aren't browsable from the UI.
- No visibility into which classification/retry path was taken beyond a small text label —
  a debugging/eval-focused view (showing retrieved chunks before reranking, raw confidence
  scores, etc.) could be a useful "developer mode" addition later, but would clutter a
  normal chat UI if always visible.
- Genuinely unverified against real installed packages — expect at least one real issue on
  first `npm install`, consistent with every other library integration in this project.

## Deliverable

- [x] `frontend/` scaffolded: Next.js + TypeScript + Tailwind config
- [x] `lib/api.ts` — typed API client for all backend endpoints
- [x] `lib/AuthContext.tsx` — shared auth state
- [x] `components/LoginForm.tsx`, `ChatWindow.tsx`, `MessageBubble.tsx`, `ModelSelector.tsx`
- [x] Backend CORS middleware added (necessary prerequisite, not optional)
- [x] TypeScript checked as far as possible without network access — no real syntax errors
      found, all errors traced to missing `node_modules`
- [ ] `npm install` and real run on actual hardware — pending, expect at least one fix needed
- [ ] Results filled in above
