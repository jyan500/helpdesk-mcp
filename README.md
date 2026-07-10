# MCP Helpdesk

A follow-on to [`helpdesk-copilot`](../helpdesk-copilot) that **rebuilds the tool layer on the
[Model Context Protocol (MCP)](https://modelcontextprotocol.io)** and lets a **framework** run the
agent loop instead of a hand-rolled one.

The helpdesk tools remain the same, answers account questions, answers policy/how-to questions from a help center
(RAG), and takes sandboxed actions (refunds, tickets, emails) behind a human-approval gate, but the
tools now live behind an **MCP server** that *any* client can use, and the tool-calling loop is driven
by **Pydantic AI** rather than code written by hand.

<!-- demo video goes here -->
https://github.com/user-attachments/assets/87fe8e4a-8090-4785-acbd-9b95148b00b5
---

## The one new idea

In the original project, tools were called manually: the loop, the dispatch, and
the JSON schemas. MCP now controls the loop, and instead is merely the provider for the tools written.

> Once your tools live behind an MCP server, *any* MCP client can use them with **zero glue code** —
> Claude Code, Claude Desktop, and a Pydantic AI agent all consuming the exact same server.

That "write once, consumed by three unrelated clients" moment is the whole lesson. Everything else is
in service of it.

---

## What changed from `helpdesk-copilot`

The business logic is identical; **where the boundaries sit** is what moved.

| | `helpdesk-copilot` (original) | **`mcp-helpdesk` (this repo)** |
| --- | --- | --- |
| Your role at the tool boundary | tool **caller** | tool **provider** |
| The agent loop | **hand-written** (`agent/loop.py`) | run by the **framework** (Pydantic AI) |
| Tool schemas | hand-written `FunctionDeclaration`s | **generated** from Python type hints |
| Who can use the tools | only your app's loop | **any MCP client**, no per-client code |
| Routing | a hand-written **orchestrator** (`plan()` → route) | the client/model decides — no orchestrator |
| Transport | HTTP + SSE to a Next.js UI | **MCP over stdio and Streamable HTTP** |
| Approval gate | `PendingAction` row + `/resume` endpoint + UI card | **MCP elicitation** (in-band, client-owned UX) |
| The LLM | Gemini, driven by your loop | the **client's** model (Gemini via Pydantic AI, or Claude via Claude Code) |

### Reused as-is (carried over from the fork)

The **tool bodies** (`issue_refund`, `create_ticket`, `send_email`, `search_docs`, the account
lookups), **Postgres + seeded data**, the **knowledge base**, and **local embeddings** are all reused
unchanged. Re-exposing them as MCP primitives changed only the *registration*, never the logic.

### Kept on purpose as a reference

`server/agent/` (the hand-rolled `loop.py`, `orchestrator.py`, `pending.py`) and the `client/` Next.js
app are **retained but not part of the MCP path**. They're the "before" picture — kept so you can diff
the hand-built loop against what the framework does for free.

---

## Architecture at a glance

```
      MCP CLIENTS (each brings its own model + runs the loop)
 ┌───────────────┐   ┌───────────────┐   ┌──────────────────────────┐
 │  Claude Code  │   │ Claude Desktop│   │  Pydantic AI agent        │
 │  (Claude)     │   │  (Claude)     │   │  (pydantic_agent.py, Gemini)│
 └──────┬────────┘   └──────┬────────┘   └───────────┬──────────────┘
        │                   │                        │
        └─────────── MCP (stdio  or  Streamable HTTP /mcp) ───────────┐
                                                                      ▼
                                            ┌───────────────────────────────────┐
                                            │   FastMCP server (mcp_server.py)   │
                                            │                                    │
                                            │   tools      get_customer, …,      │
                                            │              issue_refund (gated)  │
                                            │   resource   ticket://{id}         │
                                            │   prompt     triage_ticket         │
                                            └───────────────┬────────────────────┘
                                                            │  (reused backend)
                        ┌───────────────────────────────────┼───────────────────────┐
                        ▼                                    ▼                       ▼
              Postgres (async SQLAlchemy)         sentence-transformers        (no LLM here —
              customers / orders / subs           local embeddings, 384-dim     the model lives
              articles / doc_chunks / tickets     cosine in NumPy               on the CLIENT)
```

The server is **model-agnostic**: it provides tools/resources/prompts; whichever client connects brings
its own model and runs the loop. That decoupling is the entire reason the protocol exists.

---

## The three MCP primitives

Choosing the right primitive is a core lesson. All three are served by the one `mcp_server.py`:

| Primitive | Semantics | Initiated by | In this repo |
| --- | --- | --- | --- |
| **Tool** | an action / side effect (POST) | the **model**, mid-loop | `get_customer`, `get_orders`, `get_subscription`, `search_docs`, `issue_refund`, `create_ticket`, `send_email` |
| **Resource** | read-only context (GET) | the **client/user**, by URI | `ticket://{ticket_id}` — a ticket's details, loaded into context |
| **Prompt** | a reusable interaction template | the **user**, explicitly | `triage_ticket` — packaged triage instructions (a slash-command in Claude Code) |

Each tool wrapper is thin: it opens its own DB session and delegates to the reused body in
`server/tools/*.py`. The model-facing description lives in the wrapper **docstring**, and FastMCP
**generates the input schema from the type hints** — the hand-written declarations from the original
repo are gone.

---

## Framework loop vs. hand-built loop

The payoff of the project. Ask the Pydantic AI agent a multi-step question:

```
"Refund the latest order for alice@example.com and email her a confirmation."
```

…and the framework autonomously chains four tools —
`get_customer → get_orders → issue_refund → send_email` — across five model requests, with **no loop,
no dispatch, and no routing code**. That is exactly what `agent/loop.py`'s `_drive` did by hand
(iteration cap, streaming chunk parsing, Gemini-3 `thought_signature` echoing, history bookkeeping,
tool dispatch). In this project it collapses to:

```python
agent = Agent(model, toolsets=[helpdesk_toolset], instructions=SYSTEM_PROMPT, ...)
result = await agent.run(question, usage_limits=UsageLimits(request_limit=6))
```

**What the framework now owns:** the loop, message/history bookkeeping, tool dispatch, the iteration
cap. **What is still yours:** tool design, the system prompt, model choice, and cost guardrails. The
~330-line `loop.py` shrinking to `Agent(...) + await agent.run(...)` — while the parts that *didn't*
shrink stay yours — is the sharpest takeaway of the project.

---

## Transports: one server, many clients

- **stdio** (local, simplest) — the client *launches the server as a subprocess* and talks over
  stdin/stdout. One subprocess per client; the client owns the lifecycle.
  Run: `python mcp_server.py`.
- **Streamable HTTP** (the modern remote transport) — *one long-running server* on a single `/mcp`
  endpoint that many clients **connect** to at once.
  Run: `python mcp_server.py --http` → `http://127.0.0.1:8000/mcp`.

The Phase 4 deliverable: the *same running HTTP server* used by a Pydantic AI agent **and** Claude Code
simultaneously, with zero per-client tool code — the "write once, any client" claim made literal. The
only thing that changed to get there was the transport line on each side; not a single tool, resource,
or prompt.

---

## Human-in-the-loop, at the protocol level

Irreversible actions (`issue_refund`, `send_email`) are gated with **MCP elicitation**: before doing
the deed, the *server* pauses inside the tool call and asks the *client* to confirm with the user.

```python
decision = await ctx.elicit(f"{describe_action('issue_refund', {'order_id': order_id})}?",
                            response_type=None)
if not isinstance(decision, AcceptedElicitation):
    return {"ok": False, "order_id": order_id, "status": "denied"}
# ...perform the action...
```

The client supplies an **elicitation handler** (see `pydantic_agent.py`) that renders the Approve/Deny
prompt; Claude Code renders it natively. `create_ticket` is deliberately **ungated** as the contrast.

How that compares to the original gate:

```mermaid
flowchart LR
    subgraph orig["helpdesk-copilot — out-of-band, stateful"]
      A1[gated tool] --> A2[persist PendingAction row] --> A3[END stream, show Approve/Deny card]
      A3 --> A4["/resume endpoint reloads history, runs or declines"]
    end
    subgraph mcp["mcp-helpdesk — in-band, synchronous"]
      B1[gated tool] --> B2["ctx.elicit(...)"] --> B3[client asks the human] --> B4[answer returns; tool continues]
    end
```

The original survives a server restart (the paused agent is a row in a table); MCP elicitation is
simpler but synchronous (the call blocks on the human). Either way, **the policy — which tools are
gated — stays yours** (`tools/action.REQUIRES_APPROVAL`); the protocol only standardizes the *asking*.

---

## Knowledge agent (RAG) — reused

`server/knowledge/*.md` are chunked, embedded locally with `sentence-transformers`
(`all-MiniLM-L6-v2`, 384-dim), and stored in Postgres. `search_docs` embeds the query, ranks every
chunk by cosine similarity (a single NumPy matrix-vector multiply), and returns the top-k chunks with
their article title to cite.

> Vectors are stored as a plain `float8[]` and similarity is computed in Python — fine for a tiny doc
> set. The production path (pgvector's `Vector(384)` with an in-database index) is a deliberate later
> swap; see `server/db/models.py:DocChunk`.

---

## Data model

| Table | Purpose |
| --- | --- |
| `customers`, `orders`, `subscriptions` | Account data the read tools query |
| `articles`, `doc_chunks` | Help-center docs + their per-chunk embeddings (RAG) |
| `tickets` | Rows the *ungated* `create_ticket` action inserts; also read by the `ticket://` resource |
| `pending_actions` | Used only by the **kept** hand-built loop; the MCP path gates in-band via elicitation instead |

ORM rows are never handed to the model directly. Tools convert them into small Pydantic schemas that
**whitelist** the exposed fields — the answer to "how do you keep the LLM from seeing data it
shouldn't?"

---

## Tech stack

- **MCP server:** the standalone **`fastmcp`** package (FastMCP 3.x), decorator-based
  (`@mcp.tool`, `@mcp.resource`, `@mcp.prompt`).
- **Transports:** **stdio** (local) and **Streamable HTTP** (`/mcp`, multi-client). SSE is
  end-of-life and unused.
- **Framework client:** **Pydantic AI** as an MCP client (`MCPToolset` + `StdioTransport` /
  `StreamableHttpTransport`), driving a `GoogleModel` (Gemini Flash-Lite).
- **Reused backend:** Postgres + `asyncpg`, `sentence-transformers` embeddings (local, free), seeded
  data, knowledge base.
- **LLM:** Gemini Flash-Lite; the model id is `USE_MODEL` in `server/utils/constants.py`. With Claude
  Code / Claude Desktop as the client, the model is Claude instead — same server.
- **Integrations:** all mocked/sandboxed — refunds flip a DB status, emails are printed.

---

## Project layout

```
.mcp.json                 # registers the `helpdesk` server for Claude Code (stdio or http)

server/
  mcp_server.py           # ← THE MCP SERVER: tools + ticket:// resource + triage_ticket prompt
  pydantic_agent.py       # ← THE FRAMEWORK CLIENT: Pydantic AI agent that drives the server
  tools/
    account.py            # get_customer / get_orders / get_subscription   (bodies reused)
    knowledge.py          # search_docs (RAG retrieval)                    (body reused)
    action.py             # issue_refund / create_ticket / send_email + REQUIRES_APPROVAL
  db/
    models.py             # SQLAlchemy ORM models (the tables)
    session.py            # async engine + session factory
    seed.py               # `python -m db.seed`   — fake customers/orders/subs
    ingest.py             # `python -m db.ingest` — chunk + embed + store the articles
  knowledge/*.md          # the help-center source documents
  utils/                  # embeddings, genai client, constants

  agent/                  # KEPT FOR REFERENCE (the hand-built "before"): loop.py, orchestrator.py, …
client/                   # KEPT FOR REFERENCE: the original Next.js UI (not part of the MCP path)
```

---

## Getting started

**Prerequisites:** Python 3.11+ (3.14 works), a running Postgres, and a Gemini API key.

### 1. Install

```bash
cd server
python -m venv .venv && source .venv/Scripts/activate      # Windows Git Bash
pip install -r requirements.txt fastmcp pydantic-ai        # fastmcp + pydantic-ai are the MCP-era deps
```

### 2. Configure `server/.env`

```
DATABASE_URL=postgresql+asyncpg://USER:PASSWORD@localhost:5432/helpdesk
GEMINI_API_KEY=your-key-here
```

### 3. Seed the database + build the RAG index

```bash
python -m db.seed      # drops + recreates tables, loads fake customers/orders/subs
python -m db.ingest    # chunks + embeds the knowledge/*.md articles
```

### 4. Run the server, and point clients at it

**Local (stdio) — Claude Code launches it for you** via `.mcp.json`:

```bash
python mcp_server.py --list     # sanity-check the tools/resources/prompts it exposes
# then, in an interactive `claude` session, approve the `helpdesk` server
```

**Multi-client (Streamable HTTP) — one running server, many clients:**

```bash
python -u mcp_server.py --http                 # serves http://127.0.0.1:8000/mcp  (-u = unbuffered logs)
python pydantic_agent.py                        # a Pydantic AI + Gemini client
# and/or point Claude Code at the same URL via .mcp.json ({"type":"http","url":".../mcp"})
```

Then try: *"What's alice@example.com's latest order?"*, *"How long do refunds take?"*, or *"Refund
alice's latest order"* (watch it pause for an elicitation approval).

---

## Cost guardrails

Spend is kept near zero by design: local embeddings (no per-token cost), capped `max_output_tokens`, a
capped framework-loop iteration count (`UsageLimits(request_limit=…)` — a framework loop runs away just
as easily as a hand-built one), and every integration mocked or in test mode. The real ceiling is a
per-project **Spend Cap** set in Google AI Studio.

---

## Build phases

Built one thin slice at a time (see `mcp-helpdesk-build-plan.md`):

- **Phase 0** — one-tool `fastmcp` server over stdio, called from Claude Code.
- **Phase 1** — port the real helpdesk tools as `@mcp.tool`s against the seeded Postgres.
- **Phase 2** — the two new primitives: a **resource** (`ticket://`) and a **prompt** (`triage_ticket`).
- **Phase 3** — drive the server with a **Pydantic AI** agent; compare to the kept `agent/loop.py`.
- **Phase 4** — flip to **Streamable HTTP**; one running server used by 2+ clients at once.
- **Phase 5** — protocol-level **approval via elicitation** for the gated actions.
