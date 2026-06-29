"""
Knowledge-agent tool (Phase 3) — SCAFFOLD. Fill in the TODOs.

Same three layers as tools/account.py — the structure is the lesson, reused:

  1. Pydantic output schema — the ONLY shape the model sees. For RAG the retrieved
     chunk text AND its source title both matter: the text is the context the
     model answers from, the title is what it CITES.

  2. async tool function — search_docs(session, query, top_k). This is the heart
     of retrieval: embed the query, score it against every stored chunk by cosine
     similarity, return the top-k. We do the math in Python/NumPy (the pgvector
     swap is a later step — see db/models.DocChunk).

  3. Gemini FunctionDeclaration + a name->callable registry — what the model sees
     and how the loop dispatches. NOTE the model never sees `session` (injected by
     the loop, same as the account tools).

Quick test once filled in (from server/, after `python -m db.ingest`):
    python -m tools.knowledge
"""
from __future__ import annotations

import numpy as np
from google.genai import types
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from db.models import Article, DocChunk
from utils.embeddings import embed_query

# Default number of chunks to retrieve. Raised 3 -> 5 in Phase 4: at 3 the refund
# "30-day eligibility" chunk fell just below the cutoff for "is my shipped order
# refundable?", so the agent answered without the rule and hallucinated a
# disqualifier. More context = more chance to wander, but here it closes a real gap.
DEFAULT_TOP_K = 5


# ---------------------------------------------------------------------------
# 1. OUTPUT SCHEMA — one retrieved chunk, shaped for the model.
#    article_title is the citation; score lets you (and the model) see how strong
#    each match was. We deliberately DON'T expose embeddings or internal ids.
# ---------------------------------------------------------------------------
class RetrievedChunk(BaseModel):
    content: str         # the chunk text — the actual context to answer from
    article_title: str   # the source to cite, e.g. "Refunds & Returns"
    slug: str            # stable source id, e.g. "refunds-and-returns"
    score: float         # cosine similarity to the query (1.0 = identical meaning)


# ---------------------------------------------------------------------------
# 2. TOOL FUNCTION — embed the query, cosine-rank every chunk, return top-k.
# ---------------------------------------------------------------------------
async def search_docs(
    session: AsyncSession, query: str, top_k: int = DEFAULT_TOP_K
) -> dict:
    """Vector-search the knowledge base. Return the top_k most similar chunks.

    This is the whole RAG retrieval step. Pointers:

      A. EMBED THE QUERY (same model/space as the stored chunks):
           q = embed_query(query)            # 384-float list
           q = np.array(q, dtype=np.float32)

      B. LOAD CHUNKS + THEIR ARTICLE (we need the title to cite). Join so the
         article comes back in the same query — no lazy-load surprises under async:
           stmt = select(DocChunk, Article).join(Article, DocChunk.article_id == Article.id)
           rows = (await session.execute(stmt)).all()   # list of (DocChunk, Article)
         If there are no rows, return {"count": 0, "chunks": []} (nothing ingested).

      C. SCORE BY COSINE SIMILARITY. Our stored vectors AND the query are
         normalized (embed_* uses normalize_embeddings=True), so cosine == dot
         product. Stack and do it in one matrix-vector multiply:
           mat = np.array([c.embedding for c, _ in rows], dtype=np.float32)  # (N, 384)
           scores = mat @ q                                                  # (N,)
         (matrix @ vector gives one dot product per chunk — that's the similarity
          you watched in scratch_embeddings.py, computed for every chunk at once.)

      D. TAKE THE TOP-K. Sort indices by score descending and keep the first
         top_k:
           order = np.argsort(scores)[::-1][:top_k]
         Then shape each winner through RetrievedChunk(...).model_dump():
           chunk, article = rows[idx]
           RetrievedChunk(content=chunk.content, article_title=article.title,
                          slug=article.slug, score=float(scores[idx]))

      E. RETURN an envelope (count + list), like the account tools do:
           return {"count": len(results), "chunks": results}
    """

    # embed the user query to get the vector containing the floats generated from the transformer model
    q = embed_query(query)
    # convert to numpy array for calculation purposes
    q = np.array(q, dtype=np.float32)

    # load chunks and the corresponding article
    stmt = select(DocChunk, Article).join(Article, DocChunk.article_id == Article.id)
    rows = (await session.execute(stmt)).all() # list of (DocChunk, Article)
    if len(rows) == 0:
        return {"count": 0, "chunks": []}

    # score by cosine similarity
    mat = np.array([c.embedding for c, _ in rows], dtype=np.float32)
    # note that the @ is a shortcut for matrix multiplication, calculating the dot product with
    # the rows of mat and columns of q
    scores = mat @ q

    # take the top-k
    # argsort then [::-1] to make the score descending, then slice the top_k
    order = np.argsort(scores)[::-1][:top_k]
    results = []
    # note that the order array contains the actual indices of each chunk in rows
    # but it's ordered by the cosign similarity score
    for idx in order:
        chunk, article = rows[idx]
        results.append(
            RetrievedChunk(content=chunk.content, article_title=article.title, slug=article.slug, score=float(scores[idx])).model_dump()
        )

    return {"count": len(results), "chunks": results}



# ---------------------------------------------------------------------------
# 3a. REGISTRY — name -> async callable. The loop calls:
#       await TOOLS[name](session, **model_provided_args)
# ---------------------------------------------------------------------------
TOOLS = {
    "search_docs": search_docs,
}

# ---------------------------------------------------------------------------
# 3b. DECLARATION — what the model SEES. The description is doing real work here:
#     it's how the model knows this tool answers "how do I…" / policy questions
#     by searching the help center, and that it should cite the result. `top_k`
#     is optional (note it's NOT in `required`), so the model usually omits it and
#     we use the default.
# ---------------------------------------------------------------------------
search_docs_decl = types.FunctionDeclaration(
    name="search_docs",
    description=(
        "Search the help-center knowledge base for articles relevant to a user's "
        "question (refunds, shipping, billing, account/login, support hours, etc.). "
        "Returns the most relevant text chunks, each with the title of the article "
        "it came from. Use this for 'how do I…' and policy questions, then answer "
        "from the returned chunks and cite the article title."
    ),
    parameters=types.Schema(
        type=types.Type.OBJECT,
        properties={
            "query": types.Schema(
                type=types.Type.STRING,
                description="The user's question or a focused search phrase.",
            ),
            "top_k": types.Schema(
                type=types.Type.INTEGER,
                description="How many chunks to return (optional; defaults to 3).",
            ),
        },
        required=["query"],
    ),
)

KNOWLEDGE_TOOL_DECLS = [search_docs_decl]


# ---------------------------------------------------------------------------
# Optional smoke test: run retrieval directly, no LLM. Proves your vectors and
# similarity math work before wiring the tool into the agent loop.
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import asyncio

    from db.session import AsyncSessionLocal, engine

    async def _smoke():
        async with AsyncSessionLocal() as session:
            # res = await search_docs(session, "how long do refunds take?")
            res = await search_docs(session, "Can I return a final sale item?")
            print(f"got {res['count']} chunks")
            for c in res["chunks"]:
                print(f"  {c['score']:.3f}  [{c['article_title']}]  {c['content'][:70]}...")
        await engine.dispose()

    asyncio.run(_smoke())
