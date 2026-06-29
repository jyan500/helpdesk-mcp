"""
Account-agent tools (Phase 2) — SCAFFOLD. Fill in the TODOs.

Three layers, and the separation IS the lesson:

  1. Pydantic output schemas — the *only* shape the model ever sees. You
     hand-pick which columns leave the database, so the LLM can't receive
     internal fields. This is the answer to the Phase 2 checkpoint, "how do you
     keep the LLM from seeing data it shouldn't?": scope the query AND whitelist
     the output.

  2. async tool functions — scoped SQLAlchemy queries. First arg is the DB
     `session`, which the agent loop injects; the model never provides it and
     never sends SQL, only structured args like `email`.

  3. Gemini FunctionDeclarations + a name->callable registry — what we pass to
     the model and how we dispatch the call it asks for.

Quick test once filled in (from server/):
    python -m tools.account            # see the __main__ smoke test at the bottom
"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from google.genai import types
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from db.models import Customer, Order, Subscription


# ---------------------------------------------------------------------------
# 1. OUTPUT SCHEMAS — the whitelist of fields the model is allowed to see.
#
# CustomerInfo is done for you as the pattern. Note what's DELIBERATELY left
# out vs the ORM model (e.g. created_at) — only expose what the agent needs.
#
# Serialization tip used in layer 2: schema.model_dump(mode="json") turns
# datetime -> ISO string and Decimal -> string, so the result is always JSON-safe
# to hand back to the model. That's why OrderInfo can keep datetime/Decimal types.
# ---------------------------------------------------------------------------
class CustomerInfo(BaseModel):
    id: int
    name: str
    email: str


class OrderInfo(BaseModel):
    # TODO: fields the agent needs to answer order questions.
    #   Suggested: id: int, status: str, item: str,
    #              total_amount: Decimal, created_at: datetime
    id: int
    status: str
    item: str
    total_amount: Decimal
    # The field NAME is part of what the model reads. "created_at" is ambiguous
    # (created when?); "order_date" tells the model this is the purchase date the
    # refund policy's 30-day window is measured from. (DB column stays created_at.)
    order_date: datetime


class SubscriptionInfo(BaseModel):
    # TODO: id: int, plan: str, status: str, current_period_end: datetime
    id: int
    plan: str
    status: str
    current_period_end: datetime


# ---------------------------------------------------------------------------
# 2. TOOL FUNCTIONS — scoped queries. First arg is always the injected session.
# ---------------------------------------------------------------------------
async def get_customer(session: AsyncSession, email: str) -> dict:
    """Look up one customer by email. Return {found: False} when there's no match.

    Pointers:
      - Build the query:   stmt = select(Customer).where(Customer.email == email)
      - Run it (async):    result = await session.execute(stmt)
      - Exactly 0 or 1 row, so:  customer = result.scalar_one_or_none()
      - If None -> return {"found": False, "email": email}
      - Else shape it through CustomerInfo and return:
            info = CustomerInfo(id=..., name=..., email=...)
            return {"found": True, **info.model_dump(mode="json")}
    """
    # TODO: implement per the pointers above.
    stmt = select(Customer).where(Customer.email == email)
    result = await session.execute(stmt)
    customer = result.scalar_one_or_none()
    if not customer:
        return {"found": False, "email": email}
    else:
        info = CustomerInfo(id=customer.id, name=customer.name, email=customer.email)
        return {"found": True, **info.model_dump(mode="json")}


async def get_orders(session: AsyncSession, customer_id: int) -> dict:
    """All orders for a customer, NEWEST FIRST (so 'latest' is orders[0]).

    Pointers:
      - select(Order).where(Order.customer_id == customer_id).order_by(Order.created_at.desc())
      - Many rows, so:  orders = result.scalars().all()
      - Shape each row through OrderInfo(...).model_dump(mode="json") in a list.
      - Return {"customer_id": customer_id, "count": len(shaped), "orders": shaped}
        (Returning count + an envelope, not a bare list, makes the empty case
        unambiguous for the model — see Carol, who has no orders.)
    """
    # TODO: implement per the pointers above.
    stmt = select(Order).where(Order.customer_id == customer_id).order_by(Order.created_at.desc())
    result = await session.execute(stmt)
    orders = result.scalars().all()
    shaped = []
    for order in orders:
        shaped.append(OrderInfo(
            id=order.id, 
            status=order.status, 
            item=order.item,
            total_amount=order.total_amount,
            order_date=order.created_at   # model-facing name; source column is still created_at
        ).model_dump(mode="json"))
    return {"customer_id": customer_id, "count": len(shaped), "orders": shaped}


async def get_subscription(session: AsyncSession, customer_id: int) -> dict:
    """The customer's subscription(s). Mirror get_orders' shape/approach."""
    # TODO: select(Subscription).where(...), shape via SubscriptionInfo,
    #       return {"customer_id":..., "count":..., "subscriptions":[...]}.
    stmt = select(Subscription).where(Subscription.customer_id == customer_id).order_by(Subscription.created_at.desc())
    result = await session.execute(stmt)
    subscriptions = result.scalars().all()
    shaped = []
    for subscription in subscriptions:
        shaped.append(SubscriptionInfo(
            id=subscription.id,
            plan=subscription.plan,
            status=subscription.status,
            current_period_end=subscription.current_period_end
        ).model_dump(mode="json"))
    return {"customer_id": customer_id, "count": len(shaped), "subscriptions": shaped}



# ---------------------------------------------------------------------------
# 3a. REGISTRY — name -> the async callable the loop dispatches to.
#     The loop will call:  await TOOLS[name](session, **model_provided_args)
# ---------------------------------------------------------------------------
TOOLS = {
    "get_customer": get_customer,
    "get_orders": get_orders,
    "get_subscription": get_subscription,
}

# ---------------------------------------------------------------------------
# 3b. DECLARATIONS — what the model SEES (descriptions + arg schemas, NO session).
#
# get_customer is written out as the worked example (same FunctionDeclaration
# shape as scratch_tools.py). Note `required=[...]` and that there's no session
# parameter — the model only ever fills business args.
#
# TODO: write get_orders_decl and get_subscription_decl by analogy. Both take a
# single `customer_id` of type types.Type.INTEGER. Good descriptions matter — the
# model decides WHICH tool to call from these words, so tell it to call
# get_customer first to obtain the customer_id.
# ---------------------------------------------------------------------------
get_customer_decl = types.FunctionDeclaration(
    name="get_customer",
    description=(
        "Look up a customer by their email address. Returns the customer's id, "
        "name, and email, or found=false if no customer has that email. You need "
        "the customer's id from here before calling get_orders or get_subscription."
    ),
    parameters=types.Schema(
        type=types.Type.OBJECT,
        properties={
            "email": types.Schema(
                type=types.Type.STRING,
                description="The customer's email address, e.g. alice@example.com",
            ),
        },
        required=["email"],
    ),
)

# TODO: get_orders_decl = types.FunctionDeclaration(name="get_orders", ...)
get_orders_decl = types.FunctionDeclaration(
    name="get_orders",
    description=(
        "Look up all of a customers' order by customer id. Returns a list of orders."
    ),
    parameters=types.Schema(
        type=types.Type.OBJECT,
        properties={
            "customer_id": types.Schema(
                type=types.Type.INTEGER,
                description="The customer's id"
            )
        },
        required=["customer_id"]
    )
)
# TODO: get_subscription_decl = types.FunctionDeclaration(name="get_subscription", ...)
get_subscription_decl = types.FunctionDeclaration(
    name="get_subscription",
    description=(
        "Look up all of a customers' subscriptions by customer id. Returns a list of subscriptions."
    ),
    parameters=types.Schema(
        type=types.Type.OBJECT,
        properties={
            "customer_id": types.Schema(
                type=types.Type.INTEGER,
                description="The customer's id"
            )
        },
        required=["customer_id"]
    )
)

# TODO: collect all three declarations into this list (step 6 passes it to the model).
ACCOUNT_TOOL_DECLS = [
    get_customer_decl,
    get_orders_decl,
    get_subscription_decl,
]


# ---------------------------------------------------------------------------
# Optional smoke test: run the tools directly against the seeded DB, no LLM.
# Proves your queries work before you wire them into the agent loop.
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import asyncio

    from db.session import AsyncSessionLocal, engine

    async def _smoke():
        async with AsyncSessionLocal() as session:
            cust = await get_customer(session, "alice@example.com")
            print("get_customer:", cust)
            # TODO: once get_orders works, look up alice's id and print her orders:
            orders = await get_orders(session, cust["id"])
            print("latest order:", orders["orders"][0])
            subscriptions = await get_subscription(session, cust["id"])
            print("latest subscription: ", subscriptions["subscriptions"][0])
        await engine.dispose()

    asyncio.run(_smoke())
