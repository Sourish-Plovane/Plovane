"""Evaluation metrics, one per thing that can go wrong.

Accuracy is only real if it is measured the same way every time, so each
component has a fixed metric and a fixed test set:

=======================  ==========================  ==================================
Model stage              Metric                      What it tells you
=======================  ==========================  ==================================
Retrieval (embeddings)   Recall@1000                 Whether the right entry reaches
                                                     ranking at all
Ranking (all 3 stages)   NDCG@10, MRR                Whether the right entry appears
                                                     near the top
Calibration              Expected calibration error  Whether a score of 0.8 really means
                                                     about 80% acceptance
Live usage               Acceptance rate,            Whether users keep what the models
                         reverted-insert rate        recommend
=======================  ==========================  ==================================

Every metric is reported per language as well as overall. A strong average
hides a language where results are poor, such as CUDA, where training data is
scarcest -- see :func:`per_language`.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

import numpy as np


# Input: retrieved_ids (Sequence[str]), relevant_ids (Sequence[str] | set[str]), k (int)
# Output: float
# Processing: Share of the relevant entries that appear in the first ``k`` results.
# Changed by: Claude Code
# Date: 2026-10-12
def recall_at_k(retrieved_ids: Sequence[str], relevant_ids: Sequence[str] | set[str], k: int) -> float:
    """Share of the relevant entries that appear in the first ``k`` results.

    This is the retrieval gate at k=1000. If it is low, no ranking model can
    fix it: ranking only reorders what retrieval returned.
    """
    relevant = set(relevant_ids)
    if not relevant:
        return 0.0
    if k <= 0:
        return 0.0
    found = sum(1 for entry_id in retrieved_ids[:k] if entry_id in relevant)
    return found / len(relevant)


# Input: gains (Sequence[float])
# Output: float
# Processing: Discounted cumulative gain with the standard log2 position discount.
# Changed by: Claude Code
# Date: 2026-10-12
def dcg(gains: Sequence[float]) -> float:
    """Discounted cumulative gain with the standard log2 position discount."""
    return sum(g / math.log2(rank + 1) for rank, g in enumerate(gains, start=1))


# Input: ranked_ids (Sequence[str]), relevance (Mapping[str, float]), k (int)
# Output: float
# Processing: Normalised DCG at ``k``, with exponential gains.
# Changed by: Claude Code
# Date: 2026-10-12
def ndcg_at_k(ranked_ids: Sequence[str], relevance: Mapping[str, float], k: int = 10) -> float:
    """Normalised DCG at ``k``, with exponential gains.

    Exponential gains (``2**rel - 1``) rather than the raw grade, so a
    perfect match at rank 2 is worth clearly more than a partial match at
    rank 1 -- which matches how an agent reads the list.

    Returns 0.0 when the query has no relevant entries at all, so an
    unanswerable query cannot inflate the average.
    """
    if k <= 0:
        return 0.0
    gains = [2.0 ** relevance.get(entry_id, 0.0) - 1.0 for entry_id in ranked_ids[:k]]
    ideal = sorted((2.0**grade - 1.0 for grade in relevance.values()), reverse=True)[:k]
    ideal_dcg = dcg(ideal)
    if ideal_dcg <= 0.0:
        return 0.0
    return dcg(gains) / ideal_dcg


# Input: ranked_ids (Sequence[str]), relevant_ids (Sequence[str] | set[str])
# Output: float
# Processing: 1 / rank of the first relevant result, or 0 if none appears.
# Changed by: Claude Code
# Date: 2026-10-12
def reciprocal_rank(ranked_ids: Sequence[str], relevant_ids: Sequence[str] | set[str]) -> float:
    """1 / rank of the first relevant result, or 0 if none appears."""
    relevant = set(relevant_ids)
    for rank, entry_id in enumerate(ranked_ids, start=1):
        if entry_id in relevant:
            return 1.0 / rank
    return 0.0


# Input: ranked_lists (Sequence[Sequence[str]]), relevant_sets (Sequence[Sequence[str] | set[str]])
# Output: float
# Processing: MRR across queries. Answers "how far down is the first good hit?".
# Changed by: Claude Code
# Date: 2026-10-12
def mean_reciprocal_rank(
    ranked_lists: Sequence[Sequence[str]], relevant_sets: Sequence[Sequence[str] | set[str]]
) -> float:
    """MRR across queries. Answers "how far down is the first good hit?"."""
    if len(ranked_lists) != len(relevant_sets):
        raise ValueError(f"{len(ranked_lists)} ranked lists but {len(relevant_sets)} relevant sets")
    if not ranked_lists:
        return 0.0
    return float(
        np.mean([reciprocal_rank(r, s) for r, s in zip(ranked_lists, relevant_sets, strict=True)])
    )


# Input: scores (Sequence[float]), outcomes (Sequence[float]), bins (int)
# Output: float
# Processing: Weighted average gap between predicted score and observed accept rate.
# Changed by: Claude Code
# Date: 2026-10-12
def expected_calibration_error(
    scores: Sequence[float], outcomes: Sequence[float], *, bins: int = 10
) -> float:
    """Weighted average gap between predicted score and observed accept rate.

    Bins the scores, compares each bin's mean score to the share actually
    accepted, and weights by bin size. 0 is perfect; 0.1 means the scores are
    off by about ten percentage points on average.

    Empty bins are skipped rather than counted as perfect, which otherwise
    makes a model look better the more bins you ask for.
    """
    scores_arr = np.asarray(scores, dtype=np.float64).reshape(-1)
    outcomes_arr = np.asarray(outcomes, dtype=np.float64).reshape(-1)
    if scores_arr.shape != outcomes_arr.shape:
        raise ValueError(f"scores and outcomes must align, got {scores_arr.shape} and {outcomes_arr.shape}")
    if scores_arr.size == 0:
        return 0.0
    if bins <= 0:
        raise ValueError(f"bins must be positive, got {bins}")

    edges = np.linspace(0.0, 1.0, bins + 1)
    # Right-closed bins, so a score of exactly 1.0 lands in the last bin
    # rather than falling outside the range.
    indices = np.clip(np.digitize(scores_arr, edges[1:-1], right=True), 0, bins - 1)

    total = 0.0
    for b in range(bins):
        mask = indices == b
        count = int(mask.sum())
        if count == 0:
            continue
        gap = abs(float(outcomes_arr[mask].mean()) - float(scores_arr[mask].mean()))
        total += (count / scores_arr.size) * gap
    return total


# Input: outcomes (Sequence[float])
# Output: float
# Processing: Share of recommendations the user kept. The headline live metric.
# Changed by: Claude Code
# Date: 2026-10-12
def acceptance_rate(outcomes: Sequence[float]) -> float:
    """Share of recommendations the user kept. The headline live metric."""
    if not len(outcomes):
        return 0.0
    return float(np.mean(np.asarray(outcomes, dtype=np.float64)))


# Input: inserted (int), reverted (int)
# Output: float
# Processing: Share of inserts the user deleted or heavily edited soon after.
# Changed by: Claude Code
# Date: 2026-10-12
def reverted_insert_rate(inserted: int, reverted: int) -> float:
    """Share of inserts the user deleted or heavily edited soon after.

    The most honest live signal there is. An entry that was clicked and then
    thrown away was a ranking mistake, and acceptance rate alone counts it as
    a success.
    """
    if inserted <= 0:
        return 0.0
    if reverted > inserted:
        raise ValueError(f"reverted ({reverted}) cannot exceed inserted ({inserted})")
    return reverted / inserted


# -- Per-query evaluation ---------------------------------------------------


@dataclass
class QueryOutcome:
    """One evaluated query: what was retrieved, what was ranked, what was right."""

    query_id: str
    language: str
    retrieved_ids: tuple[str, ...]
    ranked_ids: tuple[str, ...]
    relevance: Mapping[str, float]
    returned_scores: tuple[float, ...] = ()
    accepted: tuple[float, ...] = ()
    category: str | None = None

    # Input: none
    # Output: set[str]
    # Processing: Relevant ids
    # Changed by: Claude Code
    # Date: 2026-10-12
    @property
    def relevant_ids(self) -> set[str]:
        return {entry_id for entry_id, grade in self.relevance.items() if grade > 0}


@dataclass
class MetricSnapshot:
    """One measurement of the whole stack, overall and per language."""

    recall_at_1000: float = 0.0
    ndcg_at_10: float = 0.0
    mrr: float = 0.0
    calibration_error: float = 0.0
    queries: int = 0
    per_language: dict[str, MetricSnapshot] = field(default_factory=dict)

    # Input: none
    # Output: dict[str, object]
    # Processing: As dict
    # Changed by: Claude Code
    # Date: 2026-10-12
    def as_dict(self) -> dict[str, object]:
        return {
            "recall_at_1000": self.recall_at_1000,
            "ndcg_at_10": self.ndcg_at_10,
            "mrr": self.mrr,
            "calibration_error": self.calibration_error,
            "queries": self.queries,
            "per_language": {lang: snap.as_dict() for lang, snap in sorted(self.per_language.items())},
        }


# Input: outcomes (Sequence[QueryOutcome]), recall_k (int), ndcg_k (int), bins (int)
# Output: MetricSnapshot
# Processing: Snapshot
# Changed by: Claude Code
# Date: 2026-10-12
def _snapshot(outcomes: Sequence[QueryOutcome], *, recall_k: int, ndcg_k: int, bins: int) -> MetricSnapshot:
    if not outcomes:
        return MetricSnapshot()

    recalls = [recall_at_k(o.retrieved_ids, o.relevant_ids, recall_k) for o in outcomes]
    ndcgs = [ndcg_at_k(o.ranked_ids, o.relevance, ndcg_k) for o in outcomes]
    scores: list[float] = []
    accepted: list[float] = []
    for o in outcomes:
        if o.returned_scores and len(o.returned_scores) == len(o.accepted):
            scores.extend(o.returned_scores)
            accepted.extend(o.accepted)

    return MetricSnapshot(
        recall_at_1000=float(np.mean(recalls)),
        ndcg_at_10=float(np.mean(ndcgs)),
        mrr=mean_reciprocal_rank([o.ranked_ids for o in outcomes], [o.relevant_ids for o in outcomes]),
        calibration_error=expected_calibration_error(scores, accepted, bins=bins) if scores else 0.0,
        queries=len(outcomes),
    )


# Input: outcomes (Sequence[QueryOutcome])
# Output: dict[str, list[QueryOutcome]]
# Processing: Group outcomes by language, so every metric can be reported per language.
# Changed by: Claude Code
# Date: 2026-10-12
def per_language(outcomes: Sequence[QueryOutcome]) -> dict[str, list[QueryOutcome]]:
    """Group outcomes by language, so every metric can be reported per language."""
    grouped: dict[str, list[QueryOutcome]] = {}
    for outcome in outcomes:
        grouped.setdefault(outcome.language.lower(), []).append(outcome)
    return grouped


# Input: outcomes (Sequence[QueryOutcome]), recall_k (int), ndcg_k (int), calibration_bins (int)
# Output: MetricSnapshot
# Processing: Score a set of queries overall and per language.
# Changed by: Claude Code
# Date: 2026-10-12
def evaluate(
    outcomes: Sequence[QueryOutcome],
    *,
    recall_k: int = 1000,
    ndcg_k: int = 10,
    calibration_bins: int = 10,
) -> MetricSnapshot:
    """Score a set of queries overall and per language."""
    snapshot = _snapshot(outcomes, recall_k=recall_k, ndcg_k=ndcg_k, bins=calibration_bins)
    snapshot.per_language = {
        language: _snapshot(group, recall_k=recall_k, ndcg_k=ndcg_k, bins=calibration_bins)
        for language, group in sorted(per_language(outcomes).items())
    }
    return snapshot
