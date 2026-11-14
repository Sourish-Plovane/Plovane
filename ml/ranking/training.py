"""Assemble Stage 1 training data, fit the ranker, and refuse to oversell it.

The pipeline here is the one the spec describes: generate queries, retrieve
candidates for each, label the entry the query came from as relevant, mine the
highest-ranked wrong candidates as hard negatives, group one query per
lambdarank group, fit, and compare against the untrained hand-weighted ranker
on a held-out split.

What this module adds to that is a refusal. A training run on generated
queries produces excellent numbers for a reason that has nothing to do with
ranking quality: the query is built from the entry's description, the keyword
index holds that same description, so BM25 answers the query by string overlap
and a trained model's best available strategy is to copy BM25. On this library
that scores about 0.99 NDCG@10. The run succeeds, the metric is computed
correctly, and the model has learned nothing transferable.

So :class:`TrainingReport` separates *ran* from *certified*. The fit always
runs -- it is how the pipeline gets exercised, and the artefact is real. But
the report is only certified when the query set is not lexically degenerate,
when enough features actually vary to be learned from, and when the trained
model beats the hand-weighted baseline. Anything short of that is reported as
a diagnostic, and ``certified`` is False, so no release gate can read a
paraphrase-matching score as evidence the ranker works.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from enum import Enum

import numpy as np

from ml.config import HARD_NEGATIVES_PER_GROUP, STAGE1_TOP_K, LightGBMConfig
from ml.evaluation.metrics import ndcg_at_k, recall_at_k, reciprocal_rank
from ml.evaluation.synthetic import (
    OverlapReport,
    SyntheticQuery,
    measure_overlap,
)
from ml.ingest.dataset import feature_support
from ml.ranking.features import FEATURE_NAMES, FeatureContext, build_feature_matrix
from ml.ranking.stage1 import (
    FeatureRanker,
    LightGBMFeatureRanker,
    LinearFeatureRanker,
    TrainingGroup,
)
from ml.schemas import Entry, ProjectContext, Request

RETRIEVAL_FEATURES = frozenset({"bm25_score", "cosine_score", "rrf_rank"})
"""The features that come from retrieval itself and so exist on day one.

Stage 1 is specified as a judgement over four signal groups: retrieval
agreement, usage, trust and friction. Only the first is available before the
library has served anyone -- the rest are derived from usage logs, mutation
testing and a project's installed dependencies.

A model fitted on these three alone is a real and deployable ranker; it is
just a *retrieval-fusion* ranker rather than the full Stage 1. Treating those
two as the same thing is what makes a training report misleading, in either
direction: calling the fit worthless understates a genuine result, and calling
it Stage 1 overstates one. Hence :class:`TrainingScope`."""


SIGNAL_GROUPS: dict[str, frozenset[str]] = {
    "retrieval": frozenset({"bm25_score", "cosine_score", "rrf_rank"}),
    "usage": frozenset({"co_usage_lift", "usage_log", "acceptance_smoothed"}),
    "trust": frozenset({"mutation_score", "days_since_retest"}),
    "friction": frozenset({"dependency_compat", "owner_match"}),
}
"""The four things the spec asks Stage 1 to weigh, and which features carry each.

Scope is reported over these groups rather than over a feature count, because
a count answers the wrong question. Populating ``dependencies`` made
``dependency_compat`` vary, which took the run from three varying features to
four and -- under an earlier definition keyed on "any feature beyond
retrieval" -- flipped the scope to ``full_stage1``. That reading was
generous to the point of being wrong: the feature earned 34 of 7,025 gain,
under 1%, and nothing about usage or trust had changed at all. A reader seeing
``full_stage1`` would infer coverage the run did not have."""

NEGLIGIBLE_GAIN_SHARE = 0.02
"""Gain share below which a varying feature is reported as barely used.

A feature can vary, be split on, and still carry almost none of the model's
decision. Saying it is "trained" without saying that invites the same
over-reading the scope definition did."""


class TrainingScope(str, Enum):
    """What a run is entitled to claim, given which signal groups it saw."""

    RETRIEVAL_FUSION = "retrieval_fusion"
    """Only retrieval evidence varied. The fit learns to fuse keyword and
    vector signals better than the hand-set weights -- which is Stage 1's
    day-one job -- and says nothing about usage, trust or friction."""

    PARTIAL = "partial"
    """Retrieval plus some but not all of usage, trust and friction."""

    FULL_STAGE1 = "full_stage1"
    """Every group the spec names varied, so the fit covers Stage 1 as
    specified."""


@dataclass(frozen=True)
class LabelledGroup:
    """One query's candidates, with labels and the ids behind them.

    :class:`TrainingGroup` carries only features and labels, which is all
    LightGBM needs. The ids are kept alongside here so a failure can be read
    back -- "which entry did the ranker put above the right one" is the first
    question after a bad metric, and it is unanswerable from a feature matrix.
    """

    query: str
    query_id: str
    target_id: str
    language: str
    candidate_ids: tuple[str, ...]
    features: np.ndarray
    labels: np.ndarray

    # Input: none
    # Output: TrainingGroup
    # Processing: Narrow to what LightGBM consumes
    # Changed by: Sourish
    # Date: 2026-10-12
    def as_training_group(self) -> TrainingGroup:
        return TrainingGroup(features=self.features, labels=self.labels, query_id=self.query_id)


# Input: query_id (str), fractions (tuple[float, float, float])
# Output: str
# Processing: Deterministically assign a key to train, valid or test
# Changed by: Sourish
# Date: 2026-10-12
def _assign_split(key: str, fractions: tuple[float, float, float]) -> str:
    """Hash a key into one of three splits. Stable across runs and machines.

    Hashing rather than shuffling means a given entry lands in the same split
    every run without a seed having to be threaded through, so two training
    runs are comparable.
    """
    digest = hashlib.blake2b(key.encode(), digest_size=8).hexdigest()
    position = int(digest, 16) / float(1 << 64)
    train, valid, _ = fractions
    if position < train:
        return "train"
    if position < train + valid:
        return "valid"
    return "test"


# Input: queries (Sequence[SyntheticQuery]), fractions (tuple[float, float, float])
# Output: dict[str, list[SyntheticQuery]]
# Processing: Split queries into train/valid/test by source entry
# Changed by: Sourish
# Date: 2026-10-12
def split_by_entry(
    queries: Sequence[SyntheticQuery],
    *,
    fractions: tuple[float, float, float] = (0.7, 0.15, 0.15),
) -> dict[str, list[SyntheticQuery]]:
    """Split on the *source entry*, never on the query.

    Three queries generated from one entry are near-paraphrases of each other.
    Splitting per query would put two of them in train and the third in test,
    and the test score would then measure whether the model remembers an entry
    it was trained on -- which it does, perfectly. Splitting on the entry is
    what makes the held-out split held out.
    """
    if abs(sum(fractions) - 1.0) > 1e-6:
        raise ValueError(f"split fractions must sum to 1, got {fractions}")
    out: dict[str, list[SyntheticQuery]] = {"train": [], "valid": [], "test": []}
    for query in queries:
        out[_assign_split(query.entry_id, fractions)].append(query)
    return out


# Input: service (object), query (SyntheticQuery), ctx (FeatureContext), negatives (int), query_id (str)
# Output: LabelledGroup | None
# Processing: Retrieve for one query and label the candidates
# Changed by: Sourish
# Date: 2026-10-12
def assemble_group(
    service,
    query: SyntheticQuery,
    ctx: FeatureContext,
    *,
    negatives: int | None = None,
    query_id: str | None = None,
) -> LabelledGroup | None:
    """Build one lambdarank group: the target plus the candidates it is ranked against.

    Returns ``None`` when retrieval misses the target entirely. Such a query
    cannot teach a ranker anything -- the right answer is not among the rows
    it would score -- and keeping it as an all-negative group would push the
    model to drive every score down.

    ``negatives=None``, the default, keeps **every** retrieved candidate, so a
    training group is shaped exactly like a serving request. That default
    exists because the obvious alternative is a trap, and this code shipped
    with the trap in it: taking only the 15 hardest negatives produced a model
    that scored NDCG@10 0.7736 in cross-validation and **0.0066** when asked
    to rank a real retrieved set, against 0.3428 for the hand-weighted
    baseline it was supposed to replace.

    The mechanism is distribution mismatch. The hardest 15 negatives are all
    candidates retrieval already liked -- high BM25, high cosine. The model
    never saw the long tail of weak candidates, so at serving time, where that
    tail is most of the 200-odd rows, it has no idea what to do with them and
    effectively randomises. Measuring on the same 16-row groups it trained on
    hid this completely: the evaluation was of a task nobody performs.

    Measured on this library, ranking the full retrieved set:

    ======================  =========
    negatives per group     NDCG@10
    ======================  =========
    hand-weighted baseline     0.3428
    15 hardest                 0.0066
    15 hardest + 15 random     0.4204
    15 hardest + 60 random     0.4892
    all (this default)         0.5337
    ======================  =========

    Passing an integer truncates to that many hardest negatives. It is kept
    for the sampling experiment above and for a library too large to train on
    whole, and :attr:`TrainingReport.caveats` reports the truncation, because
    a run that trains on a shape it will never serve cannot be read as a
    measurement of serving quality.
    """
    request = Request(
        query=query.query,
        project_context=ProjectContext(languages=(query.language,)),
    )
    candidates = service.retrieve(request).candidates
    target = next((c for c in candidates if c.id == query.entry_id), None)
    if target is None:
        return None

    rivals = [c for c in candidates if c.id != query.entry_id]
    if negatives is not None:
        rivals = rivals[:negatives]
    chosen = [target, *rivals]
    labels = np.array([1.0] + [0.0] * len(rivals), dtype=np.float32)
    return LabelledGroup(
        query=query.query,
        query_id=query_id or f"{query.entry_id}::{abs(hash(query.query)) % 10**8}",
        target_id=query.entry_id,
        language=query.language,
        candidate_ids=tuple(c.id for c in chosen),
        features=build_feature_matrix(chosen, ctx),
        labels=labels,
    )


# Input: service (object), queries (Sequence[SyntheticQuery]), ctx (FeatureContext), negatives (int)
# Output: list[LabelledGroup]
# Processing: Assemble a labelled group per query, dropping retrieval misses
# Changed by: Sourish
# Date: 2026-10-12
def assemble_groups(
    service,
    queries: Sequence[SyntheticQuery],
    ctx: FeatureContext,
    *,
    negatives: int | None = None,
) -> list[LabelledGroup]:
    """Assemble every query that retrieval can actually serve."""
    groups: list[LabelledGroup] = []
    for index, query in enumerate(queries):
        group = assemble_group(
            service, query, ctx, negatives=negatives, query_id=f"q{index:06d}"
        )
        if group is not None:
            groups.append(group)
    return groups


# Input: ranker (FeatureRanker), groups (Sequence[LabelledGroup]), ndcg_k (int), recall_k (int)
# Output: dict[str, float]
# Processing: Score each group and average the ranking metrics
# Changed by: Sourish
# Date: 2026-10-12
def evaluate_ranker(
    ranker: FeatureRanker,
    groups: Sequence[LabelledGroup],
    *,
    ndcg_k: int = 10,
    recall_k: int = 3,
) -> dict[str, float]:
    """NDCG@k, MRR and recall@k averaged over groups.

    Positions stand in for ids, so these reuse the same metric functions the
    evaluation harness uses rather than a second implementation that could
    drift from it.
    """
    if not groups:
        return {"ndcg": 0.0, "mrr": 0.0, "recall": 0.0, "groups": 0.0}

    ndcgs: list[float] = []
    rrs: list[float] = []
    recalls: list[float] = []
    for group in groups:
        scores = ranker.score(group.features)
        order = np.argsort(-np.asarray(scores), kind="stable")
        ranked = [str(int(i)) for i in order]
        relevance = {str(i): float(group.labels[i]) for i in range(len(group.labels))}
        relevant = {key for key, value in relevance.items() if value > 0}
        ndcgs.append(ndcg_at_k(ranked, relevance, k=ndcg_k))
        rrs.append(reciprocal_rank(ranked, relevant))
        recalls.append(recall_at_k(ranked, relevant, k=recall_k))

    return {
        "ndcg": float(np.mean(ndcgs)),
        "mrr": float(np.mean(rrs)),
        "recall": float(np.mean(recalls)),
        "groups": float(len(groups)),
    }


@dataclass(frozen=True)
class CrossValidationResult:
    """Per-fold baseline and trained scores, and the spread across folds.

    A single held-out split on a few hundred groups gives a number with no
    error bar, and the first thing anyone will do with it is compare it to the
    baseline. On this dataset one split read +0.29 against a cross-validated
    +0.24, so the single split flattered the model by a fifth of its own
    improvement. Reporting the spread is what makes the figure usable.
    """

    baseline: tuple[float, ...]
    trained: tuple[float, ...]
    metric: str = "ndcg"

    # Input: none
    # Output: float
    # Processing: Mean improvement over the baseline across folds
    # Changed by: Sourish
    # Date: 2026-10-12
    @property
    def mean_delta(self) -> float:
        return float(np.mean(np.asarray(self.trained) - np.asarray(self.baseline)))

    # Input: none
    # Output: float
    # Processing: Standard deviation of the per-fold improvement
    # Changed by: Sourish
    # Date: 2026-10-12
    @property
    def delta_std(self) -> float:
        return float(np.std(np.asarray(self.trained) - np.asarray(self.baseline)))

    # Input: none
    # Output: int
    # Processing: How many folds the trained model won
    # Changed by: Sourish
    # Date: 2026-10-12
    @property
    def folds_won(self) -> int:
        return int((np.asarray(self.trained) > np.asarray(self.baseline)).sum())

    # Input: none
    # Output: bool
    # Processing: Whether the model beat the baseline on every fold
    # Changed by: Sourish
    # Date: 2026-10-12
    @property
    def consistent(self) -> bool:
        """True when the trained model wins every fold.

        A mean improvement with a losing fold in it is a different claim from
        one without, and the difference matters more than the mean: a model
        that wins four folds and loses one has found something split-specific.
        """
        return self.folds_won == len(self.trained)

    # Input: none
    # Output: str
    # Processing: One-line summary of the cross-validated comparison
    # Changed by: Sourish
    # Date: 2026-10-12
    def summary(self) -> str:
        base, trained = np.asarray(self.baseline), np.asarray(self.trained)
        return (
            f"{len(trained)}-fold {self.metric}: baseline {base.mean():.4f}+/-{base.std():.4f}, "
            f"trained {trained.mean():.4f}+/-{trained.std():.4f}, "
            f"delta {self.mean_delta:+.4f}+/-{self.delta_std:.4f}, "
            f"won {self.folds_won}/{len(trained)}"
        )


# Input: groups (Sequence[LabelledGroup]), folds (int), config (LightGBMConfig | None)
# Output: CrossValidationResult
# Processing: Cross-validate the trained ranker against the baseline by entry
# Changed by: Sourish
# Date: 2026-10-12
def cross_validate(
    groups: Sequence[LabelledGroup],
    *,
    folds: int = 5,
    config: LightGBMConfig | None = None,
) -> CrossValidationResult:
    """Fit and score ``folds`` times, holding out a different fifth each time.

    Folds are assigned by **target entry**, for the same reason the train/test
    split is: queries about one entry are near-paraphrases, and splitting them
    across the fold boundary measures memorisation instead of generalisation.
    """
    if folds < 2:
        raise ValueError(f"need at least 2 folds, got {folds}")

    # Input: entry_id (str)
    # Output: int
    # Processing: Assign an entry to a fold, stably
    # Changed by: Sourish
    # Date: 2026-10-12
    def fold_of(entry_id: str) -> int:
        return int(hashlib.blake2b(entry_id.encode(), digest_size=8).hexdigest(), 16) % folds

    baseline_scores: list[float] = []
    trained_scores: list[float] = []
    baseline_ranker = LinearFeatureRanker()
    for fold in range(folds):
        test = [g for g in groups if fold_of(g.target_id) == fold]
        train = [g for g in groups if fold_of(g.target_id) != fold]
        if not test or not train:
            continue
        ranker = LightGBMFeatureRanker(config).fit([g.as_training_group() for g in train])
        baseline_scores.append(evaluate_ranker(baseline_ranker, test)["ndcg"])
        trained_scores.append(evaluate_ranker(ranker, test)["ndcg"])

    if not trained_scores:
        raise ValueError("no fold produced both a train and a test split")
    return CrossValidationResult(tuple(baseline_scores), tuple(trained_scores))


@dataclass(frozen=True)
class TrainingReport:
    """What a training run produced, and whether it may be believed.

    ``certified`` is the field that matters. A run can complete, fit a real
    model, and report a near-perfect metric while establishing nothing; see
    the module docstring. Every reason a run is not certifiable is listed in
    ``blockers`` so the summary says *why* rather than only that.
    """

    overlap: OverlapReport
    varying_features: Mapping[str, bool]
    informative_features: tuple[str, ...]
    split_sizes: Mapping[str, int]
    baseline: Mapping[str, float]
    trained: Mapping[str, float]
    importance: Mapping[str, float] = field(default_factory=dict)
    model_path: str | None = None
    cross_validation: CrossValidationResult | None = None
    negatives_per_group: int | None = None
    """``None`` when groups held the full retrieved set, as serving does."""
    median_group_size: int = 0

    # Input: none
    # Output: tuple[str, ...]
    # Processing: Which of the spec's signal groups had a feature that varied
    # Changed by: Sourish
    # Date: 2026-10-12
    @property
    def covered_groups(self) -> tuple[str, ...]:
        """Signal groups with at least one varying feature, in spec order."""
        varying = set(self.informative_features)
        return tuple(
            group for group, features in SIGNAL_GROUPS.items() if features & varying
        )

    # Input: none
    # Output: tuple[str, ...]
    # Processing: Signal groups with no varying feature at all
    # Changed by: Sourish
    # Date: 2026-10-12
    @property
    def uncovered_groups(self) -> tuple[str, ...]:
        covered = set(self.covered_groups)
        return tuple(group for group in SIGNAL_GROUPS if group not in covered)

    # Input: none
    # Output: tuple[str, ...]
    # Processing: Features that varied but carry almost none of the decision
    # Changed by: Sourish
    # Date: 2026-10-12
    @property
    def barely_used_features(self) -> tuple[str, ...]:
        """Varying features below :data:`NEGLIGIBLE_GAIN_SHARE` of total gain."""
        total = sum(self.importance.values())
        if total <= 0:
            return ()
        return tuple(
            name
            for name in self.informative_features
            if self.importance.get(name, 0.0) / total < NEGLIGIBLE_GAIN_SHARE
        )

    # Input: none
    # Output: TrainingScope
    # Processing: What this run is entitled to claim
    # Changed by: Sourish
    # Date: 2026-10-12
    @property
    def scope(self) -> TrainingScope:
        """Scope over the spec's four signal groups, not over a feature count."""
        covered = set(self.covered_groups)
        if covered == set(SIGNAL_GROUPS):
            return TrainingScope.FULL_STAGE1
        if covered - {"retrieval"}:
            return TrainingScope.PARTIAL
        return TrainingScope.RETRIEVAL_FUSION

    # Input: none
    # Output: list[str]
    # Processing: Reasons the run is invalid even at its own scope
    # Changed by: Sourish
    # Date: 2026-10-12
    @property
    def blockers(self) -> list[str]:
        """Reasons this run cannot be believed *at its own scope*.

        Narrower than it was. An earlier version blocked any run where fewer
        than six features varied, which conflated two different things: a run
        that measures nothing, and a run that measures something real but
        narrow. A three-feature fit on honest queries is the second. The
        feature gap is now a caveat on the claim rather than a refusal of it,
        and :attr:`scope` carries what the claim covers.
        """
        reasons: list[str] = []
        if self.overlap.degenerate:
            reasons.append(
                f"query set is lexically degenerate (mean overlap "
                f"{self.overlap.mean:.2f} >= {self.overlap.threshold:.2f}): the "
                f"queries restate the descriptions BM25 indexes, so a high score "
                f"measures paraphrase matching"
            )
        if not self.informative_features:
            reasons.append("no feature varies across candidates; there is nothing to learn")
        if self.trained.get("ndcg", 0.0) <= self.baseline.get("ndcg", 0.0):
            reasons.append(
                f"trained NDCG@10 {self.trained.get('ndcg', 0.0):.4f} does not beat "
                f"the hand-weighted baseline {self.baseline.get('ndcg', 0.0):.4f}"
            )
        if self.negatives_per_group is not None:
            reasons.append(
                f"groups were truncated to {self.negatives_per_group} hardest negatives, so "
                f"the model trained and was scored on a shape it will never serve; the same "
                f"setup once read 0.7736 in cross-validation and 0.0066 on a real retrieved set"
            )
        if self.cross_validation is not None and not self.cross_validation.consistent:
            cv = self.cross_validation
            reasons.append(
                f"trained model lost {len(cv.trained) - cv.folds_won} of {len(cv.trained)} "
                f"folds, so the improvement is split-specific rather than real"
            )
        return reasons

    # Input: none
    # Output: list[str]
    # Processing: True limits that narrow the claim without invalidating it
    # Changed by: Sourish
    # Date: 2026-10-12
    @property
    def caveats(self) -> list[str]:
        """Limits a reader must carry even when the run is certified.

        These are not blockers: each one narrows what the result covers rather
        than making it false. They are listed every time, because a number
        quoted without them will be read as something it is not.
        """
        notes: list[str] = []
        untrained = [n for n in FEATURE_NAMES if n not in self.informative_features]
        if untrained:
            notes.append(
                f"{len(untrained)} of {len(FEATURE_NAMES)} features never varied and are "
                f"therefore untrained -- the model has learned nothing about them: "
                f"{', '.join(untrained)}"
            )
        if self.uncovered_groups:
            notes.append(
                f"no feature varied for: {', '.join(self.uncovered_groups)} -- those "
                f"signals need live traffic before they can be weighed, so the run "
                f"covers {', '.join(self.covered_groups)} only"
            )
        barely = self.barely_used_features
        if barely:
            notes.append(
                f"varied but carries under {NEGLIGIBLE_GAIN_SHARE:.0%} of the model's "
                f"gain, so counting it as trained overstates it: {', '.join(barely)}"
            )
        notes.append(
            "queries were written by a party that had read the entries, so the held-out "
            "score measures generalisation across entries, not real demand -- it is "
            "evidence, not a release gate"
        )
        return notes

    # Input: none
    # Output: bool
    # Processing: Whether this run validates the ranker at its scope
    # Changed by: Sourish
    # Date: 2026-10-12
    @property
    def certified(self) -> bool:
        """True when nothing blocks reading this run as valid at its scope."""
        return not self.blockers

    # Input: none
    # Output: str
    # Processing: Human-readable training report
    # Changed by: Sourish
    # Date: 2026-10-12
    def summary(self) -> str:
        lines = [
            "Stage 1 training run",
            "=" * 72,
            f"queries        {self.overlap.summary()}",
            f"features       {len(self.informative_features)}/{len(FEATURE_NAMES)} vary: "
            f"{', '.join(self.informative_features)}",
            "split          " + ", ".join(f"{k}={v}" for k, v in self.split_sizes.items()),
            f"groups         median {self.median_group_size} candidates"
            + ("" if self.negatives_per_group is None else f", TRUNCATED to {self.negatives_per_group}"),
            f"scope          {self.scope.value} "
            f"(groups: {', '.join(self.covered_groups) or 'none'})",
            "",
            f"{'metric':<12} {'baseline':>10} {'trained':>10} {'delta':>10}",
        ]
        for name in ("ndcg", "mrr", "recall"):
            base = self.baseline.get(name, 0.0)
            trained = self.trained.get(name, 0.0)
            lines.append(f"{name:<12} {base:>10.4f} {trained:>10.4f} {trained - base:>+10.4f}")
        if self.cross_validation is not None:
            lines += ["", f"cross-val      {self.cross_validation.summary()}"]
        if self.importance:
            ranked = sorted(self.importance.items(), key=lambda kv: -kv[1])
            top = ", ".join(f"{k} {v:.0f}" for k, v in ranked if v > 0) or "none"
            lines += ["", f"gain           {top}"]
        lines += ["", f"CERTIFIED      {self.certified}  (scope: {self.scope.value})"]
        for reason in self.blockers:
            lines.append(f"  blocker      {reason}")
        for note in self.caveats:
            lines.append(f"  caveat       {note}")
        if self.model_path:
            lines.append(f"artefact       {self.model_path}")
        return "\n".join(lines)


# Input: service (object), entries (Sequence[Entry]), queries (Sequence[SyntheticQuery]), ctx (FeatureContext), config (LightGBMConfig | None), model_path (str | None), cv_folds (int)
# Output: TrainingReport
# Processing: Run the full Stage 1 training pipeline and report honestly
# Changed by: Sourish
# Date: 2026-10-12
def run_stage1_training(
    service,
    entries: Sequence[Entry],
    queries: Sequence[SyntheticQuery],
    ctx: FeatureContext | None = None,
    *,
    config: LightGBMConfig | None = None,
    model_path: str | None = None,
    cv_folds: int = 5,
    negatives: int | None = None,
) -> TrainingReport:
    """Train Stage 1 end to end and return a report that states its own limits.

    The fit runs whatever the diagnostics say. An uncertifiable run is still
    worth completing: it exercises the pipeline, and a report that says "ran,
    not certified, here is why" is more useful than a refusal that leaves the
    machinery untested.
    """
    from ml.ranking.features import UsageIndex

    ctx = ctx or FeatureContext.build(usage_index=UsageIndex())
    overlap = measure_overlap(queries, {e.id: e.description for e in entries})
    varying = feature_support(entries)

    splits = split_by_entry(queries)
    assembled = {
        name: assemble_groups(service, qs, ctx, negatives=negatives)
        for name, qs in splits.items()
    }

    # Which columns actually move once the features are built. This is the
    # honest version of the question -- a field can vary across entries and
    # still be constant within every group, which is just as unlearnable.
    stacked = np.concatenate(
        [g.features for groups in assembled.values() for g in groups], axis=0
    )
    informative = tuple(
        name
        for index, name in enumerate(FEATURE_NAMES)
        if stacked.size and len(np.unique(stacked[:, index])) > 1
    )

    baseline_ranker = LinearFeatureRanker()
    ranker = LightGBMFeatureRanker(config)
    ranker.fit(
        [g.as_training_group() for g in assembled["train"]],
        validation=[g.as_training_group() for g in assembled["valid"]] or None,
    )
    if model_path:
        ranker.save(model_path)

    # Cross-validate over every group, not just the training split: a single
    # held-out split on a few hundred groups has no error bar, and on this
    # dataset it read a fifth higher than the cross-validated figure.
    all_groups = [g for groups in assembled.values() for g in groups]
    cv = None
    if cv_folds >= 2 and len(all_groups) >= cv_folds:
        cv = cross_validate(all_groups, folds=cv_folds, config=config)

    sizes = sorted(int(g.features.shape[0]) for g in all_groups) or [0]
    return TrainingReport(
        negatives_per_group=negatives,
        median_group_size=sizes[len(sizes) // 2],
        overlap=overlap,
        varying_features=varying,
        informative_features=informative,
        split_sizes={name: len(groups) for name, groups in assembled.items()},
        baseline=evaluate_ranker(baseline_ranker, assembled["test"]),
        trained=evaluate_ranker(ranker, assembled["test"]),
        importance=ranker.feature_importance(),
        model_path=model_path,
        cross_validation=cv,
    )


__all__ = [
    "NEGLIGIBLE_GAIN_SHARE",
    "RETRIEVAL_FEATURES",
    "SIGNAL_GROUPS",
    "CrossValidationResult",
    "cross_validate",
    "TrainingScope",
    "LabelledGroup",
    "TrainingReport",
    "assemble_group",
    "assemble_groups",
    "evaluate_ranker",
    "run_stage1_training",
    "split_by_entry",
]


# Input: argv (Sequence[str] | None)
# Output: int
# Processing: CLI -- build the library, generate queries, train, print the report
# Changed by: Sourish
# Date: 2026-10-12
def main(argv: Sequence[str] | None = None) -> int:
    """Run a Stage 1 training pass over a harvested dataset.

    Exits non-zero when the run is not certified, so this can gate a pipeline
    without the caller having to parse the report.
    """
    import argparse
    from pathlib import Path

    from ml.evaluation.synthetic import generate_training_queries
    from ml.ingest.dataset import DEFAULT_DATASET, DEFAULT_QUERIES, load_dataset, load_queries
    from ml.retrieval.embedding import HashingEmbeddingModel, embed_entries_inplace
    from ml.retrieval.keyword import InMemoryBM25Index
    from ml.retrieval.service import RetrievalService
    from ml.retrieval.vector import InMemoryVectorIndex

    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dataset", default=str(DEFAULT_DATASET), help="harvested entries.jsonl")
    parser.add_argument(
        "--queries",
        default=str(DEFAULT_QUERIES),
        help="hand-written queries; falls back to the templated generator when absent",
    )
    parser.add_argument("--per-entry", type=int, default=3, help="queries generated per entry")
    parser.add_argument(
        "--negatives",
        type=int,
        default=None,
        help="truncate groups to this many hardest negatives; omit to keep the full "
        "retrieved set, which is what serving ranks",
    )
    parser.add_argument("--out", default=None, help="where to save the fitted booster")
    parser.add_argument(
        "--cv-folds", type=int, default=5, help="cross-validation folds; 0 or 1 skips it"
    )
    parser.add_argument(
        "--languages", nargs="*", default=None, help="restrict the library to these languages"
    )
    args = parser.parse_args(argv)

    dataset = load_dataset(args.dataset, languages=args.languages)
    entries = list(dataset.entries)
    if not entries:
        parser.error(f"no usable entries in {args.dataset}")

    model = HashingEmbeddingModel()
    embed_entries_inplace(model, entries)
    service = RetrievalService(
        keyword_index=InMemoryBM25Index(entries),
        vector_index=InMemoryVectorIndex(entries),
        embedding_model=model,
        entry_store={entry.id: entry for entry in entries},
    )

    # Hand-written queries when they exist, because a generated one restates
    # the description the keyword index holds and so cannot pose a ranking
    # problem. The templated generator is the fallback, and the report will
    # refuse to certify a run that used it -- see TrainingReport.blockers.
    query_path = Path(args.queries)
    if query_path.exists():
        queries = load_queries(query_path, entry_ids={entry.id for entry in entries})
        print(f"loaded {len(queries)} hand-written queries from {query_path}")
    else:
        # Only entries with an author's own description generate queries; see
        # ml.ingest.dataset for why a derived description cannot be a source.
        queries = generate_training_queries(dataset.authored, per_entry=args.per_entry)
        print(f"no {query_path}; generated {len(queries)} templated queries instead")
    if not queries:
        parser.error("no queries to train on")
    report = run_stage1_training(
        service,
        entries,
        queries,
        model_path=args.out,
        cv_folds=args.cv_folds,
        negatives=args.negatives,
    )
    print(report.summary())
    return 0 if report.certified else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
