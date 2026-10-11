"""Maximal Marginal Relevance.

Ranking by relevance alone tends to return several near-identical functions:
if ``stratified_split`` is the best answer, the second and third best are
usually its own variants. The agent gets three cards and one real option.

MMR picks each next result by trading relevance against similarity to what is
already chosen. The first pick is always the most relevant candidate, so the
list still opens with the best match.
"""

from __future__ import annotations

from collections.abc import Sequence

import numpy as np

from plovane.config import MMR_LAMBDA
from plovane.retrieval.vector import l2_normalize
from plovane.schemas import Candidate


def maximal_marginal_relevance(
    candidates: Sequence[Candidate],
    *,
    k: int,
    lambda_: float = MMR_LAMBDA,
) -> list[Candidate]:
    """Select at most ``k`` candidates, diversity-aware, best match first.

    Args:
        candidates: ordered best-first, each carrying ``calibrated_score`` and
            an entry embedding. A candidate without an embedding is treated as
            dissimilar to everything -- it can still be selected, it just
            cannot be recognised as a duplicate.
        k: how many to return.
        lambda_: 1.0 is pure relevance, 0.0 is pure diversity. The spec's
            starting value, 0.7, leans toward relevance: a duplicate has to be
            very similar before it loses to a clearly worse entry.

    Returns:
        The selected candidates in selection order.
    """
    if not candidates or k <= 0:
        return []
    if not 0.0 <= lambda_ <= 1.0:
        raise ValueError(f"lambda_ must be in [0, 1], got {lambda_}")

    k = min(k, len(candidates))
    relevance = np.asarray([c.calibrated_score or 0.0 for c in candidates], dtype=np.float32)

    # Relevance and similarity have to be on one scale for the trade-off to
    # mean anything. Calibrated scores already sit in [0, 1], as do cosine
    # similarities between unit vectors once negatives are clamped away.
    dim = next((c.entry.embedding.shape[-1] for c in candidates if c.entry.embedding is not None), None)
    if dim is None:
        # Nothing to compare: MMR degenerates to taking the top k, which is
        # the correct answer when no embeddings are available.
        return list(candidates[:k])

    vectors = np.stack(
        [
            l2_normalize(np.asarray(c.entry.embedding, dtype=np.float32))
            if c.entry.embedding is not None
            else np.zeros(dim, dtype=np.float32)
            for c in candidates
        ]
    )
    similarity = np.clip(vectors @ vectors.T, 0.0, 1.0)

    selected: list[int] = [int(np.argmax(relevance))]
    remaining = [i for i in range(len(candidates)) if i not in selected]

    while len(selected) < k and remaining:
        redundancy = similarity[np.ix_(remaining, selected)].max(axis=1)
        scores = lambda_ * relevance[remaining] - (1.0 - lambda_) * redundancy
        best = remaining[int(np.argmax(scores))]
        selected.append(best)
        remaining.remove(best)

    return [candidates[i] for i in selected]
