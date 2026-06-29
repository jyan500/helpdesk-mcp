"""
Throwaway learning script (Phase 3): WATCH an embedding get generated.

Goal: demystify "where do the 384 numbers come from?" — not production code.
Run it from the server/ directory:

    python scratch_embeddings.py

First run downloads the model (~90MB) from HuggingFace; later runs are instant.

The four stages a sentence goes through, and what this script prints for each:
    1. TOKENIZE   text -> subword tokens -> integer IDs (lookup in a fixed vocab)
    2. EMBED+MIX  the network turns those IDs into one vector per token, then the
                  Transformer layers mix them so each token's vector becomes
                  context-aware (this is the part we can't easily "see", but it's
                  why "money back" ~ "refund")
    3. POOL       average the per-token vectors into ONE 384-number sentence vector
    4. NORMALIZE  scale that vector to length 1, so cosine similarity == dot product

Then we compare sentences so you can SEE that meaning -> nearby vectors.
"""
from sentence_transformers import SentenceTransformer
import numpy as np

# all-MiniLM-L6-v2: 6-layer Transformer, outputs a 384-dimensional vector.
# Small, fast, free, runs locally on CPU — the standard default for this kind of work.
MODEL_NAME = "sentence-transformers/all-MiniLM-L6-v2"

model = SentenceTransformer(MODEL_NAME)
print(f"loaded {MODEL_NAME}\n")


# ---------------------------------------------------------------------------
# STAGE 1 — Tokenization. See the text become integer IDs.
# These IDs are just row-indices into the model's learned vocabulary; there's
# nothing meaningful about the specific numbers yet — they're lookup keys.
# ---------------------------------------------------------------------------
sentence = "How do I get my money back?"
tokenizer = model.tokenizer

ids = tokenizer(sentence)["input_ids"]
tokens = tokenizer.convert_ids_to_tokens(ids)
print("STAGE 1 — tokenize")
print(f"  text:   {sentence!r}")
print(f"  tokens: {tokens}")
print(f"  ids:    {ids}")
print("  (note: 101/102 are special [CLS]/[SEP] markers; '##' means a word-piece)\n")


# ---------------------------------------------------------------------------
# STAGES 2-4 — Run the whole network and get the final sentence vector.
# encode() does token-embedding lookup -> Transformer mixing -> mean pooling ->
# normalization, all at once. normalize_embeddings=True does STAGE 4 for us.
# ---------------------------------------------------------------------------
vec = model.encode(sentence, normalize_embeddings=True)
print("STAGES 2-4 — embed, mix, pool, normalize")
print(f"  vector length:     {len(vec)}            (the 384 dimensions)")
print(f"  first 8 numbers:   {np.round(vec[:8], 4)}")
print(f"  vector magnitude:  {np.linalg.norm(vec):.4f}   (==1.0 because we normalized)\n")


# ---------------------------------------------------------------------------
# THE PAYOFF — meaning, not keywords. Compare a few sentences.
# Because vectors are normalized, cosine similarity is just the dot product.
# Watch: the paraphrase scores HIGH despite sharing almost no words; the
# unrelated sentence scores LOW despite being about the same product domain.
# ---------------------------------------------------------------------------
sentences = [
    "How do I get my money back?",      # the query
    "You can request a refund within 30 days of purchase.",  # paraphrase-ish
    "Refunds take 5 to 10 business days to appear.",         # related
    "Our office is open Monday to Friday.",                  # unrelated
]
vecs = model.encode(sentences, normalize_embeddings=True)

query, *rest = sentences
qv, *rvs = vecs
print("THE PAYOFF — cosine similarity to the query")
print(f"  query: {query!r}\n")
for s, v in zip(rest, rvs):
    sim = float(np.dot(qv, v))   # normalized vectors -> dot product == cosine sim
    bar = "#" * int(sim * 40)
    print(f"  {sim:+.3f} {bar:<40} {s}")
print("\n  ^ no shared keywords with the top match, yet it ranks highest — that's"
      "\n    the whole reason RAG retrieves by embedding instead of by keyword.")
