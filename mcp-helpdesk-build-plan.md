# MCP Helpdesk — 2–3 Week Build Plan

A focused, learning-first roadmap that takes the helpdesk tools you built **by hand** and re-exposes
them across the **Model Context Protocol (MCP)** — then drives them with a **framework** instead of a
hand-rolled loop. This is deliberately smaller than the original 12-week build: one new fundamental,
done properly.

The guiding principle, same as before: **walking skeleton first, then deepen one slice at a time.**
You want a one-tool server that Claude Desktop can actually call by the end of Phase 0, even though it
does almost nothing — every later phase makes one slice real.

> **Still a learning project, not production.** Favor understanding the protocol boundary over
> robustness. Everything stays mocked/sandboxed exactly as in the original repo — no paid or
> irreversible real-world actions.

---

## The one new idea

In the original project you were always the **caller** of tools: you wrote the loop, the dispatch, the
schemas. MCP flips you to the other side of the boundary — you become the **provider**. The payoff you
are building toward:

> Once your tools live behind an MCP server, *any* MCP client can use them with **zero glue code** —
> Claude Desktop, Claude Code, and a Pydantic AI agent all consuming the exact same server.

That "write once, consumed by three unrelated clients" moment is the whole lesson. Everything else is
in service of it.

### What's reused vs. what's new

| From helpdesk-copilot (reused as-is) | New in this project |
| --- | --- |
| The tool functions (`refund`, `create_ticket`, `send_email`, `search_docs`) | Exposing them as an **MCP server** (you're the provider now) |
| Postgres + seeded data, the knowledge base, embeddings | MCP **resources** (read-only context) and **prompts** (reusable templates) |
| Tool schemas / descriptions you wrote by hand | A **framework** (Pydantic AI) that auto-discovers them — no hand-rolled loop |
| The human-in-the-loop instinct (`REQUIRES_APPROVAL`) | How approval/elicitation looks across the protocol boundary |

---

## Stack at a glance

- **MCP server:** the standalone **`fastmcp`** package (FastMCP 2.x) — decorator-based
  (`@mcp.tool`, `@mcp.resource`, `@mcp.prompt`). *Not* the FastMCP bundled inside the older `mcp`
  SDK; the standalone project is the actively maintained one.
- **Transports:** **stdio** (local, single-client, simplest — start here) and **Streamable HTTP**
  (the modern remote transport, a single `/mcp` endpoint). **Ignore SSE entirely** — that transport
  reached end-of-life in early 2026.
- **Framework client:** **Pydantic AI**, acting as an MCP client via `MCPToolset` /
  `MCPServerStreamableHTTP` / `StdioTransport`. It wraps the FastMCP client, so the stack is
  symmetric: FastMCP server ↔ Pydantic AI client, same ecosystem.
- **Reused backend:** the existing Postgres + `pgvector`, `sentence-transformers` embeddings, seeded
  data, and knowledge base — all carried over from the fork.
- **LLM:** same provider-agnostic, cheapest-Gemini-Flash-Lite setup, same per-project spend cap.

> **Verify before you rely on versions.** FastMCP 2.x was mid-release in mid-2026 (beta ~2026-06-30,
> stable v2 ~2026-07-27). Pin the versions you install and re-check the FastMCP and Pydantic AI MCP
> docs for the current API shape before coding each phase — the decorator and client signatures were
> still settling.

---

## Phase 0 — Hello, MCP (Days 1–2)

**Goal:** a one-tool MCP server that Claude Desktop actually calls.

- `pip install fastmcp` into the server environment. Skim the FastMCP quickstart.
- Write the smallest possible server: a `FastMCP("helpdesk")` instance with a single trivial
  `@mcp.tool` (e.g. `ping()` or reuse `create_ticket` if the DB is already up), running over **stdio**.
- Register it in Claude Desktop's MCP config and confirm the tool shows up and runs from a chat.
- Run it through FastMCP's dev/inspector tooling too, so you can see the raw tool list the client
  receives.

**Deliverable:** ask Claude Desktop something that triggers your tool; watch it call your local server.

**Learning checkpoint:** what does the client actually receive when it "discovers" your tool? Can you
point to where the name, description, and arg schema come from — and how that maps to the
`FunctionDeclaration`s you wrote by hand in the old repo?

---

## Phase 1 — Port the helpdesk tools (Days 3–5)

**Goal:** all the real tools, served over MCP.

- Wrap the existing tool functions (`issue_refund`, `create_ticket`, `send_email`, `search_docs`) as
  `@mcp.tool`s. Reuse the bodies verbatim — only the *registration* changes.
- Decide how the DB session gets in. In the old loop you injected `session` as the first arg; here the
  tool runs inside the server process, so open/close a session inside each tool (or via a FastMCP
  lifespan/context). This contrast — who owns the session now — is worth a short design note.
- Keep `search_docs` pointed at the same `pgvector` data; confirm retrieval still works end-to-end.

**Deliverable:** from Claude Desktop, run a refund and a knowledge-base lookup against your real
seeded Postgres, through MCP.

**Learning checkpoint:** the model's tool descriptions are still load-bearing — does moving to MCP
change *what* you write in them, or just *where*? (Mostly where.)

---

## Phase 2 — Resources and prompts (Days 6–8)

**Goal:** use the two MCP primitives you've never touched.

- **Resource** (read-only context, like a GET): expose something the client can *pull in* rather than
  call as an action — e.g. `ticket://{id}` returning a ticket's details, or a customer record.
  This is the conceptual opposite of a tool: no side effect, it loads data into context.
- **Prompt** (reusable template): expose a server-provided prompt such as `triage_ticket` that
  packages the instructions for triaging a support ticket, so any client can invoke a consistent
  workflow without re-typing it.
- Note the distinction in a design comment: **tools = actions (POST), resources = context (GET),
  prompts = reusable interaction templates.** Knowing *which primitive fits* is the Phase 2 judgment.

**Deliverable:** in Claude Desktop, attach a `ticket://` resource into the conversation and invoke the
`triage_ticket` prompt.

**Learning checkpoint:** why is "look up this ticket" better modeled as a resource than a tool? When
does a read belong in each bucket?

---

## Phase 3 — Drive it with a framework (Days 9–11)

**Goal:** the payoff — a Pydantic AI agent consuming your server with no hand-rolled loop.

- `pip install pydantic-ai`. Build a small agent that connects to your MCP server over **stdio**
  (`StdioTransport`, launching the server as a subprocess) and registers it as a toolset.
- Ask it a multi-step question ("refund the latest order for alice@example.com and email her") and
  watch the **framework** run the tool-calling loop you previously wrote by hand — discovery,
  dispatch, result-feeding, iteration.
- Write the comparison note for your eventual blog post: line up `server/agent/loop.py` (kept in this
  fork for exactly this reason) against what Pydantic AI does for free, and call out what the
  framework hides — and what it *doesn't*.

**Deliverable:** a multi-step request handled end-to-end by the framework agent against your MCP server.

**Learning checkpoint:** which parts of your hand-built loop did the framework replace, and which
parts (tool *design*, approval policy, prompts) are still yours to own?

---

## Phase 4 — Flip the transport + multi-client (Days 12–14)

**Goal:** see the boundary's real value — one server, many clients, remote.

- Switch the server to **Streamable HTTP** (single `/mcp` endpoint) and run it as a standalone
  process. Point the Pydantic AI agent at it via `MCPServerStreamableHTTP(url=...)`.
- Now connect a **third** client to the *same running server* — e.g. Claude Code — and confirm all
  three (Claude Desktop, Pydantic AI, Claude Code) use it with no per-client tool code.
- Briefly contrast the two transports in a note: stdio is a local subprocess (one client, simplest);
  Streamable HTTP is a network endpoint (many clients, the remote standard). You don't need auth or
  hardening — that's a production concern out of scope here.

**Deliverable:** the same server, running once over HTTP, used by two+ unrelated clients at once.

**Learning checkpoint:** can you explain *why* the protocol exists by pointing at what you just did —
the same tools, zero re-integration, across clients that know nothing about each other?

---

## Optional Phase 5 — Polish & write-up

- Tidy the README around "I rebuilt my hand-wired agent's tools as an MCP server."
- Short blog-style write-up: the provider-side mental model, tools vs. resources vs. prompts, and the
  hand-built-loop vs. framework-loop comparison (your strongest interview talking point here).
- **Stretch:** add elicitation/approval at the protocol level for the gated actions
  (`issue_refund`, `send_email`) and compare it to the frontend Approve/Deny gate you built before.

---

## Cost guardrails (unchanged from the original)

- Embeddings stay local; all integrations stay mocked/test-mode.
- Cap `max_output_tokens` and the framework agent's max iterations — a framework loop can run away
  just as easily as a hand-built one.
- Keep the per-project Gemini **Spend Cap** in place.
- Log token counts/latency per request so spend stays visible.
