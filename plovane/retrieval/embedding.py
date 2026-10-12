"""The embedding-model interface, and a dependency-free stand-in for it.

The real model is :class:`plovane.retrieval.bi_encoder.CodeBiEncoder`, which
needs torch. Everything else in the stack talks to the :class:`EmbeddingModel`
protocol instead, so retrieval, ranking and evaluation can be exercised, and
the embedding model swapped, without touching them.
"""

from __future__ import annotations

import hashlib
from collections.abc import Sequence
from typing import Protocol, runtime_checkable

import numpy as np

from plovane.config import EMBEDDING_DIM
from plovane.retrieval.vector import l2_normalize
from plovane.schemas import Entry


@runtime_checkable
class EmbeddingModel(Protocol):
    """Encodes queries and entries into one shared vector space.

    Queries and entries get separate methods because a fine-tuned bi-encoder
    may prefix or pool them differently even when the weights are shared.
    """

    dimension: int

    # Input: texts (Sequence[str])
    # Output: np.ndarray
    # Processing: Return ``(len(texts), dimension)`` L2-normalised float32 vectors.
    # Changed by: Claude Code
    # Date: 2026-10-12
    def encode_queries(self, texts: Sequence[str]) -> np.ndarray:
        """Return ``(len(texts), dimension)`` L2-normalised float32 vectors."""
        ...

    # Input: entries (Sequence[Entry])
    # Output: np.ndarray
    # Processing: Return ``(len(entries), dimension)`` L2-normalised float32 vectors.
    # Changed by: Claude Code
    # Date: 2026-10-12
    def encode_entries(self, entries: Sequence[Entry]) -> np.ndarray:
        """Return ``(len(entries), dimension)`` L2-normalised float32 vectors."""
        ...


# Input: entry (Entry)
# Output: str
# Processing: What gets embedded for an entry.
# Changed by: Claude Code
# Date: 2026-10-12
def entry_embedding_text(entry: Entry) -> str:
    """What gets embedded for an entry.

    Name, signature, docstring and description carry the meaning; the code
    body is included last and truncated, because a long implementation
    otherwise dominates the pooled vector with syntax rather than intent.
    """
    parts = [
        f"<lang:{entry.language.lower()}>",
        entry.name,
        entry.signature or "",
        entry.docstring,
        entry.description,
        entry.code[:2000],
    ]
    return "\n".join(p for p in parts if p)


class HashingEmbeddingModel:
    """A deterministic hashing encoder. **Not a semantic model.**

    It maps tokens to fixed random directions, so it captures lexical overlap
    and nothing else -- it cannot match "keep class balance" to
    ``stratified_split``, which is the entire reason vector search exists.

    It is here so the pipeline, the rankers and the evaluation harness are
    runnable and testable without torch or a model download, and as the floor
    that any real embedding model must beat on recall@1000. Never serve it.
    """

    # Input: dimension (int), seed (int)
    # Output: None
    # Processing: Init
    # Changed by: Claude Code
    # Date: 2026-10-12
    def __init__(self, dimension: int = EMBEDDING_DIM, *, seed: int = 0) -> None:
        self.dimension = dimension
        self.seed = seed

    # Input: token (str)
    # Output: np.ndarray
    # Processing: Token vector
    # Changed by: Claude Code
    # Date: 2026-10-12
    def _token_vector(self, token: str) -> np.ndarray:
        digest = hashlib.blake2b(
            token.encode("utf-8"), digest_size=8, person=self.seed.to_bytes(8, "little")
        ).digest()
        rng = np.random.default_rng(int.from_bytes(digest, "little"))
        return rng.normal(size=self.dimension).astype(np.float32)

    # Input: text (str)
    # Output: np.ndarray
    # Processing: Encode one
    # Changed by: Claude Code
    # Date: 2026-10-12
    def _encode_one(self, text: str) -> np.ndarray:
        from plovane.retrieval.keyword import tokenize

        tokens = tokenize(text)
        if not tokens:
            return np.zeros(self.dimension, dtype=np.float32)
        return np.mean([self._token_vector(t) for t in tokens], axis=0)

    # Input: texts (Sequence[str])
    # Output: np.ndarray
    # Processing: Encode queries
    # Changed by: Claude Code
    # Date: 2026-10-12
    def encode_queries(self, texts: Sequence[str]) -> np.ndarray:
        if not texts:
            return np.zeros((0, self.dimension), dtype=np.float32)
        return l2_normalize(np.stack([self._encode_one(t) for t in texts]))

    # Input: entries (Sequence[Entry])
    # Output: np.ndarray
    # Processing: Encode entries
    # Changed by: Claude Code
    # Date: 2026-10-12
    def encode_entries(self, entries: Sequence[Entry]) -> np.ndarray:
        return self.encode_queries([entry_embedding_text(e) for e in entries])


# Input: model (EmbeddingModel), entries (Sequence[Entry])
# Output: None
# Processing: Fill in ``entry.embedding`` for a batch, ready for indexing.
# Changed by: Claude Code
# Date: 2026-10-12
def embed_entries_inplace(model: EmbeddingModel, entries: Sequence[Entry]) -> None:
    """Fill in ``entry.embedding`` for a batch, ready for indexing."""
    if not entries:
        return
    vectors = model.encode_entries(entries)
    for entry, vector in zip(entries, vectors, strict=True):
        entry.embedding = vector
