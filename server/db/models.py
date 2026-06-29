"""
SQLAlchemy ORM models for the Account agent's data (Phase 2).

These are the *tables* — the shape of what lives in Postgres. They are NOT what
the agent/LLM sees: the tools (Phase 2, step 5) convert rows into small Pydantic
objects so the model only ever receives the fields we choose to expose. Keeping
those two layers separate is the whole "don't let the model see data it
shouldn't" lesson.

Style note: this is SQLAlchemy 2.0 syntax — `Mapped[...]` type annotations plus
`mapped_column(...)`, which replaces the old `Column(...)` + `declarative_base()`
style you'll see in older tutorials.

Relationships here are one-to-many: 1 customer -> N orders, 1 customer -> N
subscriptions. The "many" side is whichever table holds the ForeignKey (orders,
subscriptions); a `Mapped[list[...]]` is the collection ("many") end and a plain
`Mapped["Customer"]` is the scalar ("one") end.
"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sqlalchemy import ARRAY, JSON, Float, ForeignKey, Numeric, String, Text, DateTime, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    """All models inherit from this; it carries the shared metadata/registry."""
    pass


class Customer(Base):
    __tablename__ = "customers"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    # email is how the agent looks a customer up, so it's unique + indexed.
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    # relationship() is the ORM-level link; the real DB constraint is the
    # ForeignKey on the child tables below. back_populates keeps both sides in
    # sync. cascade="all, delete-orphan": deleting a customer deletes their
    # orders/subscriptions, and detaching a child from the collection deletes it.
    orders: Mapped[list["Order"]] = relationship(
        back_populates="customer", cascade="all, delete-orphan"
    )
    subscriptions: Mapped[list["Subscription"]] = relationship(
        back_populates="customer", cascade="all, delete-orphan"
    )

class Order(Base):
    __tablename__ = "orders"

    id: Mapped[int] = mapped_column(primary_key=True)
    customer_id: Mapped[int] = mapped_column(ForeignKey("customers.id"), index=True)
    # Free-text-ish status. A real system would use an enum/check constraint;
    # a plain string keeps the learning focus on querying, not schema design.
    status: Mapped[str] = mapped_column(String(32))  # pending|shipped|delivered|cancelled|refunded
    item: Mapped[str] = mapped_column(String(200))
    # Money as Numeric(10,2) -> Python Decimal. Never float for currency.
    # Stores up to 10 digits, up to 2 decimal places i.e 99999999.99
    total_amount: Mapped[Decimal] = mapped_column(Numeric(precision=10, scale=2))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    customer: Mapped["Customer"] = relationship(back_populates="orders")


class Subscription(Base):
    __tablename__ = "subscriptions"

    id: Mapped[int] = mapped_column(primary_key=True)
    customer_id: Mapped[int] = mapped_column(ForeignKey("customers.id"), index=True)
    plan: Mapped[str] = mapped_column(String(32))    # free|pro|enterprise
    status: Mapped[str] = mapped_column(String(32))  # active|past_due|canceled
    current_period_end: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    customer: Mapped["Customer"] = relationship(back_populates="subscriptions")


# ===========================================================================
# Phase 3 — Knowledge base for RAG.
#
# Two tables, and the split mirrors how RAG actually works:
#
#   Article   one help-center document, stored whole. This is what we CITE
#             ("see: Refunds & Returns"), so the agent's answer can point a
#             user at a real source.
#   DocChunk  the Article sliced into small overlapping pieces, each with its
#             OWN embedding — one of the 384-number vectors from
#             scratch_embeddings.py. Retrieval happens at the CHUNK level; we
#             keep a FK back to the Article so a matched chunk still knows which
#             document — and therefore which citation — it came from.
#
# Why chunk at all? Embeddings capture meaning best over a focused span of
# text. Embedding a whole article blurs many topics into one vector; embedding
# small chunks keeps each vector "about one thing", so similarity search can
# surface the exact paragraph that answers the question. Chunk size/overlap is
# the main RAG-quality knob you'll experiment with in db/ingest.py.
# ===========================================================================
class Article(Base):
    __tablename__ = "articles"

    id: Mapped[int] = mapped_column(primary_key=True)
    # Human-facing title, used verbatim in citations ("Refunds & Returns").
    title: Mapped[str] = mapped_column(String(200))
    # A stable slug/filename for the source, e.g. "refunds-and-returns".
    slug: Mapped[str] = mapped_column(String(200), unique=True, index=True)
    # Full original text. Handy for debugging retrieval and for showing the
    # source; the agent never embeds this directly — it embeds the chunks.
    body: Mapped[str] = mapped_column(Text)

    # One article -> many chunks. Deleting/re-ingesting an article clears its
    # chunks (cascade), so re-running the ingest script stays clean.
    chunks: Mapped[list["DocChunk"]] = relationship(
        back_populates="article", cascade="all, delete-orphan"
    )


class DocChunk(Base):
    __tablename__ = "doc_chunks"

    id: Mapped[int] = mapped_column(primary_key=True)
    article_id: Mapped[int] = mapped_column(ForeignKey("articles.id"), index=True)
    # Position of this chunk within its article (0,1,2,...) — useful for
    # debugging and for stitching adjacent chunks back together if you want.
    chunk_index: Mapped[int] = mapped_column()
    # The actual text we embedded and will hand back to the model as context.
    content: Mapped[str] = mapped_column(Text)

    # The embedding: a fixed-length vector of floats (384 for all-MiniLM-L6-v2) —
    # exactly the kind of vector scratch_embeddings.py printed.
    #
    # We store it as a plain Postgres float8[] (ARRAY(Float)) for now and do the
    # cosine-similarity search in Python (see tools/knowledge.py). That's enough
    # for a tiny doc set and keeps the focus on the RAG concepts.
    #
    # The "production" path is pgvector's Vector(384) type, which does the
    # similarity search inside the database with an index. Swapping to it later
    # is a column-type change here (ARRAY(Float) -> Vector(384)) plus enabling
    # the extension — deliberately deferred (see CLAUDE.md / build plan).
    embedding: Mapped[list[float]] = mapped_column(ARRAY(Float))

    article: Mapped["Article"] = relationship(back_populates="chunks")


# ===========================================================================
# Phase 5 — Action agent + human-in-the-loop.
#
# Two new tables, and they sit at opposite ends of the "does this action need a
# gate?" spectrum that's the whole Phase 5 lesson:
#
#   Ticket          a row the ACTION agent CREATES (create_ticket). Opening a
#                   ticket is reversible/low-stakes, so it runs immediately — no
#                   approval. This is just "a write tool that inserts a row".
#
#   PendingAction   the hard part. When the agent wants to do something
#                   IRREVERSIBLE (issue a refund, send an email), we DON'T run the
#                   tool. We freeze the agent's state into THIS row, end the
#                   stream, and show the user an Approve/Deny prompt. On Approve we
#                   reload the row, run the tool, and resume the loop. This table
#                   IS the answer to the phase checkpoint, "how do you represent
#                   and resume a paused agent?" — the paused agent is just a row.
# ===========================================================================
class Ticket(Base):
    """A support ticket the action agent opens. Mirror the Order/Subscription style."""
    __tablename__ = "tickets"

    # TODO: fill in the columns. Suggested shape (lean — only what create_ticket needs):
    #   id: Mapped[int]            -> mapped_column(primary_key=True)
    #   customer_email: Mapped[str | None] -> mapped_column(String(255), nullable=True)
    #         (the agent may open a ticket without a known customer)
    #   subject: Mapped[str]       -> mapped_column(String(200))
    #   body: Mapped[str]          -> mapped_column(Text)
    #   status: Mapped[str]        -> mapped_column(String(32), server_default="open")
    #   created_at: Mapped[datetime] -> mapped_column(DateTime(timezone=True),
    #                                                  server_default=func.now())
    # (No relationship needed — a ticket is standalone for this learning slice.)
    id: Mapped[int] = mapped_column(primary_key=True)
    customer_email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    subject: Mapped[str] = mapped_column(String(200))
    body: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(32), server_default="open")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class PendingAction(Base):
    """A paused agent, persisted. One row = one action awaiting human approval.

    The serialized agent lives in `contents`: the full genai conversation history
    (user turn, any tool lookups, and the model turn that REQUESTED the gated tool)
    as JSON. To resume, agent/loop.py deserializes it, appends the tool's result
    (Approve) or a "declined" note (Deny), and re-enters the same loop. Storing it
    in Postgres — not memory — is what lets the pause survive a server restart and
    makes "resuming a paused agent" a concrete, inspectable thing.
    """
    __tablename__ = "pending_actions"

    # TODO: fill in the columns. Pointers:
    #   id: Mapped[str]   -> mapped_column(String(32), primary_key=True)
    #       NOTE: NOT autoincrement. We generate this app-side (uuid4().hex) so the
    #       value exists before commit and can be handed to the UI as `pending_id`
    #       to round-trip on the resume request. (agent/pending.py, subpart 3, mints it.)
    #
    #   agent_name: Mapped[str] -> mapped_column(String(32))
    #       Which specialist to resume — a key into AGENTS (e.g. "action"). On resume
    #       we look the AgentConfig back up by this name instead of re-classifying.
    #
    #   tool_name: Mapped[str]  -> mapped_column(String(64))
    #   tool_args: Mapped[dict] -> mapped_column(JSON)
    #       The exact call the user is approving — kept alongside `contents` so the
    #       resume step can run it directly (await agent.tools[tool_name](**tool_args))
    #       without re-parsing it out of the history.
    #
    #   contents: Mapped[list] -> mapped_column(JSON)
    #       The serialized conversation history (list of genai Content dicts). This
    #       is the "frozen agent". Watch out: the Gemini-3 thought_signature on the
    #       function-call part MUST survive serialization or the resumed call 400s
    #       (same gotcha agent/loop.py already handles when echoing the model turn).
    #
    #   status: Mapped[str] -> mapped_column(String(16), server_default="pending")
    #       pending | approved | denied — the action's lifecycle.
    #
    #   created_at: Mapped[datetime] -> mapped_column(DateTime(timezone=True),
    #                                                  server_default=func.now())
    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    agent_name: Mapped[str] = mapped_column(String(32))
    tool_name: Mapped[str] = mapped_column(String(64))
    tool_args: Mapped[dict] = mapped_column(JSON)
    contents: Mapped[list] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(16), server_default="pending")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
