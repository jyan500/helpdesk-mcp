"""
Async SQLAlchemy engine + session wiring (Phase 2).

Three pieces, in order of "how long they live":

  engine            one per process. Owns the connection pool to Postgres.
  AsyncSessionLocal a factory that hands out short-lived sessions.
  get_session()     a FastAPI dependency: one session per request, always closed.

The `+asyncpg` in DATABASE_URL tells SQLAlchemy to use the async asyncpg driver,
which is what lets us `await` queries inside `async def` endpoints.
"""
import os

from dotenv import load_dotenv
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

# Endpoints import this module at startup; load .env so DATABASE_URL is present
# even if something imports us before main.py's own load_dotenv() runs.
load_dotenv()

DATABASE_URL = os.environ["DATABASE_URL"]

# echo=True would log every SQL statement — handy while learning, noisy later.
engine = create_async_engine(DATABASE_URL, echo=False)

# expire_on_commit=False: after commit, objects keep their loaded attribute
# values instead of being expired and re-fetched on next access. Important with
# async — touching an expired attribute triggers lazy IO that can't happen
# implicitly under asyncio.
AsyncSessionLocal = async_sessionmaker(engine, expire_on_commit=False)


async def get_session() -> AsyncSession:
    """FastAPI dependency. Use as: `session: AsyncSession = Depends(get_session)`.

    The `async with` guarantees the session is closed (and its connection
    returned to the pool) when the request finishes, even on error.
    """
    async with AsyncSessionLocal() as session:
        yield session
