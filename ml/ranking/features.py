"""Stage 1 features, one row per candidate.

The feature set is fixed by the spec and shared by Stage 1 and Stage 3 (which
sees these plus the cross-encoder score), so the column order lives here and
nowhere else. Both rankers are gradient-boosted trees, so the features are
left on their natural scales -- trees split on order, not magnitude -- except
where the spec asks for a transform that changes what a feature *means*:
log-scaled usage, a co-usage ratio, a smoothed acceptance rate.
"""

from __future__ import annotations

import math
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime

import numpy as np

from ml.config import ACCEPTANCE_SMOOTHING_IMPRESSIONS, CO_USAGE_NEIGHBOURS
from ml.retrieval.filters import classify_dependency_compat
from ml.retrieval.vector import l2_normalize
from ml.schemas import Candidate

FEATURE_NAMES: tuple[str, ...] = (
    "bm25_score",
    "cosine_score",
    "rrf_rank",
    "co_usage_lift",
    "usage_log",
    "acceptance_smoothed",
    "mutation_score",
    "dependency_compat",
    "owner_match",
    "days_since_retest",
)
"""Column order for both LightGBM rankers. Appending is safe; reordering is
not -- a trained model's splits are bound to these positions, so a reorder
silently changes what every split tests. Add at the end and retrain."""

MISSING_RANK = 10_000.0
"""Stand-in rank for a candidate one arm never returned. Finite rather than
NaN so LightGBM treats "not retrieved by this arm" as a worse rank instead of
routing it down its default-direction branch."""


class UsageIndex:
    """Per-project usage history, and the project embeddings that make
    "similar projects" mean something.

    This is what the co-usage feature reads. In production it is a materialised
    view over the usage logs, refreshed on a schedule -- the spec's plan to keep
    raw logs for a fixed window and then aggregate into per-entry counts is
    exactly this table, and it is why storage stops growing without limit.
    """

    # Input: project_embeddings (Mapping[str, np.ndarray] | None), project_usage (Mapping[str, Mapping[str, int]] | None)
    # Output: None
    # Processing: Init
    # Changed by: Claude Code
    # Date: 2026-10-12
    def __init__(
        self,
        *,
        project_embeddings: Mapping[str, np.ndarray] | None = None,
        project_usage: Mapping[str, Mapping[str, int]] | None = None,
    ) -> None:
        self._project_ids: list[str] = []
        self._matrix: np.ndarray | None = None
        self._usage: dict[str, Counter[str]] = {
            pid: Counter(counts) for pid, counts in (project_usage or {}).items()
        }
        if project_embeddings:
            self._project_ids = list(project_embeddings)
            self._matrix = l2_normalize(
                np.stack([np.asarray(project_embeddings[pid], dtype=np.float32) for pid in self._project_ids])
            )

    # Input: none
    # Output: int
    # Processing: Library-wide usage events, the denominator for an entry's base rate.
    # Changed by: Claude Code
    # Date: 2026-10-12
    @property
    def total_usage(self) -> int:
        """Library-wide usage events, the denominator for an entry's base rate."""
        return int(sum(sum(counts.values()) for counts in self._usage.values()))

    # Input: embedding (np.ndarray | None), k (int)
    # Output: list[str]
    # Processing: The k most similar projects by cosine similarity on project embeddings.
    # Changed by: Claude Code
    # Date: 2026-10-12
    def nearest_projects(self, embedding: np.ndarray | None, *, k: int = CO_USAGE_NEIGHBOURS) -> list[str]:
        """The k most similar projects by cosine similarity on project embeddings."""
        if embedding is None or self._matrix is None or not self._project_ids:
            return []
        query = l2_normalize(np.asarray(embedding, dtype=np.float32).reshape(-1))
        sims = self._matrix @ query
        order = np.argsort(-sims, kind="stable")[:k]
        return [self._project_ids[i] for i in order]

    # Input: project_ids (Sequence[str])
    # Output: Counter[str]
    # Processing: How often each entry was used across the given projects.
    # Changed by: Claude Code
    # Date: 2026-10-12
    def co_usage_counts(self, project_ids: Sequence[str]) -> Counter[str]:
        """How often each entry was used across the given projects."""
        totals: Counter[str] = Counter()
        for pid in project_ids:
            totals.update(self._usage.get(pid, {}))
        return totals


@dataclass
class FeatureContext:
    """Everything a feature row needs that is not on the candidate itself."""

    global_acceptance_rate: float = 0.0
    """Library-wide accept rate; low-impression entries are pulled toward it."""

    neighbour_count: int = 0
    co_usage: Mapping[str, int] = field(default_factory=dict)
    total_usage: int = 0
    installed_dependencies: Sequence[str] = ()
    requester_id: str | None = None
    now: datetime | None = None

    # Input: usage_index (UsageIndex), project_embedding (np.ndarray | None), global_acceptance_rate (float), installed_dependencies (Sequence[str]), requester_id (str | None), neighbours (int), now (datetime | None)
    # Output: FeatureContext
    # Processing: Build
    # Changed by: Claude Code
    # Date: 2026-10-12
    @classmethod
    def build(
        cls,
        *,
        usage_index: UsageIndex,
        project_embedding: np.ndarray | None = None,
        global_acceptance_rate: float = 0.0,
        installed_dependencies: Sequence[str] = (),
        requester_id: str | None = None,
        neighbours: int = CO_USAGE_NEIGHBOURS,
        now: datetime | None = None,
    ) -> FeatureContext:
        neighbour_ids = usage_index.nearest_projects(project_embedding, k=neighbours)
        return cls(
            global_acceptance_rate=global_acceptance_rate,
            neighbour_count=len(neighbour_ids),
            co_usage=usage_index.co_usage_counts(neighbour_ids),
            total_usage=usage_index.total_usage,
            installed_dependencies=tuple(installed_dependencies),
            requester_id=requester_id,
            now=now,
        )


# Input: candidate (Candidate), ctx (FeatureContext)
# Output: float
# Processing: Local usage rate divided by the entry's overall usage rate.
# Changed by: Claude Code
# Date: 2026-10-12
def co_usage_lift(candidate: Candidate, ctx: FeatureContext) -> float:
    """Local usage rate divided by the entry's overall usage rate.

    The division is the point. A raw neighbour count would just re-rank by
    popularity, and the library's most-used entry would win every query. The
    ratio asks a different question: is this entry used *more* by projects like
    this one than by projects in general? A value above 1 means yes.

    Returns 0 when there is nothing to compare -- no neighbours, no usage
    history, or an entry nobody has used -- which reads as "no signal" rather
    than as evidence against the entry.
    """
    if ctx.neighbour_count == 0 or ctx.total_usage <= 0:
        return 0.0
    overall_rate = candidate.entry.usage_count / ctx.total_usage
    if overall_rate <= 0.0:
        return 0.0
    local_rate = ctx.co_usage.get(candidate.id, 0) / ctx.neighbour_count
    return local_rate / overall_rate


# Input: candidate (Candidate), ctx (FeatureContext)
# Output: float
# Processing: Acceptance rate pulled toward the global average by a pseudo-count.
# Changed by: Claude Code
# Date: 2026-10-12
def smoothed_acceptance(candidate: Candidate, ctx: FeatureContext) -> float:
    """Acceptance rate pulled toward the global average by a pseudo-count.

    An entry shown twice and accepted twice has a raw rate of 1.0, which would
    outrank an entry accepted 400 times out of 600. The prior fixes that: with
    50 pseudo-impressions, two accepts barely move off the global average,
    while 600 real impressions almost entirely override it.
    """
    entry = candidate.entry
    prior = ACCEPTANCE_SMOOTHING_IMPRESSIONS
    return (entry.accepts + prior * ctx.global_acceptance_rate) / (entry.impressions + prior)


# Input: candidate (Candidate), ctx (FeatureContext)
# Output: list[float]
# Processing: One feature vector, in :data:`FEATURE_NAMES` order.
# Changed by: Claude Code
# Date: 2026-10-12
def feature_row(candidate: Candidate, ctx: FeatureContext) -> list[float]:
    """One feature vector, in :data:`FEATURE_NAMES` order."""
    entry = candidate.entry
    compat = classify_dependency_compat(entry, ctx.installed_dependencies)
    owner_match = bool(ctx.requester_id is not None and entry.owner_id == ctx.requester_id)

    return [
        float(candidate.bm25_score),
        float(candidate.cosine_score),
        float(candidate.rrf_rank if candidate.rrf_rank is not None else MISSING_RANK),
        co_usage_lift(candidate, ctx),
        math.log1p(max(0, entry.usage_count)),
        smoothed_acceptance(candidate, ctx),
        float(entry.mutation_score),
        float(compat.ordinal),
        float(owner_match),
        entry.days_since_retest(ctx.now),
    ]


# Input: candidates (Sequence[Candidate]), ctx (FeatureContext)
# Output: np.ndarray
# Processing: ``(len(candidates), len(FEATURE_NAMES))`` float32 matrix.
# Changed by: Claude Code
# Date: 2026-10-12
def build_feature_matrix(candidates: Sequence[Candidate], ctx: FeatureContext) -> np.ndarray:
    """``(len(candidates), len(FEATURE_NAMES))`` float32 matrix."""
    if not candidates:
        return np.zeros((0, len(FEATURE_NAMES)), dtype=np.float32)
    return np.asarray([feature_row(c, ctx) for c in candidates], dtype=np.float32)
