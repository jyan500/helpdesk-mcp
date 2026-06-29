"""
Local embeddings (Phase 3) — SCAFFOLD. Fill in the TODOs.

This is the one place in the app that turns text into vectors. BOTH sides of RAG
go through here, which is the whole point:
    - ingest time:  embed every doc CHUNK  (db/ingest.py)
    - query time:   embed the user's QUESTION  (tools/knowledge.py)
The query and the chunks MUST be embedded by the same model the same way, or the
vectors live in different spaces and cosine similarity is meaningless.

You already saw this model run in scratch_embeddings.py — this is that same
`model.encode(..., normalize_embeddings=True)` call, just wrapped so the rest of
the app imports one tidy function instead of touching the model directly.

Cost note: this runs LOCALLY on CPU. No per-token cost, no network at query time
(after the one-time model download). That's why the build plan picks local
sentence-transformers over a paid embeddings API.
"""
from __future__ import annotations

from sentence_transformers import SentenceTransformer

# all-MiniLM-L6-v2 -> 384-dim vectors. If you change this model you MUST change
# the DocChunk.embedding dimension to match AND re-run ingest, since old vectors
# would be the wrong size / wrong space.
MODEL_NAME = "sentence-transformers/all-MiniLM-L6-v2"
EMBED_DIM = 384  # keep in sync with the model above and with db/models.DocChunk

# Lazy singleton: load the model the FIRST time it's needed, then reuse it.
# Loading is the slow part (reads weights from disk); we never want to pay it
# twice or load it at import time (that would slow every `import` of this module).
_model: SentenceTransformer | None = None


def _get_model() -> SentenceTransformer:
    """Return the shared model instance, loading it once on first use."""
    global _model
    if _model is None:
        _model = SentenceTransformer(MODEL_NAME)
    return _model


def embed_texts(texts: list[str]) -> list[list[float]]:
    """Embed a LIST of strings -> a list of 384-float vectors (one per input).

    Used at ingest time to embed many chunks in one batched call (faster than
    one-at-a-time).

    Pointers (this is the scratch_embeddings.py call, batched):
      - model = _get_model()
      - vecs = model.encode(texts, normalize_embeddings=True)
          * normalize_embeddings=True is what makes cosine similarity == dot
            product later — don't skip it.
      - encode() returns a numpy array; the DB column wants plain Python floats,
        so convert:  return [v.tolist() for v in vecs]
    """
    # TODO: implement per the pointers above.
    model = _get_model()
    vecs = model.encode(texts, normalize_embeddings=True)
    return [v.tolist() for v in vecs]


def embed_query(text: str) -> list[float]:
    """Embed ONE string -> a single 384-float vector.

    Convenience wrapper for the query side (tools/knowledge.py embeds exactly one
    question). Implement it in terms of embed_texts so there's only one code path
    that talks to the model:
        return embed_texts([text])[0]
    """
    # TODO: implement per the pointer above.
    return embed_texts([text])[0]


# ---------------------------------------------------------------------------
# Optional smoke test: prove the util works before wiring it into ingest/search.
#     python -m utils.embeddings
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    vecs = embed_texts(["hello world", "how do I get a refund?"])
    print(f"got {len(vecs)} vectors, dim={len(vecs[0])} (expected {EMBED_DIM})")
    print("first 8 of vec[0]:", [round(x, 4) for x in vecs[0][:8]])
