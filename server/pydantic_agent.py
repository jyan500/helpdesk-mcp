"""Phase 3 — Drive the MCP server with a FRAMEWORK (Pydantic AI). SCAFFOLD — fill in the TODOs.

This is the payoff of the whole project. In helpdesk-copilot you WROTE the agent loop
(server/agent/loop.py): the `for i in range(MAX_ITERS)` loop, streaming chunk parsing, the
Gemini-3 thought_signature echo, the `contents.append` bookkeeping (model turn +
function_response), and dispatch via `TOOLS[name](session, **args)`. Here a Pydantic AI
`Agent` does ALL of that for free — you point it at your MCP server as a toolset and call
`.run()` once. There is deliberately NO loop in this file; that absence IS the lesson.

What the FRAMEWORK now owns (all of this was yours in loop.py):
  - the loop: discover tools -> send to model -> get tool call -> dispatch -> feed result
    back -> repeat until a final answer
  - conversation/history bookkeeping, tool dispatch, and the iteration cap (via UsageLimits)

What is STILL yours (the framework does NOT do these):
  - tool DESIGN + the MCP server itself (Phases 1-2)
  - the system prompt / instructions (prompt engineering — lift from loop.py if you want)
  - model choice + cost guardrails (max_tokens, request_limit)

One contrast worth noting for the write-up: loop.py PAUSES on approval-gated tools
(requires_approval -> PendingAction). This framework agent has no such gate yet, so it will
call issue_refund/send_email straight through. Protocol-level approval is Phase 5.

Run (from server/):  .venv/Scripts/python.exe pydantic_agent.py
Requires .env (GEMINI_API_KEY) + a seeded DB (the spawned mcp_server.py hits Postgres).
"""
import asyncio
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent / ".env")

from pydantic_ai import Agent
from pydantic_ai.mcp import MCPToolset, StdioTransport, StreamableHttpTransport
from pydantic_ai.models.google import GoogleModel
from pydantic_ai.providers.google import GoogleProvider
from pydantic_ai.settings import ModelSettings
from pydantic_ai.usage import UsageLimits

from utils.constants import USE_MODEL  # "gemini-3.1-flash-lite-preview" — same model as loop.py


# --- WORKED: the MODEL. Native Gemini via google-genai (your existing key). Provider is a
#     key/base-url swap, per CLAUDE.md — nothing else changes if you point elsewhere. ------
model = GoogleModel(
    USE_MODEL,
    provider=GoogleProvider(api_key=os.environ["GEMINI_API_KEY"]),
)

# --- WORKED: your MCP SERVER as a TOOLSET. StdioTransport LAUNCHES mcp_server.py as a
#     subprocess (the same launch recipe as .mcp.json — command + args), exactly like Claude
#     Code did; MCPToolset then discovers its tools and exposes them to the agent.
#     init_timeout is bumped to 60s because importing mcp_server pulls in sentence-
#     transformers/torch at startup (slow), which trips the default handshake timeout. ------

# helpdesk_toolset = MCPToolset(
#     StdioTransport(
#         command=sys.executable,                      # the venv python running THIS client
#         args=["mcp_server.py"],
#         cwd=str(Path(__file__).resolve().parent),    # so the server's relative imports resolve
#     ),
#     init_timeout=60,
# )

# PHASE 4 — TODO: swap the stdio toolset above for a Streamable HTTP one. Everything below
# (the Agent, the run call) is UNCHANGED — you're only changing HOW the client reaches the
# server: from "spawn a subprocess" to "connect to a running URL". Steps:
#   1. Start the ONE shared server first, in its own terminal:  python mcp_server.py --http
#   2. Add StreamableHttpTransport to the import at the top:
#        from pydantic_ai.mcp import MCPToolset, StdioTransport, StreamableHttpTransport
#   3. Replace helpdesk_toolset with:
#        helpdesk_toolset = MCPToolset(
#            StreamableHttpTransport(url="http://127.0.0.1:8000/mcp"),
#        )
#   No command/args/cwd/init_timeout now — there's no subprocess to launch or wait on; the
#   server is already up, so you just point at its URL.

# PHASE 5 — the CLIENT half of protocol-level approval. When the server calls ctx.elicit on a
# gated tool (issue_refund/send_email), THIS handler runs: surface the request to the human and
# return their decision. In helpdesk-copilot the Next.js frontend rendered Approve/Deny; here
# the MCP client owns that UX, and the SAME server works with any client that provides one.
from fastmcp.client.elicitation import ElicitResult


async def approval_handler(message, response_type, params, context):
    ans = input(f"\n[APPROVAL NEEDED] {message}  Approve? [y/N] ").strip().lower()
    return ElicitResult(action="accept" if ans in ("y", "yes") else "decline")


# PHASE 5 — TODO: wire the handler into the toolset so approvals can round-trip. Add the kwarg:
#     helpdesk_toolset = MCPToolset(
#         StreamableHttpTransport(url="http://127.0.0.1:8000/mcp"),
#         elicitation_handler=approval_handler,
#     )
# (Then run main() with the REFUND question, not the read-only one, to trigger the prompt.)
helpdesk_toolset = MCPToolset(
    StreamableHttpTransport(url="http://127.0.0.1:8000/mcp"),
    elicitation_handler=approval_handler
)


# TODO 1 — build the AGENT. This single object replaces your entire loop.py.
#   agent = Agent(
#       model,
#       toolsets=[helpdesk_toolset],   # <- your MCP server; tools discovered automatically
#       instructions="...",            # <- STILL your job: the system prompt. Adapt one of
#                                      #    loop.py's *_SYSTEM_PROMPT (the action one fits a
#                                      #    do-things agent). Tell it to resolve ids with the
#                                      #    lookup tools before acting, and to be concise.
#       model_settings=ModelSettings(max_tokens=1000),  # cost guardrail (CLAUDE.md)
#   )

SYSTEM_PROMPT = """
You are a helpdesk action agent. You DO things on the user's behalf: issue
refunds, open support tickets, and send emails.

Many actions need a concrete id the user didn't give you. Resolve it FIRST with
the lookup tools: call get_customer(email) to get the customer id, then
get_orders(customer_id) to find the specific order. NEVER invent an order id,
customer id, or email — if you can't determine the exact target from the data,
say what's missing instead of guessing.

Once you have the concrete arguments, call the single action tool that does what
the user asked (issue_refund, create_ticket, or send_email). Do exactly what was
requested — one action — and don't take extra actions you weren't asked for.

After a tool returns, report the outcome plainly: confirm what was done (include
the refund/ticket id) on success, or explain what went wrong (e.g. the order was
already refunded or couldn't be found). Be concise.
"""

agent = Agent(
    model,
    toolsets=[helpdesk_toolset],
    instructions=SYSTEM_PROMPT,
    model_settings=ModelSettings(max_tokens=1000)
)


# TODO 2 — run it. Notice: ONE await, no loop of your own. `async with agent` opens the
# toolset (spawns + handshakes the server); agent.run drives the model<->tool loop.
#   async def main():
#       async with agent:
#           result = await agent.run(
#               "Refund the latest order for alice@example.com and email her a confirmation.",
#               usage_limits=UsageLimits(request_limit=6),  # the iteration cap (was MAX_ITERS)
#           )
#           print("ANSWER:", result.output)
#           # cost visibility (CLAUDE.md): usage is an ATTRIBUTE in pydantic-ai 2.5, not a call
#           print("USAGE:", result.usage)   # input/output tokens + request count
#
#   asyncio.run(main())

async def main():
    async with agent:
        refund_request = "Refund the latest order for alice@example.com and email her a confirmation.",
        latest_order = "What is the status of alice@example.com's latest order?"
        email_request = "Can you send a test email to alice@example.com?"
        result = await agent.run(
            email_request,
            usage_limits=UsageLimits(request_limit=6)
        )
        print("ANSWER: ", result.output)
        print("USAGE: ", result.usage)

# TODO 3 (optional, for the write-up) — swap the question above for a read-only one first,
# e.g. "What's the status of alice@example.com's latest order?", and watch the SAME agent
# chain get_customer -> get_orders with zero routing code (loop.py needed an orchestrator).

if __name__ == "__main__":
    asyncio.run(main())
