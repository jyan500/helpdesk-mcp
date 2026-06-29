# Helpdesk Copilot — 12-Week Build Plan

A learning-focused roadmap for building an autonomous support-operations agent with a Next.js frontend and a FastAPI backend. Paced for ~2–3 months of part-time work, with room to stop and understand how things work rather than just wiring them together.

The guiding principle: **build a thin end-to-end "walking skeleton" first, then deepen one slice at a time.** You want a working chat → backend → LLM → response loop in week 1, even if it does almost nothing useful. Every later phase makes one slice real.

---

## Stack at a glance

- **Frontend:** Next.js (App Router) + TypeScript, streaming responses over Server-Sent Events (SSE)
- **Backend:** FastAPI + Pydantic + async SQLAlchemy, served with uvicorn
- **Database:** Postgres with the `pgvector` extension (run locally via Docker)
- **Embeddings:** `sentence-transformers` running locally (free, no per-token cost)
- **LLM:** the cheapest current Gemini model (the Flash-Lite tier), accessed through an OpenAI-compatible interface, with a per-project monthly spend cap set in Google AI Studio
- **Integrations:** Stripe test mode, a ticketing API free tier (or a mock), and email via a free-tier service or a mock
- **Deploy (optional, week 12):** Vercel (frontend) + Render/Fly/Railway (backend) + Neon/Supabase (Postgres) — all have usable free tiers

### Why these choices keep costs near zero

- **LLM:** use the cheapest current Gemini Flash-Lite model — it's well-suited to the routing, classification, and extraction work that makes up most of this app, and the lineup/pricing shifts so confirm the cheapest option on the live pricing page. Control spend with a **Project Spend Cap**: create a dedicated Google Cloud project for this app, then in Google AI Studio's **Spend** tab set a small monthly limit (e.g. $5–$10). Once the cap is hit, API requests are blocked until the next cycle. Set it slightly below your true ceiling because there's a ~10-minute enforcement delay during which overages are still billed to you.
- **Provider-agnostic from day one:** write your LLM client against the OpenAI-compatible chat-completions shape. Gemini, DeepSeek, Groq, and others all expose this, so switching providers later is a base-URL + key change, not a rewrite. This is also a good interview talking point about avoiding vendor lock-in.
- **Embeddings:** running `sentence-transformers` locally means RAG indexing and search cost nothing per token. (Gemini also offers free embeddings if you'd rather not run a model locally.)
- **Vector store:** `pgvector` lives inside the Postgres you already need — one fewer service, no paid vector DB, and it mirrors a real production pattern.
- **Integrations:** Stripe **test mode** issues fake refunds for free; ticketing/email can use free tiers or simple mocks. Never touch a paid action.
- **Cost guardrails in code:** cap `max_output_tokens`, cap agent-loop iterations, and log token usage per request from the start. These prevent runaway loops (the classic way to burn a free quota) and are good engineering habits.

### Monitoring your spend

Use two complementary layers. **Google's side:** AI Studio provides dashboards with a daily cost breakdown and usage by model. **Your side:** log input and output token counts per request (built into Phase 0 and Phase 6) and compute an approximate running cost — this gives you proactive visibility before the dashboard updates and lets you catch a runaway loop in seconds. An account-level tier cap also exists (Tier 1 is $250/month) but sits far above anything a learning project reaches; your per-project cap is the real control.

> Note: Gemini free-tier limits, model names, and prices all changed significantly in early 2026 and continue to move. Verify current Flash-Lite pricing and the spend-cap UI before relying on specific numbers.

---

## Phase 0 — Foundations & the walking skeleton (Week 1)

**Goal:** a request travels frontend → FastAPI → LLM → back to the browser, streamed.

- Set up two folders (or a simple monorepo): a Next.js app and a FastAPI service.
- **FastAPI, coming from Flask:** learn the differences that matter — `async def` endpoints, Pydantic request/response models for validation, dependency injection (`Depends`), and the auto-generated docs at `/docs`. Get uvicorn running with hot reload.
- **Next.js, since it's new to you:** learn the App Router layout, the server vs. client component split (your chat UI is a client component because it holds state), reading env vars, and calling your FastAPI backend. Don't go deep yet — just enough to render a page and hit an API route.
- Build a single `/chat` endpoint that calls the LLM (Gemini free tier or Ollama) and **streams** the reply back via SSE. Render the streaming text in a minimal chat box.

**Deliverable:** type a message, see a streamed LLM reply. Nothing else.

**Learning checkpoint:** can you explain how SSE differs from a normal request, and why streaming matters for chat UX?

---

## Phase 1 — Tool calling from scratch (Week 2)

**Goal:** understand the tool-calling loop deeply by building it by hand — no agent framework yet.

- Define one trivial tool (e.g. `get_current_time`) as a JSON schema you pass to the model.
- Implement the loop yourself: send the prompt + tool definitions → if the model returns a tool call, execute it in Python, append the result to the message history, and call the model again → repeat until it returns a final text answer.
- Add an **iteration cap** (e.g. max 6 loops) and log each step.

**Deliverable:** ask a question that forces a tool call; watch the loop run and resolve.

**Learning checkpoint:** the model never runs your code — it only *asks* you to. Make sure you can articulate that handshake, because frameworks later will hide it.

---

## Phase 2 — First real agent + database integration (Weeks 3–4)

**Goal:** the **Account agent** answers real questions by querying a database.

- Spin up Postgres in Docker. Seed it with fake customers, orders, and subscriptions.
- Learn **async SQLAlchemy** in FastAPI: engine/session setup, models, and querying inside an endpoint. Use Pydantic models to shape what the tools return.
- Build tools like `get_customer(email)` and `get_orders(customer_id)` that hit the DB. Wire them into your hand-rolled loop from Phase 1.
- Flesh out the frontend chat: message history, loading states, clean streaming.

**Deliverable:** "What's the status of the latest order for alice@example.com?" → the agent queries Postgres and answers correctly.

**Learning checkpoint:** how do you keep the LLM from seeing data it shouldn't? (Scope queries; never hand the model raw SQL execution.)

---

## Phase 3 — Knowledge agent with RAG (Weeks 5–6)

**Goal:** the **Knowledge agent** answers from a document base using retrieval.

- Write a handful of fake help-center articles / FAQ entries.
- Learn the RAG pipeline: chunk the docs, embed each chunk with local `sentence-transformers`, and store the vectors in `pgvector`.
- Build a `search_docs(query)` tool that embeds the query, does a vector similarity search, and returns the top chunks. The agent answers using those chunks and cites which article it used.
- Experiment with chunk size and number of results — this is where you build intuition for why RAG quality varies.

**Deliverable:** ask a "how do I…" question; the agent retrieves the right article and answers with a citation.

**Learning checkpoint:** can you explain what an embedding is, and why cosine similarity finds relevant chunks?

---

## Phase 4 — Orchestration (Weeks 7–8)

**Goal:** an **orchestrator agent** classifies each request and routes it to the right specialist.

- Build a triage step: the orchestrator classifies intent (account question / knowledge question / action request) and calls the appropriate sub-agent.
- Refactor your code into a clean structure: a shared tool registry, one module per agent, and an orchestrator that owns routing and passes context between agents.
- Learn the orchestration patterns as you go: start with **routing**, then try a case that needs two agents in sequence, and (optionally) one where two lookups run in **parallel**.

**Deliverable:** a single chat box that correctly handles all three request types by routing internally.

**Learning checkpoint:** when does multi-agent orchestration actually help versus just making one agent with more tools? (Have an opinion — interviewers ask this.)

---

## Phase 5 — Action agent + human-in-the-loop (Weeks 9–10)

**Goal:** the **Action agent** can take real (sandboxed) actions, with approval gating on risky ones.

- Build action tools: `issue_refund` (Stripe **test mode**), `create_ticket` (a ticketing free tier or a mock), `send_email` (free-tier email service or a mock).
- Implement the **human-in-the-loop gate:** before any irreversible action, the agent pauses and the UI shows an approval prompt ("Issue $40 refund to alice@example.com? Approve / Deny"). Only on approval does the tool run.
- This is the hardest and most impressive part: you have to persist the agent's pending state across a pause and resume it after the user responds.

**Deliverable:** a refund request that halts for approval and only executes when you click Approve.

**Learning checkpoint:** how do you represent and resume a paused agent? (This is a real production concern — be ready to discuss it.)

---

## Phase 6 — Observability, hardening & polish (Weeks 11–12)

**Goal:** make it demo-ready and production-literate.

- **Agent thoughts panel:** stream the agent's reasoning and each tool call to a side panel in the UI. This is what makes the demo impressive — viewers can *see* the agent work.
- **Logging & cost tracking:** log every tool call, token count, and latency per request. A tiny dashboard or even structured logs is enough.
- **Hardening:** retries on tool failure, timeouts, the iteration cap from Phase 1, and graceful handling when the LLM returns a malformed tool call.
- **Deploy (optional):** Vercel for the frontend, a free-tier host for the FastAPI backend, and Neon/Supabase for Postgres.
- **Write it up:** a README with the architecture diagram, a short demo video or GIF, and a blog post explaining your design decisions and trade-offs. For a job hunt, the write-up is as valuable as the code.

**Deliverable:** a polished, deployed (or locally runnable) project with a clear README and demo.

---

## A note on frameworks

Build the agent loop **by hand** through Phases 1–4. It's the best way to understand what's actually happening, and interviewers value that you know what a framework abstracts away. *After* you understand the fundamentals, you can optionally swap in a framework as a learning exercise — Pydantic AI is a natural fit given your FastAPI/Pydantic stack, and LangGraph is widely mentioned in job descriptions if you want exposure to it. Treat that as a stretch goal, not a dependency.

---

## Interview talking points this project gives you

- The tool-calling handshake, explained from first principles (you built it by hand)
- RAG end to end: chunking, embeddings, vector search, and the quality trade-offs
- Orchestration patterns and *when* multi-agent is worth it
- Human-in-the-loop design and resuming paused agent state
- Cost-aware engineering: provider-agnostic clients, local embeddings, iteration caps, token logging
- Real full-stack delivery: async FastAPI, Next.js streaming UI, Postgres + pgvector, deployment

---

## Optional stretch goals (if you have time or want to go further)

- Add evaluation: a small test set of support questions with expected behavior, run automatically
- Add conversation memory across sessions
- Swap in a framework (Pydantic AI / LangGraph) and compare it to your hand-rolled version
- Add authentication and a basic admin view of logged actions
- Support multiple languages in the knowledge base

---

## Suggested weekly rhythm

Each week: pick the phase goal, spend the first portion *researching and understanding* the new concept (read docs, build a tiny throwaway example), then implement it in the project, then write a few notes on what you learned. Those notes become your blog post and your interview prep.
