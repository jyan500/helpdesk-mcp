"""
Pending-action persistence (Phase 5) — SCAFFOLD. Fill in the TODOs.

This module is the bridge between the in-memory agent loop and the `pending_actions`
table. It does exactly two jobs, and keeping them HERE keeps agent/loop.py free of
both DB code and serialization fiddliness:

  1. (De)serialize the agent's conversation history.
     The loop works with `list[types.Content]` (genai objects). Postgres can only
     store JSON. serialize_contents/deserialize_contents convert between the two.

  2. CRUD the pending row.
     create_pending_action freezes a paused agent into a row and returns its id;
     load_pending_action thaws it; mark_pending_action records approve/deny.

THE GOTCHA worth internalizing (it's the whole reason serialization isn't trivial):
a Gemini-3 function-call part carries an opaque `thought_signature`. agent/loop.py
already learned that this MUST be echoed back on the model turn or the next request
400s. When we serialize the history to Postgres and reload it later, that signature
has to survive the round-trip too. model_dump(mode="json") / model_validate keep it
because it's a real field on the Content — but if you ever hand-roll the dicts,
that's the field people drop.
"""
from __future__ import annotations

import uuid

from google.genai import types
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from db.models import PendingAction


# ---------------------------------------------------------------------------
# 1. (DE)SERIALIZATION — list[types.Content] <-> JSON-safe list[dict].
#
# types.Content is a pydantic model, so it already knows how to dump/validate
# itself; we just map over the list. mode="json" makes nested bytes/enums
# JSON-safe (the thought_signature included).
# ---------------------------------------------------------------------------
def serialize_contents(contents: list[types.Content]) -> list[dict]:
    """genai history -> a JSON-storable list of dicts (for PendingAction.contents)."""
    # TODO: return [c.model_dump(mode="json") for c in contents]
    return [c.model_dump(mode="json") for c in contents]


def deserialize_contents(raw: list[dict]) -> list[types.Content]:
    """The inverse: JSON dicts from the DB -> genai Content objects the loop can use."""
    # TODO: return [types.Content.model_validate(c) for c in raw]
    return [types.Content.model_validate(c) for c in raw]


# ---------------------------------------------------------------------------
# 2. CRUD on the pending row.
# ---------------------------------------------------------------------------
async def create_pending_action(
    session: AsyncSession,
    agent_name: str,
    function_call: types.FunctionCall,
    contents: list[types.Content],
) -> str:
    """Freeze a paused agent into a `pending_actions` row; return its id (pending_id).

    Called by the loop the moment the model asks for an approval-gated tool. The
    `contents` passed in MUST already include the model turn that requested the
    tool (so resuming has the full history) — the loop appends it before calling us.

    Pointers:
      - Mint the id app-side so it exists before commit and can be streamed to the UI:
            pending_id = uuid.uuid4().hex          # 32 hex chars -> matches String(32)
      - Build the row:
            row = PendingAction(
                id=pending_id,
                agent_name=agent_name,
                tool_name=function_call.name,
                tool_args=dict(function_call.args),     # FunctionCall.args -> plain dict
                contents=serialize_contents(contents),
                # status defaults to "pending" via server_default — don't set it
            )
      - session.add(row); await session.commit()
      - return pending_id
    """
    # TODO: implement per the pointers above.
    pending_id = uuid.uuid4().hex
    row = PendingAction(
        id=pending_id,
        agent_name=agent_name,
        tool_name=function_call.name,
        tool_args=dict(function_call.args),
        contents=serialize_contents(contents),
        status="pending"
    )
    session.add(row)
    await session.commit()
    return pending_id


async def load_pending_action(
    session: AsyncSession, pending_id: str
) -> PendingAction | None:
    """Fetch one pending row by id (or None). The resume endpoint calls this first.

    Pointers:
      - result = await session.execute(
            select(PendingAction).where(PendingAction.id == pending_id))
      - return result.scalar_one_or_none()
    """
    # TODO: implement per the pointers above.
    result = await session.execute(select(PendingAction).where(PendingAction.id == pending_id))
    return result.scalar_one_or_none()


async def mark_pending_action(
    session: AsyncSession, pending_id: str, status: str
) -> None:
    """Record the lifecycle transition: pending -> approved | denied.

    Keeps the row as an audit trail of what was decided (Phase 6 observability will
    thank you) and guards against a double-approve if you check it before resuming.

    Pointers:
      - row = await load_pending_action(session, pending_id)
      - if row is None: return            # nothing to mark
      - row.status = status; await session.commit()
    """
    row = await load_pending_action(session, pending_id)
    if row is None:
        return
    row.status = status
    await session.commit()
