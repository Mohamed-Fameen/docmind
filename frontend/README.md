# DocMind Frontend

A minimal Next.js chat UI for the DocMind backend — login/register, a chat window with
citations, and a model selector pulled live from the backend's model registry.

## Setup

```bash
cd frontend
npm install
npm run dev
```

Then open http://localhost:3000. Make sure the backend is running first:

```bash
# from the project root, in a separate terminal
uv run uvicorn backend.app.main:app --reload
```

## What to expect on first run

This code was written and typechecked with `tsc` in a sandbox with **no network access**,
meaning `npm install` was never actually run against it before now. Every dependency
version in `package.json` is a reasonable current choice, not a pinned, tested-together
combination — expect a real chance of at least one version mismatch or missing peer
dependency on first install, consistent with what happened at every other "first real run"
point in this project (LangGraph, passlib/bcrypt, RAGAS all needed at least one fix once
actually executed). Paste whatever `npm install` or `npm run dev` produce if anything looks
wrong, and we'll fix it against the real error rather than guess further.

## Project structure

```
frontend/
├── app/
│   ├── layout.tsx       # root layout, wraps everything in AuthProvider
│   ├── page.tsx         # shows LoginForm or ChatWindow depending on auth state
│   └── globals.css      # Tailwind directives
├── components/
│   ├── LoginForm.tsx    # email/password, toggles between login and register
│   ├── ChatWindow.tsx   # message list, input box, send logic
│   ├── MessageBubble.tsx # renders one message + its citations
│   └── ModelSelector.tsx # dropdown populated from GET /models
└── lib/
    ├── api.ts           # all fetch() calls to the backend, typed
    └── AuthContext.tsx  # shares the JWT token across components
```
