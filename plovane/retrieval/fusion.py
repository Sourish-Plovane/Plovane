"""Reciprocal Rank Fusion.

Each item scores the sum of 1 / (RRF_K + rank) across the lists it appears in.
RRF needs no tuning and handles scores on different scales, which is the whole
reason it is here: BM25 scores are unbounded and cosine similarities sit in
[-1, 1], so adding them directly would be meaningless.
"""

from __future__ import annotations

from collections.abc import Sequence

from plovane.config import RRF_K


# Input: ranked_lists (Sequence[Sequence[str]]), k (int)
# Output: list[tuple[str, float]]
# Processing: Fuse ranked ID lists into one list, best first.
# Changed by: Claude Code
# Date: 2026-10-12
def reciprocal_rank_fusion(
    ranked_lists: Sequence[Sequence[str]],
    *,
    k: int = RRF_K,
) -> list[tuple[str, float]]:
    """Fuse ranked ID lists into one list, best first.

    Args:
        ranked_lists: one sequence of entry IDs per retrieval arm, each already
            ordered best-first. Lists may differ in length and overlap freely.
        k: the RRF constant. Larger values flatten the curve, so a top-1 hit in
            one arm counts for less against broad agreement across arms.

    Returns:
        ``(entry_id, fused_score)`` pairs sorted by descending score. Ties break
        on entry ID so the output is deterministic for a fixed input.
    """
    if k <= 0:
        raise ValueError(f"RRF k must be positive, got {k}")

    scores: dict[str, float] = {}
    for ranked in ranked_lists:
        for rank, entry_id in enumerate(ranked, start=1):
            scores[entry_id] = scores.get(entry_id, 0.0) + 1.0 / (k + rank)

    return sorted(scores.items(), key=lambda kv: (-kv[1], kv[0]))
