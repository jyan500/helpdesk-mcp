# Helpdesk Copilot

An autonomous support-operations agent: one chat box that answers questions about a
customer's account, answers policy/how-to questions from a help center (RAG), and
takes sandboxed actions (refunds, tickets, emails) behind a human-approval gate.

https://github.com/user-attachments/assets/edc316f6-ef1d-4c6f-b3bc-680c9d69ef1e

---

## What it does

| Ask… | Routed to | How it answers |
| --- | --- | --- |
| "What's the status of alice@example.com's latest order?" | **Account** agent | Queries Postgres via scoped tools |
| "How long do refunds take?" | **Knowledge** agent | RAG over help-center articles, with a citation |
| "Is my latest order eligible for a refund?" | **Account → Knowledge** | Looks up the order, *then* checks the policy |
| "Refund alice's latest order" | **Action** agent | Resolves the order id, then **pauses for approval** before refunding |

---

## Architecture at a glance

```
┌─────────────────────────────┐         SSE (text/event-stream)        ┌──────────────────────────────┐
│   Next.js client (App Router)│ ◄──────────────────────────────────── │   FastAPI backend (uvicorn)   │
│                              │                                        │                              │
│  OrchestratorChat.tsx        │  GET /api/orchestrator/chat?message=…  │  Orchestrator (plan + route) │
│   • chat bubbles (deltas)    │ ─────────────────────────────────────► │        │                     │
│   • Agent Activity panel     │                                        │        ▼                     │
│     (route / tool / result)  │  GET /api/orchestrator/resume?…        │  Specialist agent  ──► Tools │
│   • Approve / Deny card      │ ─────────────────────────────────────► │  (hand-rolled loop)    │     │
└─────────────────────────────┘                                        └────────────────────────┼─────┘
                                                                                                 │
                                              ┌──────────────────────────────────────────────────┤
                                              ▼                          ▼                        ▼
                                    Postgres (async SQLAlchemy)   sentence-transformers     Gemini Flash-Lite
                                    customers / orders / subs     (local embeddings,        (google-genai SDK,
                                    articles / doc_chunks         384-dim, cosine in NumPy) tool calling)
                                    tickets / pending_actions
```

The single entry point is the **orchestrator**. Everything else is internal.

### Request lifecycle

```mermaid
flowchart TD
    U[User message] --> P["plan() — one cheap LLM classify call<br/>→ ordered list of specialists"]
    P -->|"route event"| UI[(client)]
    P --> D{How many steps?}
    D -->|one| S[stream_agent: run that specialist]
    D -->|"two+ (e.g. account → knowledge)"| SEQ["gather context from earlier agents,<br/>fold into the final agent's input"]
    SEQ --> S
    S --> L[["_drive: hand-rolled tool-calling loop"]]
    L -->|"tool / tool_result / delta events"| UI
    L --> G{Gated tool?<br/>issue_refund / send_email}
    G -->|no| EX[execute tool, feed result back to model]
    EX --> L
    G -->|yes| PAUSE["persist PendingAction,<br/>emit approval event, END stream"]
    PAUSE --> UI
    UI -->|Approve / Deny| R["/resume → resume_agent<br/>reloads history, runs (or declines) the tool"]
    R --> L
```

---

## The hand-rolled agent loop

The heart of the project is `server/agent/loop.py` — the tool-calling handshake built
by hand instead of with a framework, so the mechanics are visible:

1. Send the conversation + tool **declarations** to Gemini.
2. If the model replies with a **function call**, the loop executes the matching Python
   tool, appends the result to the history, and calls the model again.
3. Repeat until the model returns plain text (the answer), capped at `MAX_ITERS = 6`.

> **The key idea:** the model never runs your code — it only *asks* you to. The loop
> owns execution. That handshake is what a framework would hide.

A specialist is described as **data** (`AgentConfig`: name, system prompt, tool
declarations, tool registry, and which tools are approval-gated), so the same generic
loop drives every agent. Adding a new specialist is "make one more `AgentConfig`",
not "copy the loop".

Notable details handled in the loop:
- **Streaming** — text chunks are yielded as `delta` events for a typewriter UI.
- **Gemini-3 `thought_signature`** — the opaque token on a function-call part is echoed
  back on the model turn (the API 400s without it).
- **Empty-turn retries** — Flash-Lite occasionally ends a turn with no content; the loop
  retries a few times before giving up.
- **Cost guardrails** — `max_output_tokens` capped, thinking disabled, iteration cap.

---

## Human-in-the-loop (pause & resume)

Irreversible actions (`issue_refund`, `send_email`) don't run immediately. When the
model asks for one, the loop:

1. **Freezes** the agent: serializes the full conversation history into a
   `pending_actions` row in Postgres and returns its `pending_id`.
2. Emits an **`approval`** event with a human-readable summary and **ends the stream**.
3. The UI shows an **Approve / Deny** card.
4. On a decision, the client opens a *new* request to `/api/orchestrator/resume`.
   `resume_agent` reloads the frozen history, appends the tool's result (Approve) or a
   "declined" note (Deny), and **re-enters the same loop** so the model narrates the
   outcome into the same chat bubble.

A "paused agent" is therefore just **a row in a table** — which is what lets the pause
survive a server restart. `create_ticket` is deliberately *ungated* as the contrast case.

---

## Knowledge agent (RAG)

`server/knowledge/*.md` are chunked, embedded locally with `sentence-transformers`
(`all-MiniLM-L6-v2`, 384-dim), and stored in Postgres. `search_docs` embeds the query,
ranks every chunk by cosine similarity (a single NumPy matrix-vector multiply), and
returns the top-k chunks with their article title to cite.

> Vectors are stored as a plain `float8[]` and similarity is computed in Python — fine
> for a tiny doc set. The production path (pgvector's `Vector(384)` with an in-database
> index) is a deliberate later swap; see `server/db/models.py:DocChunk`.

---

## The SSE event contract

The orchestrator and every specialist emit the **same** event shape, so one frontend
renders any agent. Each event is a JSON object in the SSE `data:` field:

| Event | Payload | Meaning |
| --- | --- | --- |
| `route` | `{intent}` | Which specialist(s) the orchestrator picked (e.g. `"account -> knowledge"`) |
| `tool` | `{name, args}` | A tool is about to run |
| `tool_result` | `{name, result}` | What that tool returned (powers the Agent Activity panel) |
| `delta` | `{text}` | A chunk of the answer (typewriter streaming) |
| `approval` | `{pending_id, name, args, summary}` | A gated action paused; stream ends here |
| `done` | — | Stream finished |

The client (`client/app/components/OrchestratorChat.tsx`) keeps every event of a turn
in a `trace` array and renders it as a persistent **Agent Activity** timeline — the
"see the agent work" panel — alongside the chat.

---

## Data model

| Table | Purpose |
| --- | --- |
| `customers`, `orders`, `subscriptions` | Account data the read tools query |
| `articles`, `doc_chunks` | Help-center docs + their per-chunk embeddings (RAG) |
| `tickets` | Rows the *ungated* `create_ticket` action inserts |
| `pending_actions` | A frozen, resumable agent awaiting human approval |

ORM rows are never handed to the model directly. Tools convert them into small Pydantic
schemas that **whitelist** the exposed fields — the answer to "how do you keep the LLM
from seeing data it shouldn't?"

---

## Tech stack

- **Frontend:** Next.js 16 (App Router) + TypeScript; consumes SSE via `EventSource`.
- **Backend:** FastAPI + Pydantic + async SQLAlchemy, served with uvicorn.
- **Database:** Postgres (async via `asyncpg`).
- **Embeddings:** `sentence-transformers` running locally (free, no per-token cost).
- **LLM:** Gemini Flash-Lite via the `google-genai` SDK, with tool calling. The model id
  is set in `server/utils/constants.py` (`USE_MODEL`).
- **Integrations:** all mocked/sandboxed — refunds flip a DB status, emails are printed.

---

## Project layout

```
server/
  main.py                 # FastAPI app + the 3 SSE endpoints
  agent/
    orchestrator.py       # plan() → route → run one specialist or a sequential pipeline
    loop.py               # AgentConfig + the hand-rolled tool-calling loop (_drive)
    agents.py             # the AGENTS registry (account / knowledge / action)
    pending.py            # (de)serialize + CRUD for the pending_actions row
  tools/
    account.py            # get_customer / get_orders / get_subscription
    knowledge.py          # search_docs (RAG retrieval)
    action.py             # issue_refund / create_ticket / send_email (+ approval set)
  db/
    models.py             # SQLAlchemy ORM models (the tables)
    session.py            # async engine + session factory
    seed.py               # `python -m db.seed`   — fake customers/orders/subs
    ingest.py             # `python -m db.ingest` — chunk + embed + store the articles
  knowledge/*.md          # the help-center source documents
  utils/                  # genai client, embeddings, constants

client/
  app/page.tsx                          # mounts the chat
  app/components/OrchestratorChat.tsx   # chat + Agent Activity panel + approval card
```

---

## Getting started

**Prerequisites:** Python 3.11+, Node 18+, a running Postgres, and a Gemini API key.

### Backend

```bash
cd server
python -m venv .venv && source .venv/Scripts/activate   # Windows Git Bash
pip install -r requirements.txt
```

Set two environment variables (e.g. in `server/.env`):

```
DATABASE_URL=postgresql+asyncpg://USER:PASSWORD@localhost:5432/helpdesk
GEMINI_API_KEY=your-key-here
```

Seed the data, build the RAG index, then run the API:

```bash
python -m db.seed      # drops + recreates tables, loads fake customers/orders
python -m db.ingest    # chunks + embeds the knowledge/*.md articles
uvicorn main:app --reload   # serves on http://localhost:8000  (docs at /docs)
```

### Frontend

```bash
cd client
npm install
npm run dev            # http://localhost:3000
```

Open <http://localhost:3000> and try: *"What's alice@example.com's latest order?"*,
*"How long do refunds take?"*, or *"Refund alice's latest order"* (watch it pause for
approval).

---

## Cost guardrails

Spend is kept near zero by design: local embeddings (no per-token cost), capped
`max_output_tokens`, a capped agent-loop iteration count, thinking disabled on the
cheap classify/loop calls, and every integration mocked or in test mode. The real
ceiling is a per-project **Spend Cap** set in Google AI Studio.
