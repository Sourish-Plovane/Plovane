"""Finding the library entries a candidate duplicates.

Two numbers come out of one pass, and keeping them apart is the point of this
module. The **best match** answers "does this already exist?". The **saturation
count** answers "does another one of these help anybody?". A fiftieth
string-trimming helper scores a perfect match on the first and zero on the
second, and a model that only measured similarity would recommend it anyway.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np

from ml.retrieval.vector import l2_normalize
from ml.schemas import Entry

DUPLICATE_THRESHOLD = 0.92
"""Cosine above which two entries are treated as the same thing.

High on purpose. A false duplicate is worse than a missed one here: it
cancels a suggestion the user would have wanted, and they never learn why.
"""

NEAR_THRESHOLD = 0.75
"""Cosine above which an entry counts toward saturation.

Lower than the duplicate line, because saturation is about a crowded
neighbourhood rather than an exact twin.
"""

SATURATION_LIMIT = 8
"""Near matches beyond which another entry adds nothing.

A guess, and the first number to tune once there is real library data. Too low
and the library stops growing in its most popular areas; too high and it fills
with variations nobody needs.
"""


@dataclass(frozen=True)
class Match:
    entry: Entry
    similarity: float


@dataclass
class SimilarityReport:
    """What the library already has in a candidate's neighbourhood."""

    best: Match | None
    near: tuple[Match, ...]

    # Input: none
    # Output: bool
    # Processing: Whether the best match is close enough to be a duplicate.
    # Changed by: Claude Code
    # Date: 2026-10-22
    @property
    def has_duplicate(self) -> bool:
        return self.best is not None and self.best.similarity >= DUPLICATE_THRESHOLD

    # Input: none
    # Output: int
    # Processing: Number of near matches in the candidate's neighbourhood.
    # Changed by: Claude Code
    # Date: 2026-10-22
    @property
    def saturation(self) -> int:
        return len(self.near)

    # Input: limit (int)
    # Output: bool
    # Processing: Whether the neighbourhood already has enough near matches.
    # Changed by: Claude Code
    # Date: 2026-10-22
    def is_saturated(self, limit: int = SATURATION_LIMIT) -> bool:
        return self.saturation >= limit


class LibraryIndex:
    """Embeddings of the library, for duplicate and saturation lookups.

    Separate from the retrieval vector index because the question differs.
    Retrieval asks which entries answer a query; this asks which entries *are*
    the candidate. Same arithmetic, opposite purpose, and the thresholds that
    matter are not the same ones.
    """

    # Input: entries (Sequence[Entry])
    # Output: None
    # Processing: Build the index from the entries' embeddings.
    # Changed by: Claude Code
    # Date: 2026-10-22
    def __init__(self, entries: Sequence[Entry] = ()) -> None:
        self._entries: list[Entry] = []
        self._matrix: np.ndarray | None = None
        if entries:
            self.rebuild(entries)

    # Input: none
    # Output: int
    # Processing: Number of indexed entries.
    # Changed by: Claude Code
    # Date: 2026-10-22
    def __len__(self) -> int:
        return len(self._entries)

    # Input: entries (Sequence[Entry])
    # Output: None
    # Processing: Replace the index, skipping entries without an embedding.
    # Changed by: Claude Code
    # Date: 2026-10-22
    def rebuild(self, entries: Sequence[Entry]) -> None:
        """Replace the index. Entries without an embedding are skipped.

        Skipped rather than rejected: a library mid-backfill should still
        answer duplicate questions about the part that is embedded, and a
        missing embedding is an ingestion gap, not a caller error.
        """
        usable = [e for e in entries if e.embedding is not None]
        self._entries = usable
        if not usable:
            self._matrix = None
            return
        self._matrix = l2_normalize(
            np.stack([np.asarray(e.embedding, dtype=np.float32) for e in usable])
        )

    # Input: embedding (np.ndarray), exclude_ids (Sequence[str]), near_threshold (float), language (str | None)
    # Output: SimilarityReport
    # Processing: Compare one embedding against the library: best match and near matches.
    # Changed by: Claude Code
    # Date: 2026-10-22
    def compare(
        self,
        embedding: np.ndarray,
        *,
        exclude_ids: Sequence[str] = (),
        near_threshold: float = NEAR_THRESHOLD,
        language: str | None = None,
    ) -> SimilarityReport:
        """Compare one candidate against the library.

        ``language`` restricts the comparison, because a Python helper and a
        JavaScript one that do the same job are not duplicates of each other --
        neither can replace the other in a user's project.
        """
        if self._matrix is None or embedding is None:
            return SimilarityReport(best=None, near=())

        excluded = set(exclude_ids)
        indices = [
            i
            for i, entry in enumerate(self._entries)
            if entry.id not in excluded
            and (language is None or entry.language.lower() == language.lower())
        ]
        if not indices:
            return SimilarityReport(best=None, near=())

        query = l2_normalize(np.asarray(embedding, dtype=np.float32).reshape(-1))
        sims = self._matrix[indices] @ query

        order = np.argsort(-sims, kind="stable")
        best_index = indices[int(order[0])]
        best = Match(entry=self._entries[best_index], similarity=float(sims[order[0]]))

        near = tuple(
            Match(entry=self._entries[indices[int(i)]], similarity=float(sims[i]))
            for i in order
            if float(sims[i]) >= near_threshold
        )
        return SimilarityReport(best=best, near=near)
