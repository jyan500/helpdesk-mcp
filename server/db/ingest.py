"""
Ingest the knowledge base (Phase 3) — SCAFFOLD. Fill in the TODOs.

This is the RAG "build the index" step, and it's the sibling of db/seed.py: where
seed.py loads customers/orders, this loads ARTICLES and their CHUNKS+EMBEDDINGS.
Run it from the server/ directory (after seed.py has created the DB):

    python -m db.ingest

The pipeline, end to end:
    1. LOAD    read every .md file in server/knowledge/ -> (title, body)
    2. CHUNK   slice each body into small overlapping pieces  <-- the quality knob
    3. EMBED   turn each chunk into a 384-float vector (utils.embeddings)
    4. STORE   write one Article row + its DocChunk rows (text + vector + index)

Re-runnable by design: we delete existing articles first (cascade clears their
chunks) so running it again gives a clean, predictable index — just like seed.py.

EXPERIMENT here (this is the Phase 3 learning checkpoint in practice): change
CHUNK_SIZE / CHUNK_OVERLAP, re-ingest, then ask the agent the same question and
watch retrieval quality change. Big chunks = more context but blurrier vectors;
tiny chunks = sharp vectors but answers can lose surrounding context.
"""
import asyncio
import re
from pathlib import Path

from sqlalchemy import delete, select

from db.models import Article, Base, DocChunk
from db.session import AsyncSessionLocal, engine
from utils.embeddings import embed_texts

# server/knowledge/*.md  (this file is server/db/ingest.py -> parent.parent = server/)
KNOWLEDGE_DIR = Path(__file__).resolve().parent.parent / "knowledge"

# ---- the quality knobs. Measured in WORDS here (simplest to reason about). ----
CHUNK_SIZE = 80      # words per chunk (used by the word-window FALLBACK below)
CHUNK_OVERLAP = 20   # words shared between consecutive word-window chunks
MAX_CHUNK_WORDS = 120  # a paragraph longer than this gets sub-split by the word window


def load_articles() -> list[tuple[str, str, str]]:
    """Read every .md file -> list of (slug, title, body).

    slug  = filename without extension, e.g. "refunds-and-returns"
    title = the first markdown H1 line ("# Refunds & Returns" -> "Refunds & Returns")
    body  = everything after the title line
    """
    articles: list[tuple[str, str, str]] = []
    for path in sorted(KNOWLEDGE_DIR.glob("*.md")):
        text = path.read_text(encoding="utf-8").strip()
        lines = text.splitlines()
        # First line is "# Title"; strip the leading '# ' for the citation title.
        title = lines[0].lstrip("# ").strip()
        body = "\n".join(lines[1:]).strip()
        articles.append((path.stem, title, body))
    return articles


def _chunk_words(text: str) -> list[str]:
    """Sliding word-window splitter — your Phase 3 chunker, kept as a HELPER.

    It's no longer the primary strategy; chunk_text() below calls it only as a
    FALLBACK, to sub-split a single paragraph that's too long to embed as one chunk.
    (Same sliding window as before: CHUNK_SIZE words per step of CHUNK_SIZE-OVERLAP.)
    """
    words = text.split()
    step = CHUNK_SIZE - CHUNK_OVERLAP
    if step <= 0:
        raise ValueError("CHUNK_OVERLAP must be smaller than CHUNK_SIZE")
    chunks = []
    i = 0
    while i < len(words):
        chunk_words = words[i:i + CHUNK_SIZE]
        chunks.append(" ".join(chunk_words))
        i = i + step
    return chunks


def chunk_text(body: str) -> list[str]:
    """Split an article body into PARAGRAPH-based chunks — SCAFFOLD, fill the TODOs.

    Why switch from word windows to paragraphs: in refunds-and-returns.md the
    "30-day window" sentence and the eligibility CONDITIONS sit in the same
    paragraph, but the fixed 80-word window scattered them into different chunks
    (and cut mid-sentence). Markdown separates paragraphs with a blank line, so each
    paragraph is already a self-contained idea — a much better unit to embed and
    retrieve whole.

    Pointers:
      1. SPLIT on blank lines into paragraphs, then strip() each and DROP empties.
         (Regex for "one or more blank lines" is in the TODO below.)
      2. For each paragraph, pick granularity by length:
           - short enough (<= MAX_CHUNK_WORDS words) -> keep it as ONE chunk
               chunks.append(para)
           - longer -> sub-split it so no chunk is huge, reusing the helper:
               chunks.extend(_chunk_words(para))
      3. Return the chunks in document order.

    EXPERIMENT (the Phase 3 checkpoint in practice): tweak MAX_CHUNK_WORDS, re-run
    `python -m db.ingest`, then re-ask the refund-eligibility question and watch
    which chunks come back.
    """
    # TODO: implement paragraph splitting per the pointers above.
    #   - paras = re.split(r"\n\s*\n", body)   # break on one-or-more blank lines
    #   - normalize: para = para.strip(); skip if empty
    #   - length test: len(para.split()) <= MAX_CHUNK_WORDS  -> one chunk; else _chunk_words(para)
    chunks = []
    paragraphs = re.split(r"\n\s*\n", body) # break on one or more blank lines
    for paragraph in paragraphs:
        if paragraph.strip() != "":
            if len(paragraph.split()) <= MAX_CHUNK_WORDS:
                chunks.append(paragraph.strip())
            else:
                # needs to be sub-split so no singular chunk is too big
                chunks.extend(_chunk_words(paragraph.strip()))
    return chunks

async def ingest() -> None:
    # Make sure the tables exist. create_all only creates what's missing, so this
    # won't touch the customers/orders seeded by seed.py.
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    articles = load_articles()
    print(f"loaded {len(articles)} articles from {KNOWLEDGE_DIR}")

    async with AsyncSessionLocal() as session:
        # Clean slate so re-running is idempotent. Deleting Articles cascades to
        # their DocChunks via the relationship's cascade="all, delete-orphan".
        existing = (await session.execute(select(Article))).scalars().all()
        for art in existing:
            await session.delete(art)
        await session.commit()

        total_chunks = 0
        for slug, title, body in articles:
            # TODO: build the index for ONE article:
            #   1. chunks = chunk_text(body)
            #   2. vectors = embed_texts(chunks)        # batched: one call per article
            #   3. create the Article with its DocChunks attached, e.g.:
            #        article = Article(
            #            slug=slug, title=title, body=body,
            #            chunks=[
            #                DocChunk(chunk_index=i, content=c, embedding=v)
            #                for i, (c, v) in enumerate(zip(chunks, vectors))
            #            ],
            #        )
            #        session.add(article)
            #   4. total_chunks += len(chunks); print a per-article line so you can
            #      see how many chunks each article produced (your quality knob).
            chunks = chunk_text(body)
            vectors = embed_texts(chunks)
            article = Article(
                slug=slug, title=title, body=body,
                chunks=[
                    DocChunk(chunk_index=i, content=c, embedding=v) for i, (c,v) in enumerate(zip(chunks, vectors))
                ]
            )
            session.add(article)
            total_chunks += len(chunks)
        await session.commit()
        print(f"ingested {len(articles)} articles, {total_chunks} chunks")

    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(ingest())
