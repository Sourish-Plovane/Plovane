"""Vector search.

Matches meaning when the words differ -- "split a dataset while keeping class
balance" finding ``stratified_split`` even though the two share no terms.
Entries are embedded with a code-specialised embedding model and stored in
pgvector with an HNSW index under cosine distance.

On choosing the embedding model: measure recall on your own query set rather
than trusting benchmark claims. Open code-specialised models such as
``jina-embeddings-v2-base-code`` are reasonable starting candidates, not
conclusions -- :mod:`plovane.evaluation` exists to settle it.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any, Protocol

import numpy as np

from plovane.config import HNSW_EF_SEARCH, PGVECTOR_ITERATIVE_SCAN, RETRIEVAL_TOP_K
from plovane.retrieval.filters import FilterSpec
from plovane.schemas import Entry


# Input: vectors (np.ndarray), axis (int), eps (float)
# Output: np.ndarray
# Processing: Scale to unit length so cosine similarity is a plain dot product.
# Changed by: Claude Code
# Date: 2026-10-12
def l2_normalize(vectors: np.ndarray, *, axis: int = -1, eps: float = 1e-12) -> np.ndarray:
    """Scale to unit length so cosine similarity is a plain dot product.

    Zero vectors are left alone rather than producing NaNs: an entry that
    failed to embed should score 0 against everything, not poison the batch.
    """
    vectors = np.asarray(vectors, dtype=np.float32)
    norms = np.linalg.norm(vectors, axis=axis, keepdims=True)
    return vectors / np.maximum(norms, eps)


class VectorIndex(Protocol):
    """The contract a vector backend must satisfy."""

    # Input: embedding (np.ndarray), limit (int), filters (FilterSpec | None)
    # Output: list[tuple[str, float]]
    # Processing: Return ``(entry_id, cosine_similarity)`` best first.
    # Changed by: Claude Code
    # Date: 2026-10-12
    def search(
        self,
        embedding: np.ndarray,
        *,
        limit: int = RETRIEVAL_TOP_K,
        filters: FilterSpec | None = None,
    ) -> list[tuple[str, float]]:
        """Return ``(entry_id, cosine_similarity)`` best first."""
        ...


class InMemoryVectorIndex:
    """Exact brute-force cosine search.

    No HNSW approximation, so it gives the true nearest neighbours. That makes
    it the right thing for evaluation -- it measures the embedding model
    without the index's recall loss mixed in -- and the wrong thing for
    serving a large library.
    """

    # Input: entries (Iterable[Entry])
    # Output: None
    # Processing: Init
    # Changed by: Claude Code
    # Date: 2026-10-12
    def __init__(self, entries: Iterable[Entry] = ()) -> None:
        self._entries: dict[str, Entry] = {}
        self._vectors: dict[str, np.ndarray] = {}
        for entry in entries:
            self.add(entry)

    # Input: none
    # Output: int
    # Processing: Len
    # Changed by: Claude Code
    # Date: 2026-10-12
    def __len__(self) -> int:
        return len(self._entries)

    # Input: entry (Entry)
    # Output: None
    # Processing: Add
    # Changed by: Claude Code
    # Date: 2026-10-12
    def add(self, entry: Entry) -> None:
        if entry.embedding is None:
            raise ValueError(f"entry {entry.id} has no embedding to index")
        self._entries[entry.id] = entry
        self._vectors[entry.id] = l2_normalize(np.asarray(entry.embedding, dtype=np.float32))

    # Input: entry_id (str)
    # Output: None
    # Processing: Remove
    # Changed by: Claude Code
    # Date: 2026-10-12
    def remove(self, entry_id: str) -> None:
        self._entries.pop(entry_id, None)
        self._vectors.pop(entry_id, None)

    # Input: embedding (np.ndarray), limit (int), filters (FilterSpec | None)
    # Output: list[tuple[str, float]]
    # Processing: Search
    # Changed by: Claude Code
    # Date: 2026-10-12
    def search(
        self,
        embedding: np.ndarray,
        *,
        limit: int = RETRIEVAL_TOP_K,
        filters: FilterSpec | None = None,
    ) -> list[tuple[str, float]]:
        if not self._vectors:
            return []

        ids = [
            entry_id
            for entry_id, entry in self._entries.items()
            if filters is None or filters.matches(entry)
        ]
        if not ids:
            return []

        matrix = np.stack([self._vectors[entry_id] for entry_id in ids])
        query = l2_normalize(np.asarray(embedding, dtype=np.float32).reshape(-1))
        sims = matrix @ query

        order = np.argsort(-sims, kind="stable")[:limit]
        return [(ids[i], float(sims[i])) for i in order]


# -- Serving backend ---------------------------------------------------------

PGVECTOR_SEARCH_SQL = """
SELECT e.id, 1 - (e.embedding <=> :query_embedding) AS cosine_similarity
FROM entries e
WHERE {filters}
ORDER BY e.embedding <=> :query_embedding
LIMIT :limit
"""
"""Cosine distance (``<=>``) under an HNSW index.

Not exercised by the test suite: it needs a live Postgres with pgvector, so it
is verified in the integration environment.
"""

CREATE_HNSW_INDEX_SQL = """
CREATE INDEX IF NOT EXISTS entries_embedding_hnsw
ON entries USING hnsw (embedding vector_cosine_ops)
WITH (m = 16, ef_construction = 64)
"""


# Input: ef_search (int), iterative_scan (str | None)
# Output: list[str]
# Processing: Per-connection settings that keep *filtered* vector search honest.
# Changed by: Claude Code
# Date: 2026-10-12
def pgvector_session_sql(
    *,
    ef_search: int = HNSW_EF_SEARCH,
    iterative_scan: str | None = PGVECTOR_ITERATIVE_SCAN,
) -> list[str]:
    """Per-connection settings that keep *filtered* vector search honest.

    Filtered vector search can return fewer rows than requested, because HNSW
    picks its nearest neighbours before the SQL filter removes some of them. A
    request narrowed to private CUDA entries can come back with a handful of
    rows out of a library that holds hundreds. Two fixes, applied together
    here: raise ``hnsw.ef_search`` so more neighbours are considered, and turn
    on iterative index scans (pgvector >= 0.8.0) so the scan continues until
    enough rows survive the filter.
    """
    statements = [f"SET LOCAL hnsw.ef_search = {int(ef_search)}"]
    if iterative_scan and iterative_scan != "off":
        if iterative_scan not in {"strict_order", "relaxed_order"}:
            raise ValueError(
                f"iterative_scan must be 'off', 'strict_order' or 'relaxed_order', got {iterative_scan!r}"
            )
        statements.append(f"SET LOCAL hnsw.iterative_scan = {iterative_scan}")
    return statements


# Input: filters (FilterSpec)
# Output: tuple[str, dict[str, Any]]
# Processing: Render :data:`PGVECTOR_SEARCH_SQL` with this request's filters bound in.
# Changed by: Claude Code
# Date: 2026-10-12
def render_vector_sql(filters: FilterSpec) -> tuple[str, dict[str, Any]]:
    """Render :data:`PGVECTOR_SEARCH_SQL` with this request's filters bound in."""
    clause, params = filters.where_clause()
    return PGVECTOR_SEARCH_SQL.format(filters=clause or "TRUE"), params
