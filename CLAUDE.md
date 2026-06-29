# CLAUDE.md

Guidance for Claude Code when working in this repository.

## What this project is

**Helpdesk Copilot** is an autonomous support-operations agent built as a **learning project**
for getting hands-on with modern AI tooling (agents, tool calling, RAG, orchestration,
human-in-the-loop). It follows the 12-week roadmap in
[`helpdesk-copilot-build-plan.md`](./helpdesk-copilot-build-plan.md) — read that file first;
it is the source of truth for scope and sequencing.

> **This is not a production system.** It is paced for part-time learning, with the explicit goal
> of *understanding* how each piece works rather than shipping something hardened. Favor clarity,
> teachability, and "build it by hand to learn it" over robustness, abstraction, or completeness.
> Don't add production concerns (auth, scaling, exhaustive error handling, CI/CD) unless the
> current phase calls for them or the user asks.

## Guiding principle

Build a thin end-to-end **walking skeleton** first (chat → backend → LLM → streamed response),
then deepen **one slice at a time**. Each phase makes one slice real.

**Build the agent loop by hand through Phases 1–4 — do not reach for an agent framework.**
The whole point is to understand the tool-calling handshake the framework would hide. Frameworks
(Pydantic AI, LangGraph) are an optional stretch goal *after* the fundamentals are solid.

## Stack

- **Frontend** (`client/`): Next.js (App Router) + TypeScript; streams responses over SSE.
- **Backend** (`server/`): FastAPI + Pydantic + async SQLAlchemy, served with uvicorn.
- **Database:** Postgres + `pgvector` (run locally via Docker).
- **Embeddings:** `sentence-transformers` locally (free, no per-token cost).
- **LLM:** cheapest current Gemini Flash-Lite tier, accessed through an **OpenAI-compatible**
  chat-completions interface so the provider is a base-URL + key change, not a rewrite.
- **Integrations:** Stripe **test mode**, ticketing free tier or mock, email free tier or mock.
  Never trigger a paid or irreversible real-world action.

Both `client/` and `server/` are currently empty scaffolding — the project is at Phase 0.

## Cost guardrails (apply from day one)

Costs must stay near zero. When writing LLM code:

- Cap `max_output_tokens`.
- Cap agent-loop iterations (e.g. max ~6) to prevent runaway loops — the classic way to burn quota.
- Log input/output token counts and latency per request so spend is visible before dashboards update.
- Keep embeddings local; keep all integrations in test mode or mocked.

The real spend control is a per-project **Spend Cap** in Google AI Studio — keep that in mind but
it is a config step, not code.

## Phase roadmap (see build plan for full detail)

- **Phase 0 (Wk 1):** Walking skeleton — `/chat` endpoint streaming an LLM reply over SSE.
- **Phase 1 (Wk 2):** Tool-calling loop by hand — one trivial tool, iteration cap, logged steps.
- **Phase 2 (Wk 3–4):** Account agent + async SQLAlchemy over seeded Postgres data.
- **Phase 3 (Wk 5–6):** Knowledge agent with RAG — chunk, embed, `pgvector` search, cite sources.
- **Phase 4 (Wk 7–8):** Orchestrator agent — classify intent and route to the right specialist.
- **Phase 5 (Wk 9–10):** Action agent + human-in-the-loop approval gate on irreversible actions.
- **Phase 6 (Wk 11–12):** Observability (agent-thoughts panel, logging/cost tracking), hardening, optional deploy, write-up.

When starting work, identify which phase is active and stay within its scope — the value is in
going deep on one slice, not racing ahead.

## Working conventions

- **Verify live LLM details before relying on them.** Gemini model names, free-tier limits, and
  pricing shifted in early 2026 and keep moving. Don't hardcode assumptions about the cheapest model.
- **Provider-agnostic LLM client:** write against the OpenAI-compatible chat-completions shape.
- **Security mindset even in a toy:** scope DB queries; never hand the model raw SQL execution;
  gate irreversible actions behind explicit human approval.
- **Learning-first:** when a concept is new, a small throwaway example to understand it is
  encouraged. Leave short notes on design decisions — they feed the eventual README and blog post.

## Commands

No build/test/run commands are established yet (Phase 0 not yet implemented). Update this section as
the Next.js app and FastAPI service get scaffolded.
