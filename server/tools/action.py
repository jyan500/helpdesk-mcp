"""
Action-agent tools (Phase 5) — SCAFFOLD. Fill in the TODOs.

Same three layers as tools/account.py and tools/knowledge.py — the structure is
the lesson, reused a third time. What's NEW in Phase 5 is that these tools have
SIDE EFFECTS: they change the world (refund money, open a ticket, send mail)
instead of just reading it. That's why this file also exports two things the read
tools never needed:

  REQUIRES_APPROVAL  - the set of tool names that are IRREVERSIBLE and must pause
                       for human approval before they run. (issue_refund, send_email)
                       create_ticket is deliberately NOT in it — opening a ticket is
                       cheap and reversible, so it runs immediately. Deciding WHICH
                       actions need a gate is the core Phase 5 judgment call.
  describe_action()  - turns a pending (tool_name, args) into a one-line human
                       sentence for the Approve/Deny prompt the UI shows.

Everything is MOCK / sandboxed (CLAUDE.md: never trigger a paid or irreversible
real-world action). "Issuing a refund" just flips an order's status in Postgres;
"sending an email" returns a fake success. No Stripe, no SMTP.

Quick test once filled in (from server/, after `python -m db.seed`):
    python -m tools.action          # see the __main__ smoke test at the bottom
"""
from __future__ import annotations

from decimal import Decimal

from google.genai import types
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from db.models import Order, Ticket


# ---------------------------------------------------------------------------
# 1. OUTPUT SCHEMAS — the whitelist of fields the model sees back from each tool.
#
# RefundResult is written out as the pattern. Note the `ok` flag: side-effecting
# tools should report success/failure EXPLICITLY so the model can tell the user
# "done" vs "couldn't" instead of guessing from missing fields.
# ---------------------------------------------------------------------------
class RefundResult(BaseModel):
    ok: bool
    order_id: int
    refund_id: str | None = None   # fake "re_mock_..." id, only when ok=True
    amount: Decimal | None = None
    status: str                    # "succeeded" | "already_refunded" | "not_found"


class TicketResult(BaseModel):
    # TODO: the fields create_ticket should report back. Suggested:
    #   ok: bool
    #   ticket_id: int | None = None
    #   status: str               # e.g. "open" | "failed"
    ok: bool
    ticket_id: int | None = None
    status: str


class EmailResult(BaseModel):
    # TODO: the fields send_email should report back. Suggested:
    #   ok: bool
    #   to: str
    #   status: str               # e.g. "sent" | "failed"
    ok: bool
    to: str
    status: str


# ---------------------------------------------------------------------------
# 2. TOOL FUNCTIONS — first arg is always the injected `session`, even when a
#    tool doesn't use it (send_email). The loop ALWAYS calls
#    `await TOOLS[name](session, **model_args)`, so every signature must accept it.
# ---------------------------------------------------------------------------
async def issue_refund(session: AsyncSession, order_id: int) -> dict:
    """Refund an order (MOCK). Flip its status to 'refunded' and report the result.

    This is the IRREVERSIBLE action the whole human-in-the-loop gate exists for.
    By the time this runs, the user has already approved (see agent/loop.py
    resume_agent) — so here we just do the deed.

    Pointers:
      - Load the order:  order = (await session.execute(
            select(Order).where(Order.id == order_id))).scalar_one_or_none()
      - Not found        -> return RefundResult(ok=False, order_id=order_id,
                                                status="not_found").model_dump(mode="json")
      - Already refunded -> guard against a double refund:
            if order.status == "refunded": return ... status="already_refunded", ok=False
      - Otherwise: order.status = "refunded"; await session.commit()
            refund_id = f"re_mock_{order_id}"
            return RefundResult(ok=True, order_id=order_id, refund_id=refund_id,
                                amount=order.total_amount, status="succeeded")
                       .model_dump(mode="json")
      (model_dump(mode="json") turns Decimal -> string so the result is JSON-safe
       to hand back to the model, same as the account tools.)
    """
    # TODO: implement per the pointers above.
    order = await session.execute(select(Order).where(Order.id == order_id))
    # scalar one or none returns the first result object or None
    order_obj = order.scalar_one_or_none()
    # order is not found
    if not order_obj:
        return RefundResult(
            ok=False, order_id=order_id, status="not_found"
        ).model_dump(mode="json")
    # guard against double refund
    elif order_obj.status == "refunded":
        return RefundResult(
            ok=False, order_id=order_id, status="already_refunded"
        ).model_dump(mode="json")
    
    # otherwise, process the order  
    order_obj.status = "refunded"
    await session.commit()
    refund_id = f"re_mock_{order_id}"
    return RefundResult(
        ok=True, order_id=order_id, refund_id=refund_id,
        amount=order_obj.total_amount, status="succeeded"
    ).model_dump(mode="json")





async def create_ticket(
    session: AsyncSession, subject: str, body: str, customer_email: str | None = None
) -> dict:
    """Open a support ticket (MOCK persistence — a real row in `tickets`).

    UNGATED: this runs immediately, no approval. It's the contrast case to refunds.

    Pointers:
      - ticket = Ticket(subject=subject, body=body, customer_email=customer_email)
        (don't set status — the column server_defaults to "open")
      - session.add(ticket); await session.commit()
      - await session.refresh(ticket)   # so ticket.id / ticket.status are populated
      - return TicketResult(ok=True, ticket_id=ticket.id, status=ticket.status)
               .model_dump(mode="json")
    """
    # TODO: implement per the pointers above.
    ticket = Ticket(subject=subject, body=body, customer_email=customer_email, status="open")
    session.add(ticket)
    await session.commit()
    await session.refresh(ticket)
    return TicketResult(ok=True, ticket_id=ticket.id, status=ticket.status).model_dump(mode="json")


async def send_email(session: AsyncSession, to: str, subject: str, body: str) -> dict:
    """Send an email (PURE MOCK — no SMTP, nothing leaves the machine).

    GATED (in REQUIRES_APPROVAL): you can't un-send an email, so it pauses for
    approval even though our mock does nothing. The GATE is the lesson, not the send.

    Pointers:
      - Don't touch the DB. Just print(f"[mock email] -> {to}: {subject}") so you
        can see it in the logs, and return success:
            return EmailResult(ok=True, to=to, status="sent").model_dump(mode="json")
    """
    # TODO: implement per the pointers above.
    print(f"[mock email] -> {to}: {subject}")
    return EmailResult(
        ok=True, to=to, status="sent"
    ).model_dump(mode="json")


# ---------------------------------------------------------------------------
# 3a. REGISTRY — name -> the async callable the loop dispatches to.
# ---------------------------------------------------------------------------
TOOLS = {
    "issue_refund": issue_refund,
    "create_ticket": create_ticket,
    "send_email": send_email,
}

# ---------------------------------------------------------------------------
# 3b. APPROVAL GATE — which of the above are irreversible and must be approved.
#
# This is the single source of truth the agent loop checks before running a tool.
# An AgentConfig will carry this set (agent/agents.py, subpart 5) and the loop
# will, for any call whose name is in here, PAUSE instead of execute (subpart 4).
# Keep create_ticket OUT — it's the deliberately ungated counter-example.
# ---------------------------------------------------------------------------
REQUIRES_APPROVAL = frozenset({"issue_refund", "send_email"})


def describe_action(name: str, args: dict) -> str:
    """One-line, human-readable summary of a pending action for the approval prompt.

    The UI shows this verbatim next to Approve / Deny, so write it the way you'd
    want to read it before authorizing money to move.

    Pointers (a simple per-tool template is plenty):
      - "issue_refund" -> f"Issue a refund for order #{args.get('order_id')}?"
      - "send_email"   -> f"Send an email to {args.get('to')}?"
      - fallback       -> f"Run {name} with {args}?"
      (Optional polish: for refunds, look the order up first and include the
       dollar amount — "Issue a $89.00 refund for order #3?" — but that needs a
       session, so keep it simple here unless you want to thread one through.)
    """
    # TODO: implement per the pointers above.
    res = ""
    if name == "issue_refund":
        res = f"Issue a refund for order #{args.get('order_id')}"
    elif name == "send_email":
        res = f"Send email to {args.get('to')}"
    else:
        res = f"Run {name} with {args}"
    return res

# ---------------------------------------------------------------------------
# 3c. DECLARATIONS — what the model SEES (descriptions + arg schemas, NO session).
#
# issue_refund_decl is the worked example. The DESCRIPTION is load-bearing: the
# model decides whether to call this from these words, so be explicit that it
# needs the numeric order_id (and that it should look the order up first if it
# only has an email).
#
# TODO: write create_ticket_decl and send_email_decl by analogy, then add all
# three to ACTION_TOOL_DECLS below.
# ---------------------------------------------------------------------------
issue_refund_decl = types.FunctionDeclaration(
    name="issue_refund",
    description=(
        "Issue a refund for a specific order, identified by its numeric order id. "
        "If you only have the customer's email, look up the customer and their "
        "orders FIRST to get the order id, then call this. This is an irreversible "
        "action and will be confirmed with the user before it runs."
    ),
    parameters=types.Schema(
        type=types.Type.OBJECT,
        properties={
            "order_id": types.Schema(
                type=types.Type.INTEGER,
                description="The numeric id of the order to refund, e.g. 3",
            ),
        },
        required=["order_id"],
    ),
)

# TODO: create_ticket_decl = types.FunctionDeclaration(name="create_ticket", ...)
#   params: subject (STRING, required), body (STRING, required),
#           customer_email (STRING, optional — omit from `required`).
create_ticket_decl = types.FunctionDeclaration(
    name="create_ticket",
    description=(
        "Create a support ticket with a subject, body and an optional customer email"
        "This action does not require a confirmation from the user before it runs."
    ),
    parameters=types.Schema(
        type=types.Type.OBJECT,
        properties={
            "subject": types.Schema(
                type=types.Type.STRING,
                description="The subject of the support ticket"
            ),
            "body": types.Schema(
                type=types.Type.STRING,
                description="The body of the support ticket"
            ),
            "customer_email": types.Schema(
                type=types.Type.STRING,
                description="The customer email tied to the support ticket"
            )
        },
        required=["subject", "body"]
    )
)


# TODO: send_email_decl = types.FunctionDeclaration(name="send_email", ...)
#   params: to (STRING, required), subject (STRING, required), body (STRING, required).
send_email_decl = types.FunctionDeclaration(
    name="send_email",
    description=(
        "Mock email with to, subject and body"
        "Doesn't send an actual email yet"
    ),
    parameters=types.Schema(
        type=types.Type.OBJECT,
        properties={
            "to": types.Schema(
                type=types.Type.STRING,
                description="Email's to field"
            ),
            "subject": types.Schema(
                type=types.Type.STRING,
                description="Email subject field"
            ),
            "body": types.Schema(
                type=types.Type.STRING,
                description="Body field"
            ),
        },
        required=["to", "subject", "body"]
    )
)

# TODO: collect all three declarations here (subpart 5 hands this to the model).
ACTION_TOOL_DECLS = [
    issue_refund_decl,
    create_ticket_decl,
    send_email_decl,
]


# ---------------------------------------------------------------------------
# Optional smoke test: run the tools directly against the seeded DB, no LLM.
# Proves the side effects work before wiring them into the agent loop.
# Pick an order_id that exists from your seed (python -m db.seed prints the data).
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import asyncio

    from db.session import AsyncSessionLocal, engine

    async def _smoke():
        async with AsyncSessionLocal() as session:
            # TODO: refund a real seeded order id and confirm status flips.
            #   refund = await issue_refund(session, order_id=3)
            #   print("issue_refund:", refund)
            #   # run it again -> should report already_refunded
            #   print("again:", await issue_refund(session, order_id=3))
            #   print("ticket:", await create_ticket(session, "Broken keyboard",
            #         "Arrived with missing keys", "alice@example.com"))
            #   print("email:", await send_email(session, "alice@example.com",
            #         "Your refund", "It's on the way"))
            print("ticket: ", await create_ticket(session, "Broken keyboard", "Arrived with missing keys", "alice@example.com"))
            print("email: ", await send_email(session, "alice@example.com", "Your refund", "It's on the way"))
        await engine.dispose()

    asyncio.run(_smoke())
