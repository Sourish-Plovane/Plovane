"""Evaluation: fixed metrics, a frozen gold set, and the gates that use them."""

from ml.evaluation.gates import GATED_METRICS, GateResult, MetricDelta, ReleaseGate
from ml.evaluation.gold_set import GoldQuery, GoldSet, assign_split
from ml.evaluation.metrics import (
    MetricSnapshot,
    QueryOutcome,
    acceptance_rate,
    evaluate,
    expected_calibration_error,
    mean_reciprocal_rank,
    ndcg_at_k,
    per_language,
    recall_at_k,
    reciprocal_rank,
    reverted_insert_rate,
)

__all__ = [
    "GATED_METRICS",
    "GateResult",
    "GoldQuery",
    "GoldSet",
    "MetricDelta",
    "MetricSnapshot",
    "QueryOutcome",
    "ReleaseGate",
    "acceptance_rate",
    "assign_split",
    "evaluate",
    "expected_calibration_error",
    "mean_reciprocal_rank",
    "ndcg_at_k",
    "per_language",
    "recall_at_k",
    "reciprocal_rank",
    "reverted_insert_rate",
]
