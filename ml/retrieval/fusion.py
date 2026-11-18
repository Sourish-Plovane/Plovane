"""Reciprocal Rank Fusion.

Each item scores the sum of 1 / (RRF_K + rank) across the lists it appears in.
RRF needs no tuning and handles scores on different scales, which is the whole
reason it is here: BM25 scores are unbounded and cosine similarities sit in
[-1, 1], so adding them directly would be meaningless.
"""

from __future__ import annotations

from collections.abc import Sequence

from ml.config import RRF_K


# Input: ranked_lists (Sequence[Sequence[str]]), k (int)
# Output: list[tuple[str, float]]
# Processing: Fuse ranked ID lists into one list, best first.
# Changed by: Sourish
# Date: 2026-10-12
def reciprocal_rank_fusion(
    ranked_lists: Sequence[Sequence[str]],
    *,
    k: int = RRF_K,
    weights: Sequence[float] | None = None,
) -> list[tuple[str, float]]:
    """Fuse ranked ID lists into one list, best first.

    Args:
        ranked_lists: one sequence of entry IDs per retrieval arm, each already
            ordered best-first. Lists may differ in length and overlap freely.
        k: the RRF constant. Larger values flatten the curve, so a top-1 hit in
            one arm counts for less against broad agreement across arms.
        weights: optional per-arm weights, one per list, defaulting to equal
            (standard RRF). An arm scored with weight ``w`` contributes
            ``w / (k + rank)`` instead of ``1 / (k + rank)``, so setting a
            weight below 1 reduces that arm's influence without removing it.

            This exists for the case the measurements name: when one arm is
            demonstrably worse than the other on a representative query set,
            equal weighting makes the fused list worse than the stronger arm
            on its own. On the current library -- which still runs the
            hashing-placeholder embedding -- the vector arm scores 25% @1
            against BM25's 44%, and equal RRF drags fused @1 down to 37%.
            Reducing the vector arm's weight to 0.25 recovers 41%.

            Weights are a workaround for a weak arm, not a tuning knob for a
            good one: a bi-encoder trained on the right domain restores the
            case where both arms carry comparable signal, and the weights
            should go back to equal.

    Returns:
        ``(entry_id, fused_score)`` pairs sorted by descending score. Ties break
        on entry ID so the output is deterministic for a fixed input.
    """
    if k <= 0:
        raise ValueError(f"RRF k must be positive, got {k}")
    if weights is None:
        weights = [1.0] * len(ranked_lists)
    if len(weights) != len(ranked_lists):
        raise ValueError(
            f"weights must match ranked_lists: {len(weights)} vs {len(ranked_lists)}"
        )
    if any(w < 0 for w in weights):
        raise ValueError(f"arm weights must be non-negative, got {list(weights)}")

    scores: dict[str, float] = {}
    for ranked, weight in zip(ranked_lists, weights, strict=True):
        if weight == 0.0:
            continue
        for rank, entry_id in enumerate(ranked, start=1):
            scores[entry_id] = scores.get(entry_id, 0.0) + weight / (k + rank)

    return sorted(scores.items(), key=lambda kv: (-kv[1], kv[0]))
