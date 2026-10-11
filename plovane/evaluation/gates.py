"""Release gates.

Set each baseline from the first measurement, then block any release that
makes a metric worse. A ranking or model change ships only if NDCG@10 and
Recall@1000 do not drop on the held-out set and calibration error does not
rise.

The tolerance exists because a gold set of a few hundred queries has real
sampling noise: a 0.001 NDCG difference is not a regression, and a gate with
zero tolerance blocks on coin flips until people learn to ignore it. It is a
noise allowance, not a budget to spend -- widening it to let a change through
is how a slow decline ships one acceptable step at a time.

Per-language regressions are reported but do not block by default. A thin
language swings on a single query, so blocking on it would be blocking on
noise; the point of surfacing them is that a strong average hides a language
where results are poor, such as CUDA. Set ``block_on_language_regression``
once a language has enough gold queries for its metrics to mean something.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field

from plovane.evaluation.metrics import MetricSnapshot

HIGHER_IS_BETTER: tuple[str, ...] = ("recall_at_1000", "ndcg_at_10", "mrr")
LOWER_IS_BETTER: tuple[str, ...] = ("calibration_error",)
GATED_METRICS: tuple[str, ...] = ("recall_at_1000", "ndcg_at_10", "calibration_error")
"""What blocks a release. MRR is measured and reported but not gated -- it
moves with NDCG@10 and gating both just means blocking twice on one signal."""


@dataclass(frozen=True)
class MetricDelta:
    metric: str
    baseline: float
    candidate: float
    tolerance: float
    higher_is_better: bool

    @property
    def change(self) -> float:
        return self.candidate - self.baseline

    @property
    def regressed(self) -> bool:
        if self.higher_is_better:
            return self.change < -self.tolerance
        return self.change > self.tolerance

    def describe(self) -> str:
        direction = "dropped" if self.higher_is_better else "rose"
        return (
            f"{self.metric} {direction} from {self.baseline:.4f} to {self.candidate:.4f} "
            f"({self.change:+.4f}, tolerance {self.tolerance:.4f})"
        )


@dataclass
class GateResult:
    passed: bool
    deltas: tuple[MetricDelta, ...] = ()
    blocking: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()

    def summary(self) -> str:
        if self.passed and not self.warnings:
            return "release gate: PASS"
        lines = [f"release gate: {'PASS' if self.passed else 'BLOCKED'}"]
        lines.extend(f"  blocking: {reason}" for reason in self.blocking)
        lines.extend(f"  warning:  {reason}" for reason in self.warnings)
        return "\n".join(lines)


@dataclass
class ReleaseGate:
    """Compares a candidate measurement against the frozen baseline."""

    tolerance: float = 0.002
    gated_metrics: Sequence[str] = field(default_factory=lambda: GATED_METRICS)
    block_on_language_regression: bool = False
    language_tolerance: float = 0.01
    minimum_queries: int = 100
    """Below this many held-out queries the gate blocks outright rather than
    passing: a measurement too small to detect a regression is not evidence
    that there isn't one."""

    def _delta(self, metric: str, baseline: MetricSnapshot, candidate: MetricSnapshot, tolerance: float) -> MetricDelta:
        return MetricDelta(
            metric=metric,
            baseline=float(getattr(baseline, metric)),
            candidate=float(getattr(candidate, metric)),
            tolerance=tolerance,
            higher_is_better=metric in HIGHER_IS_BETTER,
        )

    def check(self, baseline: MetricSnapshot, candidate: MetricSnapshot) -> GateResult:
        blocking: list[str] = []
        warnings: list[str] = []

        if candidate.queries < self.minimum_queries:
            blocking.append(
                f"held-out set has {candidate.queries} queries, below the minimum {self.minimum_queries}"
            )

        deltas = [self._delta(m, baseline, candidate, self.tolerance) for m in self.gated_metrics]
        blocking.extend(d.describe() for d in deltas if d.regressed)

        for language, base_snapshot in sorted(baseline.per_language.items()):
            cand_snapshot = candidate.per_language.get(language)
            if cand_snapshot is None:
                warnings.append(f"{language}: present in baseline but missing from the candidate measurement")
                continue
            for metric in self.gated_metrics:
                delta = self._delta(metric, base_snapshot, cand_snapshot, self.language_tolerance)
                if delta.regressed:
                    message = f"{language}: {delta.describe()}"
                    (blocking if self.block_on_language_regression else warnings).append(message)

        for language in sorted(set(candidate.per_language) - set(baseline.per_language)):
            warnings.append(f"{language}: new in the candidate measurement, no baseline to compare against")

        return GateResult(
            passed=not blocking,
            deltas=tuple(deltas),
            blocking=tuple(blocking),
            warnings=tuple(warnings),
        )
