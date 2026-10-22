"""Train Stage 3 and fit its calibrator, and say what neither can establish.

Stage 3 is specified as a second lambdarank model over Stage 1's features
*plus* the cross-encoder score, with isotonic calibration on a held-out split
so the relevance cutoff means something. This module is the harness for both.

Two things about it are deliberately blunt.

**Stage 3 without Stage 2 is Stage 1 with a constant column.** The whole point
of a second tree model is that it sees the text score alongside the facts the
cross-encoder cannot know -- a pinned-version conflict, a stale retest, a
co-usage lift. Remove the text score and the feature set is Stage 1's with
``cross_encoder_score`` fixed at 0, which cannot be split on. A fit still runs
and still produces a real booster; it is simply not the model the spec
describes, and :class:`Stage3Report` says so rather than letting a reader infer
otherwise from a healthy-looking metric.

**Calibration needs outcomes nobody has yet.** Isotonic regression maps a
score to an acceptance probability, and it learns that map from pairs of
``(score, did the user keep it)``. The library has served nobody, so there are
no outcomes -- and a calibrator fitted on invented ones would produce a cutoff
that looks principled and means nothing, which is worse than the uncalibrated
sigmoid the serving path already falls back to. So
:func:`measure_calibration` reports Expected Calibration Error on whatever it
is given and refuses to certify a fit whose outcomes did not come from real
traffic.

What *can* be established here is that the calibrator is correct: fed scores
with a known true accept rate, it should recover that rate. That is a property
of the implementation rather than of the product, and it is tested as one.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

import numpy as np

from ml.config import LightGBMConfig
from ml.evaluation.metrics import expected_calibration_error
from ml.evaluation.synthetic import OverlapReport, SyntheticQuery, measure_overlap
from ml.ranking.calibration import IsotonicCalibrator
from ml.ranking.features import FeatureContext
from ml.ranking.stage1 import TrainingGroup
from ml.ranking.stage3 import (
    STAGE3_FEATURE_NAMES,
    LightGBMFinalRanker,
    LinearFinalRanker,
    build_stage3_matrix,
)
from ml.ranking.training import (
    LabelledGroup,
    assemble_groups,
    evaluate_ranker,
    split_by_entry,
)
from ml.schemas import Entry

CROSS_ENCODER_FEATURE = "cross_encoder_score"

MIN_CALIBRATION_OBSERVATIONS = 200
"""Below this, an isotonic fit is memorising rather than calibrating.

Isotonic regression has no parameters to regularise: it will fit any monotone
shape, including one that runs through every point of a small sample. The floor
is a judgement, not a measurement, and it is deliberately on the generous side
because the failure it guards against produces confident-looking probabilities
rather than an error."""

TRUSTED_OUTCOME_SOURCES = frozenset({"usage", "live", "traffic"})
"""Outcome provenances a calibrator may be certified on.

Anything else -- above all anything carrying ``synthetic`` -- is a fit on
invented accept decisions. A cutoff placed on that is arbitrary while looking
principled, which is the failure mode the whole certification apparatus
exists to catch."""


@dataclass(frozen=True)
class CalibrationReport:
    """How well a fitted calibrator's scores match observed accept rates."""

    observations: int
    distinct_scores: int
    accept_rate: float
    ece: float
    outcome_source: str
    bins: int = 10

    # Input: none
    # Output: bool
    # Processing: Whether the outcomes came from real traffic
    # Changed by: Sourish
    # Date: 2026-10-12
    @property
    def outcomes_are_real(self) -> bool:
        source = self.outcome_source.lower()
        if "synthetic" in source:
            return False
        return any(token in source for token in TRUSTED_OUTCOME_SOURCES)

    # Input: none
    # Output: list[str]
    # Processing: Reasons this calibration cannot be trusted as a cutoff basis
    # Changed by: Sourish
    # Date: 2026-10-12
    @property
    def blockers(self) -> list[str]:
        reasons: list[str] = []
        if not self.outcomes_are_real:
            reasons.append(
                f"outcomes came from {self.outcome_source!r}, not real traffic: a cutoff "
                f"fitted on invented accept decisions is arbitrary while looking principled"
            )
        if self.observations < MIN_CALIBRATION_OBSERVATIONS:
            reasons.append(
                f"{self.observations} observations is below the floor of "
                f"{MIN_CALIBRATION_OBSERVATIONS}; isotonic regression has nothing to "
                f"regularise and will run a step through every point"
            )
        if self.distinct_scores < self.bins:
            reasons.append(
                f"only {self.distinct_scores} distinct scores across {self.bins} bins, so "
                f"the curve's shape is not measurable here"
            )
        if self.accept_rate <= 0.0 or self.accept_rate >= 1.0:
            reasons.append(
                f"every outcome is the same class (accept rate {self.accept_rate:.2f}); "
                f"a calibrator cannot learn a map from one label"
            )
        return reasons

    # Input: none
    # Output: bool
    # Processing: Whether the cutoff may be based on this calibration
    # Changed by: Sourish
    # Date: 2026-10-12
    @property
    def certified(self) -> bool:
        return not self.blockers

    # Input: none
    # Output: str
    # Processing: One-line calibration summary
    # Changed by: Sourish
    # Date: 2026-10-12
    def summary(self) -> str:
        return (
            f"{self.observations} outcomes from {self.outcome_source!r}, "
            f"{self.distinct_scores} distinct scores, accept rate {self.accept_rate:.3f}, "
            f"ECE {self.ece:.4f} over {self.bins} bins"
        )


# Input: scores (Sequence[float]), accepted (Sequence[float]), outcome_source (str), bins (int)
# Output: tuple[IsotonicCalibrator, CalibrationReport]
# Processing: Fit a calibrator and measure how far its probabilities are off
# Changed by: Sourish
# Date: 2026-10-12
def fit_and_measure_calibration(
    scores: Sequence[float],
    accepted: Sequence[float],
    *,
    outcome_source: str,
    bins: int = 10,
) -> tuple[IsotonicCalibrator, CalibrationReport]:
    """Fit on the first half, measure Expected Calibration Error on the second.

    The split is what makes the number mean anything. Isotonic regression fits
    any monotone shape, so ECE measured on the data it was fitted to is close
    to zero by construction and says nothing about a future request.
    """
    scores = np.asarray(scores, dtype=np.float64).reshape(-1)
    accepted = np.asarray(accepted, dtype=np.float64).reshape(-1)
    if scores.shape != accepted.shape:
        raise ValueError(f"scores and outcomes must align, got {scores.shape} and {accepted.shape}")
    if scores.size == 0:
        raise ValueError("nothing to calibrate")

    order = np.argsort(scores, kind="stable")
    scores, accepted = scores[order], accepted[order]
    # Interleave rather than cut in half: sorted by score, a straight split
    # would fit on the low half and measure on the high half, where the
    # fitted curve has never been.
    fit_mask = np.zeros(scores.size, dtype=bool)
    fit_mask[::2] = True
    held = ~fit_mask
    if not held.any():
        fit_mask[:] = True
        held = fit_mask

    calibrator = IsotonicCalibrator().fit(scores[fit_mask], accepted[fit_mask])
    predicted = calibrator.predict(scores[held])
    report = CalibrationReport(
        observations=int(scores.size),
        distinct_scores=int(np.unique(scores).size),
        accept_rate=float(accepted.mean()),
        ece=float(expected_calibration_error(predicted, accepted[held], bins=bins)),
        outcome_source=outcome_source,
        bins=bins,
    )
    return calibrator, report


@dataclass(frozen=True)
class Stage3Report:
    """What a Stage 3 run produced, and whether it is the specified model."""

    overlap: OverlapReport
    informative_features: tuple[str, ...]
    split_sizes: Mapping[str, int]
    baseline: Mapping[str, float]
    trained: Mapping[str, float]
    importance: Mapping[str, float] = field(default_factory=dict)
    calibration: CalibrationReport | None = None
    model_path: str | None = None

    # Input: none
    # Output: bool
    # Processing: Whether the cross-encoder column carried any signal
    # Changed by: Sourish
    # Date: 2026-10-12
    @property
    def has_cross_encoder(self) -> bool:
        """True when the text score varied, i.e. Stage 2 actually ran."""
        return CROSS_ENCODER_FEATURE in self.informative_features

    # Input: none
    # Output: list[str]
    # Processing: Reasons this is not Stage 3 as specified
    # Changed by: Sourish
    # Date: 2026-10-12
    @property
    def blockers(self) -> list[str]:
        reasons: list[str] = []
        if self.overlap.degenerate:
            reasons.append(
                f"query set is lexically degenerate (mean overlap {self.overlap.mean:.2f}): a "
                f"high score measures paraphrase matching"
            )
        if not self.has_cross_encoder:
            reasons.append(
                "cross_encoder_score never varied, so this is Stage 1's feature set with a "
                "constant column -- the text signal that makes Stage 3 a distinct stage is "
                "absent, and no fit here can stand in for it"
            )
        if self.trained.get("ndcg", 0.0) <= self.baseline.get("ndcg", 0.0):
            reasons.append(
                f"trained NDCG@10 {self.trained.get('ndcg', 0.0):.4f} does not beat the "
                f"hand-weighted baseline {self.baseline.get('ndcg', 0.0):.4f}"
            )
        if self.calibration is not None and not self.calibration.certified:
            reasons.extend(f"calibration: {reason}" for reason in self.calibration.blockers)
        elif self.calibration is None:
            reasons.append(
                "no calibration was fitted, so the relevance cutoff has no meaning and the "
                "serving path falls back to an uncalibrated sigmoid"
            )
        return reasons

    # Input: none
    # Output: bool
    # Processing: Whether this run validates Stage 3 as specified
    # Changed by: Sourish
    # Date: 2026-10-12
    @property
    def certified(self) -> bool:
        return not self.blockers

    # Input: none
    # Output: str
    # Processing: Human-readable Stage 3 report
    # Changed by: Sourish
    # Date: 2026-10-12
    def summary(self) -> str:
        lines = [
            "Stage 3 training run",
            "=" * 72,
            f"queries        {self.overlap.summary()}",
            f"features       {len(self.informative_features)}/{len(STAGE3_FEATURE_NAMES)} vary: "
            f"{', '.join(self.informative_features)}",
            "split          " + ", ".join(f"{k}={v}" for k, v in self.split_sizes.items()),
            f"cross-encoder  {'present' if self.has_cross_encoder else 'ABSENT'}",
            "",
            f"{'metric':<12} {'baseline':>10} {'trained':>10} {'delta':>10}",
        ]
        for name in ("ndcg", "mrr", "recall"):
            base = self.baseline.get(name, 0.0)
            trained = self.trained.get(name, 0.0)
            lines.append(f"{name:<12} {base:>10.4f} {trained:>10.4f} {trained - base:>+10.4f}")
        if self.importance:
            ranked = sorted(self.importance.items(), key=lambda kv: -kv[1])
            top = ", ".join(f"{k} {v:.0f}" for k, v in ranked if v > 0) or "none"
            lines += ["", f"gain           {top}"]
        if self.calibration is not None:
            lines += ["", f"calibration    {self.calibration.summary()}"]
        lines += ["", f"CERTIFIED      {self.certified}"]
        for reason in self.blockers:
            lines.append(f"  blocker      {reason}")
        if self.model_path:
            lines.append(f"artefact       {self.model_path}")
        return "\n".join(lines)


# Input: groups (Sequence[LabelledGroup]), ctx (FeatureContext)
# Output: list[LabelledGroup]
# Processing: Re-featurise groups with the Stage 3 feature set
# Changed by: Sourish
# Date: 2026-10-12
def to_stage3_groups(
    groups: Sequence[LabelledGroup], ctx: FeatureContext, candidates_by_group=None
) -> list[LabelledGroup]:
    """Widen each group's matrix to the Stage 3 columns.

    When no candidate objects are supplied the cross-encoder column is zero,
    which is the honest representation of a stage that did not run -- and
    :attr:`Stage3Report.has_cross_encoder` reports it as absent rather than
    letting the constant column pass unnoticed.
    """
    out: list[LabelledGroup] = []
    for index, group in enumerate(groups):
        candidates = None if candidates_by_group is None else candidates_by_group[index]
        if candidates is not None:
            features = build_stage3_matrix(candidates, ctx)
        else:
            padding = np.zeros((group.features.shape[0], 1), dtype=np.float32)
            features = np.hstack([group.features, padding])
        out.append(
            LabelledGroup(
                query=group.query,
                query_id=group.query_id,
                target_id=group.target_id,
                language=group.language,
                candidate_ids=group.candidate_ids,
                features=features,
                labels=group.labels,
            )
        )
    return out


# Input: service (object), entries (Sequence[Entry]), queries (Sequence[SyntheticQuery]), ctx (FeatureContext | None), config (LightGBMConfig | None), model_path (str | None), outcomes (tuple | None)
# Output: Stage3Report
# Processing: Run the Stage 3 pipeline and report what it cannot establish
# Changed by: Sourish
# Date: 2026-10-12
def run_stage3_training(
    service,
    entries: Sequence[Entry],
    queries: Sequence[SyntheticQuery],
    ctx: FeatureContext | None = None,
    *,
    config: LightGBMConfig | None = None,
    model_path: str | None = None,
    outcomes: tuple[Sequence[float], Sequence[float], str] | None = None,
) -> Stage3Report:
    """Fit Stage 3, fit its calibrator if outcomes exist, and report honestly.

    ``outcomes`` is ``(scores, accepted, source)`` from live traffic. Passing
    ``None`` is the current state of the world and produces an uncertified
    report that names the gap, rather than a calibrator fitted on nothing.
    """
    from ml.ranking.features import UsageIndex

    ctx = ctx or FeatureContext.build(usage_index=UsageIndex())
    overlap = measure_overlap(queries, {e.id: e.description for e in entries})

    splits = split_by_entry(queries)
    assembled = {
        name: to_stage3_groups(assemble_groups(service, qs, ctx), ctx)
        for name, qs in splits.items()
    }

    stacked = np.concatenate(
        [g.features for groups in assembled.values() for g in groups], axis=0
    )
    informative = tuple(
        name
        for index, name in enumerate(STAGE3_FEATURE_NAMES)
        if stacked.size and len(np.unique(stacked[:, index])) > 1
    )

    ranker = LightGBMFinalRanker(config)
    ranker.fit(
        [
            TrainingGroup(features=g.features, labels=g.labels, query_id=g.query_id)
            for g in assembled["train"]
        ],
        validation=[
            TrainingGroup(features=g.features, labels=g.labels, query_id=g.query_id)
            for g in assembled["valid"]
        ]
        or None,
    )
    if model_path:
        ranker.save(model_path)

    calibration = None
    if outcomes is not None:
        scores, accepted, source = outcomes
        _, calibration = fit_and_measure_calibration(scores, accepted, outcome_source=source)

    return Stage3Report(
        overlap=overlap,
        informative_features=informative,
        split_sizes={name: len(groups) for name, groups in assembled.items()},
        baseline=evaluate_ranker(LinearFinalRanker(), assembled["test"]),
        trained=evaluate_ranker(ranker, assembled["test"]),
        importance=ranker.feature_importance(),
        calibration=calibration,
        model_path=model_path,
    )


__all__ = [
    "CROSS_ENCODER_FEATURE",
    "MIN_CALIBRATION_OBSERVATIONS",
    "TRUSTED_OUTCOME_SOURCES",
    "CalibrationReport",
    "Stage3Report",
    "fit_and_measure_calibration",
    "run_stage3_training",
    "to_stage3_groups",
]
