"""Keyword search.

Matches exact names and identifiers such as ``stratified_split`` or
``DataLoader`` -- the cases where vector search is weakest, because two
identifiers can be lexically almost identical and semantically unrelated.

On scoring: Postgres's built-in ``ts_rank`` is *not* BM25. It has no document
length normalisation and no IDF saturation, so it systematically favours long
documents and over-rewards repeated terms. For real BM25 use a Postgres
extension that provides it (``pg_search``/ParadeDB, ``pg_bm25``) or a separate
engine such as OpenSearch or Meilisearch. :class:`InMemoryBM25Index` is the
reference implementation this repo scores against; it is correct but single
process, so it is for evaluation and tests, not for serving the whole library.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from collections.abc import Iterable, Sequence
from typing import Protocol

from plovane.config import BM25_B, BM25_K1, RETRIEVAL_TOP_K
from plovane.retrieval.filters import FilterSpec
from plovane.schemas import Entry

_TOKEN_RE = re.compile(r"[A-Za-z0-9_]+")
_CAMEL_BOUNDARY = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")


def tokenize(text: str) -> list[str]:
    """Split code-ish text into terms.

    Identifiers are kept whole *and* split on snake_case and camelCase
    boundaries, so ``DataLoader`` is findable as ``dataloader``, ``data`` and
    ``loader``, and a query for "data loader" hits it without the user
    guessing the exact casing.
    """
    tokens: list[str] = []
    for raw in _TOKEN_RE.findall(text):
        lowered = raw.lower()
        tokens.append(lowered)
        pieces = [p for chunk in lowered.split("_") if chunk for p in _CAMEL_BOUNDARY.split(chunk)]
        sub = [p.lower() for p in _CAMEL_BOUNDARY.split(raw) if p]
        for piece in (*pieces, *sub):
            piece = piece.lower()
            if piece and piece != lowered:
                tokens.append(piece)
    return tokens


class KeywordIndex(Protocol):
    """The contract a BM25 backend must satisfy.

    Implemented here in memory; implement it again over ParadeDB, OpenSearch or
    Meilisearch for serving. The filter is part of the contract on purpose --
    it has to run inside the search, not after it.
    """

    def search(
        self,
        query: str,
        *,
        limit: int = RETRIEVAL_TOP_K,
        filters: FilterSpec | None = None,
    ) -> list[tuple[str, float]]:
        """Return ``(entry_id, bm25_score)`` best first, at most ``limit``."""
        ...


class InMemoryBM25Index:
    """Okapi BM25 over the indexed fields of each entry.

    Indexes name, signature, docstring, tags and generated description -- the
    document :meth:`Entry.indexed_text` builds.
    """

    def __init__(self, entries: Iterable[Entry] = (), *, k1: float = BM25_K1, b: float = BM25_B) -> None:
        self.k1 = k1
        self.b = b
        self._entries: dict[str, Entry] = {}
        self._term_freqs: dict[str, Counter[str]] = {}
        self._lengths: dict[str, int] = {}
        self._doc_freq: Counter[str] = Counter()
        for entry in entries:
            self.add(entry)

    def __len__(self) -> int:
        return len(self._entries)

    def add(self, entry: Entry) -> None:
        """Add or replace an entry. Replacing retracts the old term counts
        first, so re-indexing an edited entry does not inflate its IDF."""
        if entry.id in self._entries:
            self.remove(entry.id)
        tokens = tokenize(entry.indexed_text())
        freqs = Counter(tokens)
        self._entries[entry.id] = entry
        self._term_freqs[entry.id] = freqs
        self._lengths[entry.id] = len(tokens)
        for term in freqs:
            self._doc_freq[term] += 1

    def remove(self, entry_id: str) -> None:
        if entry_id not in self._entries:
            return
        for term in self._term_freqs[entry_id]:
            self._doc_freq[term] -= 1
            if self._doc_freq[term] <= 0:
                del self._doc_freq[term]
        del self._entries[entry_id]
        del self._term_freqs[entry_id]
        del self._lengths[entry_id]

    @property
    def average_length(self) -> float:
        if not self._lengths:
            return 0.0
        return sum(self._lengths.values()) / len(self._lengths)

    def _idf(self, term: str) -> float:
        """Robertson-Sparck Jones IDF with the +0.5 smoothing, floored at zero.

        Without the floor a term present in more than half the corpus scores
        negative, which would let a common term *penalise* a match.
        """
        n = len(self._entries)
        df = self._doc_freq.get(term, 0)
        return max(0.0, math.log(1.0 + (n - df + 0.5) / (df + 0.5)))

    def search(
        self,
        query: str,
        *,
        limit: int = RETRIEVAL_TOP_K,
        filters: FilterSpec | None = None,
    ) -> list[tuple[str, float]]:
        terms = tokenize(query)
        if not terms or not self._entries:
            return []

        avgdl = self.average_length or 1.0
        query_freqs = Counter(terms)
        scored: list[tuple[str, float]] = []

        for entry_id, entry in self._entries.items():
            if filters is not None and not filters.matches(entry):
                continue
            freqs = self._term_freqs[entry_id]
            length = self._lengths[entry_id]
            score = 0.0
            for term, qf in query_freqs.items():
                tf = freqs.get(term)
                if not tf:
                    continue
                denom = tf + self.k1 * (1.0 - self.b + self.b * length / avgdl)
                score += self._idf(term) * qf * tf * (self.k1 + 1.0) / denom
            if score > 0.0:
                scored.append((entry_id, score))

        scored.sort(key=lambda kv: (-kv[1], kv[0]))
        return scored[:limit]


# -- Serving backend ---------------------------------------------------------

BM25_SEARCH_SQL = """
-- ParadeDB / pg_search. The @@@ operator scores with real BM25; paradedb.score
-- exposes it. Swap this for an OpenSearch or Meilisearch query if the library
-- outgrows a Postgres extension -- the KeywordIndex contract is unchanged.
SELECT e.id, paradedb.score(e.id) AS bm25_score
FROM entries e
WHERE e.search_document @@@ :query
  AND {filters}
ORDER BY bm25_score DESC
LIMIT :limit
"""
"""Reference serving query. ``{filters}`` takes :meth:`FilterSpec.where_clause`.

Not exercised by the test suite: it needs a live Postgres with the extension
installed, so it is verified in the integration environment, not here.
"""


def render_bm25_sql(filters: FilterSpec) -> tuple[str, dict[str, object]]:
    """Render :data:`BM25_SEARCH_SQL` with this request's filters bound in."""
    clause, params = filters.where_clause()
    return BM25_SEARCH_SQL.format(filters=clause or "TRUE"), params
