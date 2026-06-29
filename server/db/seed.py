"""
Seed the helpdesk database with fake data (Phase 2).

Run from the server/ directory:
    python -m db.seed

This is DESTRUCTIVE by design: it drops and recreates every table each run so
you always get a clean, predictable dataset to query against. That's fine for a
learning seed; never point this at data you care about.

Alice deliberately has three orders with different dates so the Phase 2
deliverable question — "status of the LATEST order for alice@example.com" — has
an unambiguous right answer (the 2026-06-10 'Mechanical keyboard', shipped).
"""
import asyncio
from datetime import datetime, timezone
from decimal import Decimal

from db.models import Base, Customer, Order, Subscription
from db.session import AsyncSessionLocal, engine


def _utc(year: int, month: int, day: int) -> datetime:
    return datetime(year, month, day, tzinfo=timezone.utc)


async def seed() -> None:
    # Drop + recreate all tables from the model metadata. create_all/drop_all are
    # sync calls, so we run them through the async engine's run_sync bridge.
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
        print("tables: dropped + recreated")

    async with AsyncSessionLocal() as session:
        alice = Customer(
            name="Alice Nguyen",
            email="alice@example.com",
            orders=[
                Order(status="delivered", item="USB-C cable",        total_amount=Decimal("12.99"),  created_at=_utc(2026, 3, 2)),
                Order(status="cancelled", item="Laptop stand",       total_amount=Decimal("39.50"),  created_at=_utc(2026, 4, 18)),
                Order(status="shipped",   item="Mechanical keyboard", total_amount=Decimal("89.00"), created_at=_utc(2026, 6, 10)),  # <- latest
            ],
            subscriptions=[
                Subscription(plan="pro", status="active", current_period_end=_utc(2026, 7, 1)),
            ],
        )

        bob = Customer(
            name="Bob Martinez",
            email="bob@example.com",
            orders=[
                Order(status="delivered", item="Wireless mouse", total_amount=Decimal("24.00"), created_at=_utc(2026, 5, 21)),
            ],
            subscriptions=[
                Subscription(plan="free", status="past_due", current_period_end=_utc(2026, 6, 5)),
            ],
        )

        carol = Customer(
            name="Carol Smith",
            email="carol@example.com",
            orders=[],  # no orders — useful for testing the "nothing found" path
            subscriptions=[
                Subscription(plan="enterprise", status="canceled", current_period_end=_utc(2026, 2, 28)),
            ],
        )

        # add_all cascades to the orders/subscriptions via the relationships.
        session.add_all([alice, bob, carol])
        await session.commit()

        print("seeded: 3 customers, 4 orders, 3 subscriptions")

    # Dispose the pool so the script exits cleanly instead of hanging on open
    # connections.
    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(seed())
