"""MCP Helpdesk server.

Phase 0 — Hello, MCP: proved the provider-side boundary with a trivial `ping` tool
over stdio (kept below as a health check).

Phase 1 — Port the real helpdesk tools (THIS FILE, SCAFFOLD — fill in the TODOs):
the account/action/knowledge tools you wrote by hand in helpdesk-copilot get exposed as
`@mcp.tool`s. The build-plan rule is "reuse the bodies verbatim — only the registration
changes," so the actual logic still lives untouched in `server/tools/*.py`; this file is
*only* the MCP registration layer.

Three things change at the boundary — and typing them out is the Phase 1 lesson:

  1. WHO OWNS THE DB SESSION. In the hand-built loop, the loop injected `session` as the
     first arg of every tool: `await TOOLS[name](session, **model_args)`. But an MCP
     tool's full signature becomes its PUBLIC input schema, and the client must never
     supply a DB session. So each wrapper here OPENS ITS OWN session
     (`async with AsyncSessionLocal() as session:`) and delegates to the original body.
     Ownership moved from the loop to the tool. (A FastMCP lifespan/context could instead
     hold one shared session — deferred; per-call sessions are simplest and ride the pool.)

  2. WHERE THE SCHEMA COMES FROM. The old `types.FunctionDeclaration(...)` blocks (still
     in tools/*.py for reference) are NOT used here. FastMCP generates the input schema
     from each wrapper's TYPE HINTS — exactly like `echo`'s `message: str` in Phase 0.

  3. WHERE THE DESCRIPTION LIVES. Copy the load-bearing model-facing text from those
     FunctionDeclaration `description=` strings into the wrapper DOCSTRING. Phase 1
     checkpoint: moving to MCP changes *where* you write the description, not *what*.

Approval note (for later): `issue_refund` and `send_email` are irreversible (see
tools/action.REQUIRES_APPROVAL). Phase 1 adds NO protocol-level gate — for now the
human-in-the-loop is the MCP *client's* built-in per-tool approval prompt. Protocol-level
elicitation is the Phase 5 deepening.

Run locally (after venv + .env + seeded DB are ready):
    .venv/Scripts/python.exe mcp_server.py          # serves over stdio
    .venv/Scripts/fastmcp.exe dev mcp_server.py     # Inspector: see the raw tool list

Smoke-test discovery WITHOUT a live DB (create_async_engine is lazy, so imports work with
any DATABASE_URL set): see the __main__ block at the bottom.
"""

from pathlib import Path

from dotenv import load_dotenv

# Load server/.env before importing db.session, which reads os.environ["DATABASE_URL"]
# at import time. Explicit path (not bare load_dotenv()) so it works regardless of the
# CWD the MCP client launches us from (stdio clients don't guarantee cwd == server/).
load_dotenv(Path(__file__).resolve().parent / ".env")

from fastmcp import FastMCP

# The tool BODIES, reused verbatim — we delegate to these, never reimplement them.
from db.session import AsyncSessionLocal
from tools import account, action, knowledge

# Phase 2: the resource reads a ticket row directly (no existing "read ticket" body
# exists — tickets were only ever created before), so we touch the ORM model here.
from db.models import Ticket

mcp = FastMCP("helpdesk")


# ---------------------------------------------------------------------------
# Phase 0 leftover — a trivial health check. Harmless to keep.
# ---------------------------------------------------------------------------
@mcp.tool
def ping() -> str:
    """Health check. Returns 'pong' to confirm the MCP server is reachable."""
    return "pong"


# ===========================================================================
# ACCOUNT tools (read-only).
#
# get_customer is the WORKED EXAMPLE — the pattern every other wrapper follows:
#   - decorate with @mcp.tool
#   - signature = the model-facing args ONLY (no `session`), with type hints
#   - docstring = the description copied from tools/account.get_customer_decl
#   - body = open a session, delegate to the original function, return its dict
# ===========================================================================
@mcp.tool
async def get_customer(email: str) -> dict:
    """Look up a customer by their email address. Returns the customer's id, name,
    and email, or found=false if no customer has that email. You need the customer's
    id from here before calling get_orders or get_subscription."""
    async with AsyncSessionLocal() as session:
        return await account.get_customer(session, email)


# TODO: get_orders — by analogy with get_customer.
#   arg: customer_id: int
#   docstring: copy from tools/account.get_orders_decl (newest first; call get_customer
#              first to obtain customer_id).
#   body: async with AsyncSessionLocal() as session:
#             return await account.get_orders(session, customer_id)
@mcp.tool
async def get_orders(customer_id: int) -> dict:
    """ Look up all of a customers' order by customer id. Returns a list of orders. """
    async with AsyncSessionLocal() as session:
        return await account.get_orders(session, customer_id)


# TODO: get_subscription — same shape as get_orders.
#   arg: customer_id: int
#   docstring: copy from tools/account.get_subscription_decl.
#   body: delegate to account.get_subscription(session, customer_id)
@mcp.tool
async def get_subscription(customer_id: int) -> dict:
    """Look up all of a customers' subscriptions by customer id. Returns a list of subscriptions."""
    async with AsyncSessionLocal() as session:
        return await account.get_subscription(session, customer_id)

# ===========================================================================
# KNOWLEDGE tool (RAG retrieval, read-only).
# ===========================================================================
# TODO: search_docs — note the OPTIONAL arg with a default (that's how FastMCP marks it
#       not-required in the generated schema, the equivalent of leaving it out of the old
#       declaration's `required=[...]`).
#   signature: async def search_docs(query: str, top_k: int = knowledge.DEFAULT_TOP_K) -> dict
#   docstring: copy from tools/knowledge.search_docs_decl.
#   body: delegate to knowledge.search_docs(session, query, top_k)
@mcp.tool
async def search_docs(query: str, top_k: int = knowledge.DEFAULT_TOP_K) -> dict:
    """
        Search the help-center knowledge base for articles relevant to a user's
        question (refunds, shipping, billing, account/login, support hours, etc.)
        Returns the most relevant text chunks, each with the title of the article
        it came from. Use this for 'how do I…' and policy questions, then answer
        from the returned chunks and cite the article title.
    """
    async with AsyncSessionLocal() as session:
        return await knowledge.search_docs(session, query, top_k)


# ===========================================================================
# ACTION tools (side effects). issue_refund + send_email are irreversible (see the
# module docstring's approval note); create_ticket is deliberately ungated. In Phase 1
# they all register the same way — the gate is a later phase.
# ===========================================================================
# TODO: issue_refund
#   arg: order_id: int
#   docstring: copy from tools/action.issue_refund_decl.
#   body: delegate to action.issue_refund(session, order_id)
@mcp.tool
async def issue_refund(order_id: int) -> dict:
    """ 
    Issue a refund for a specific order, identified by its numeric order id. 
    If you only have the customer's email, look up the customer and their 
    orders FIRST to get the order id, then call this. This is an irreversible 
    action and will be confirmed with the user before it runs.
    """
    async with AsyncSessionLocal() as session:
        return await action.issue_refund(session, order_id)


# TODO: create_ticket
#   args: subject: str, body: str, customer_email: str | None = None
#   docstring: copy from tools/action.create_ticket_decl.
#   body: delegate to action.create_ticket(session, subject, body, customer_email)
@mcp.tool
async def create_ticket(subject: str, body: str, customer_email: str | None = None) -> dict:
    """
    Create a support ticket with a subject, body and an optional customer email
    This action does not require a confirmation from the user before it runs.
    """
    async with AsyncSessionLocal() as session:
        return await action.create_ticket(session, subject, body, customer_email)


# TODO: send_email
#   args: to: str, subject: str, body: str
#   docstring: copy from tools/action.send_email_decl.
#   body: delegate to action.send_email(session, to, subject, body)
@mcp.tool
async def send_email(to: str, subject: str, body: str) -> dict:
    """
    Mock email with to, subject and body
    Doesn't send an actual email yet
    """
    async with AsyncSessionLocal() as session:
        return await action.send_email(session, to, subject, body)


# ===========================================================================
# PHASE 2 — RESOURCES & PROMPTS (SCAFFOLD — fill in the prompt TODO).
#
# The core judgment of this phase is picking the right primitive:
#   tools     = ACTIONS       (POST, side effects)        -> @mcp.tool      [above]
#   resources = read CONTEXT  (GET, no side effect)       -> @mcp.resource  [worked below]
#   prompts   = reusable interaction TEMPLATES            -> @mcp.prompt    [your TODO]
#
# Checkpoint — why is "look up this ticket" a RESOURCE, not a tool? Two reasons:
#   1. No side effect + addressable by id -> it's a GET, i.e. context, not an action.
#   2. WHO decides to pull it in differs. A tool is something the MODEL chooses to call
#      mid-loop; a resource is context the CLIENT/user attaches by URI (like opening a
#      file / @-mentioning it). Same DB read, but a different consumer and intent.
# ===========================================================================

# WORKED EXAMPLE — a TEMPLATED resource. The {ticket_id} in the URI binds to the
# same-named function parameter; FastMCP serializes the returned dict as the resource
# contents. It owns its own session, exactly like the tools do.
@mcp.resource("ticket://{ticket_id}")
async def ticket_resource(ticket_id: int) -> dict:
    """A single support ticket's details, addressable by id. Read-only context —
    no side effect, just loads the ticket into the conversation."""
    async with AsyncSessionLocal() as session:
        t = await session.get(Ticket, ticket_id)
        if t is None:
            return {"found": False, "ticket_id": ticket_id}
        return {
            "found": True,
            "id": t.id,
            "subject": t.subject,
            "body": t.body,
            "status": t.status,
            "customer_email": t.customer_email,
            "created_at": t.created_at.isoformat(),
        }


# TODO: triage_ticket PROMPT — the second new primitive. A prompt is a reusable
# template the client can invoke (Claude Code surfaces prompts as slash-commands) so a
# consistent workflow doesn't have to be re-typed each time.
#
# Pointers:
#   - Decorate a function with @mcp.prompt (bare decorator is fine).
#   - Its PARAMETERS become the prompt's arguments. Take the ticket's content, e.g.:
#         def triage_ticket(subject: str, body: str) -> str:
#     (You could instead take ticket_id and read the DB, but keeping a prompt a PURE
#      template — data in, instructions out — is the cleaner mental model. Pair it with
#      the resource above: attach ticket://N to get the data, then run this prompt.)
#   - RETURN a string. FastMCP turns a returned str into a single 'user' message
#     (verified). That string is the packaged instruction set — this is the part worth
#     writing yourself. Have it tell the model to classify the ticket's PRIORITY
#     (low/normal/high/urgent) and CATEGORY (billing/shipping/account/refund/other),
#     then propose a next action — and interpolate the {subject}/{body} in.
#
# def triage_ticket(subject: str, body: str) -> str:
#     """Triage a support ticket: classify priority + category and suggest a next step."""
#     return f"...your template here, using {subject} and {body}..."

@mcp.prompt
def triage_ticket(subject: str, body: str) -> str:
    return f"""
        Given the following support ticket text and subject,
        please triage the ticket by classifying the priority (low/normal/high/urgent) and category (billing/shipping/account/refund/other), and suggest a next step.

        Subject:
        {subject}

        Ticket Text:
        {body}
    """


if __name__ == "__main__":
    import sys

    # `python mcp_server.py --list` -> print the discovered tool list (name + schema)
    # via an in-process client, WITHOUT a live DB. Proves your registration + generated
    # schemas are right before wiring a real client. Otherwise, serve over stdio.
    if "--list" in sys.argv:
        import asyncio
        import json

        from fastmcp import Client

        async def _list():
            async with Client(mcp) as c:
                print("TOOLS:")
                for t in await c.list_tools():
                    props = (t.inputSchema or {}).get("properties", {})
                    print(f"  - {t.name}{tuple(props)}")
                print("RESOURCES (static):")
                for r in await c.list_resources():
                    print(f"  - {r.uri}")
                print("RESOURCE TEMPLATES:")
                for r in await c.list_resource_templates():
                    print(f"  - {r.uriTemplate}")
                print("PROMPTS:")
                for p in await c.list_prompts():
                    print(f"  - {p.name}({', '.join(a.name for a in (p.arguments or []))})")

        asyncio.run(_list())
    else:
        # stdio: the client launches THIS file as a subprocess and speaks MCP over
        # stdin/stdout. Phase 4 switches this to Streamable HTTP for multi-client use.
        mcp.run(transport="stdio")
