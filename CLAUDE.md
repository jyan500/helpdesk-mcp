# CLAUDE.md

Guidance for Claude Code when working in this repository.

## What this project is

**MCP Helpdesk** is a follow-on **learning project** that takes the support-operations agent from
[`helpdesk-copilot`](../helpdesk-copilot) and rebuilds its tool layer on the **Model Context Protocol
(MCP)**. The goal is to learn the *provider* side of the tool boundary and to let a **framework** run
the agent loop, instead of the hand-built loop from the original project. It follows the roadmap in
[`mcp-helpdesk-build-plan.md`](./mcp-helpdesk-build-plan.md) — read that file first; it is the source
of truth for scope and sequencing.

> **This is not a production system.** It is paced for part-time learning, with the explicit goal of
> *understanding* how each piece works rather than shipping something hardened. Favor clarity,
> teachability, and going deep on one slice over robustness, abstraction, or completeness. Don't add
> production concerns (auth, scaling, exhaustive error handling, CI/CD) unless the current phase calls
> for them or the user asks.

## This repo is a fork — what's already here

This repo was forked from `helpdesk-copilot` as a starting point, so unlike a fresh project the
backend is **already built**:

- `server/tools/` — the working tool functions (`issue_refund`, `create_ticket`, `send_email`,
  `search_docs`). **These get re-exposed as MCP tools; reuse the bodies, change only the registration.**
- `server/db/`, `server/knowledge/`, `server/utils/embeddings.py` — Postgres + `pgvector`, seeded
  data, the knowledge base, and local embeddings. Carried over and reused as-is.
- `server/agent/` (`loop.py`, `orchestrator.py`, etc.) — the **hand-built agent loop, kept on purpose
  as a reference.** Do not extend it; it exists so you can diff "hand-built loop" against the framework
  loop. The framework replaces it.
- `client/` — the original Next.js app. Not central to this project; leave it unless a phase needs it.

## Guiding principle

Build a thin end-to-end **walking skeleton** first (one MCP tool a real client can call), then deepen
**one slice at a time**. Each phase makes one slice real.

**This time, DO use a framework for the agent loop.** That is the deliberate inversion of the original
project: you already understand the tool-calling handshake by hand, so here the lesson is the MCP
boundary and letting **Pydantic AI** drive the loop. Don't re-implement loop/dispatch logic that the
framework provides.

## The one new idea

In the original project you were always the **caller** of tools. MCP flips you to the **provider**
side. The payoff: once the tools live behind an MCP server, *any* client — Claude Desktop, Claude
Code, a Pydantic AI agent — can use them with zero glue code. Keep that "write once, consumed by many
clients" goal in view; it's what every phase builds toward.

## Stack

- **MCP server** (`server/`): the standalone **`fastmcp`** package (FastMCP 2.x), decorator-based
  (`@mcp.tool`, `@mcp.resource`, `@mcp.prompt`). NOT the FastMCP bundled inside the older `mcp` SDK —
  the standalone project is the maintained one.
- **Transports:** **stdio** (local, simplest — start here) and **Streamable HTTP** (modern remote,
  single `/mcp` endpoint). **Ignore SSE** — that transport reached end-of-life in early 2026.
- **Framework client:** **Pydantic AI** as an MCP client (`MCPToolset`, `MCPServerStreamableHTTP`,
  `StdioTransport`). Symmetric stack: FastMCP server ↔ Pydantic AI client, same ecosystem.
- **Reused backend:** Postgres + `pgvector` (Docker), `sentence-transformers` embeddings (local,
  free), seeded data, knowledge base — all from the fork.
- **LLM:** cheapest current Gemini Flash-Lite tier via an OpenAI-compatible interface; provider is a
  base-URL + key change, not a rewrite.

## Cost guardrails (apply from day one)

Costs must stay near zero:

- Cap `max_output_tokens` and the **framework agent's** max iterations — a framework loop runs away
  just as easily as a hand-built one.
- Log input/output token counts and latency per request so spend is visible before dashboards update.
- Keep embeddings local; keep all actions mocked/sandboxed (no Stripe, no SMTP — same as the fork).
- The real spend control is the per-project **Spend Cap** in Google AI Studio (a config step).

## Phase roadmap (see build plan for full detail)

- **Phase 0 (Days 1–2):** Hello, MCP — a one-tool `fastmcp` server over stdio, called from Claude Desktop.
- **Phase 1 (Days 3–5):** Port the real helpdesk tools as `@mcp.tool`s against the seeded Postgres.
- **Phase 2 (Days 6–8):** The two new primitives — a **resource** (read-only context) and a **prompt** (template).
- **Phase 3 (Days 9–11):** Drive the server with a **Pydantic AI** agent; compare to the kept `server/agent/loop.py`.
- **Phase 4 (Days 12–14):** Flip to **Streamable HTTP**; one running server used by 2+ clients at once.
- **Phase 5 (optional):** Polish, write-up, stretch: protocol-level approval/elicitation for gated actions.

When starting work, identify which phase is active and stay within its scope.

## Working conventions

- **Verify live MCP details before relying on them.** FastMCP 2.x and the Pydantic AI MCP client were
  still settling in mid-2026 (FastMCP v2 stable targeted ~2026-07-27). Pin versions and re-check the
  FastMCP / Pydantic AI docs for the current API shape before coding each phase — don't hardcode
  signatures from memory.
- **Pick the right primitive:** tools = actions (POST, side effects), resources = read-only context
  (GET), prompts = reusable interaction templates. Choosing correctly is a core lesson of Phase 2.
- **Security mindset even in a toy:** scope DB queries; never hand the model raw SQL execution; keep
  irreversible actions (`issue_refund`, `send_email`) gated behind explicit approval.
- **Learning-first:** when a concept is new, a small throwaway example to understand it is encouraged.
  Leave short design notes — especially the hand-built-loop vs. framework-loop comparison — they feed
  the eventual README and blog post.

## Commands

Server env lives in `server/.venv` (Python 3.14, `fastmcp` 3.x — note: actual installed major is
**3.x**, not the 2.x the build plan anticipated; the decorator/`run` API was verified against 3.4.2).

- **Run the MCP server (stdio):** `server/.venv/Scripts/python.exe server/mcp_server.py`
- **Inspect the raw tool list:** `server/.venv/Scripts/fastmcp.exe dev server/mcp_server.py`
- **Phase 0 client:** registered with **Claude Code** (not Claude Desktop — not installed here) via the
  project-scoped `.mcp.json` at repo root. Tools surface as `mcp__helpdesk__ping` / `mcp__helpdesk__echo`
  after approving the server in an interactive `claude` session. Check status with `claude mcp list`.

The reused backend's setup (Docker Postgres, `python -m db.seed`, `python -m db.ingest`) still applies
and comes online in Phase 1. Update this section as later phases (Pydantic AI client, HTTP transport)
get scaffolded.
