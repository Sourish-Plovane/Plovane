"""Evaluation: metrics against known values, the gold set, the release gates."""

from __future__ import annotations

import numpy as np
import pytest

from ml.config import MVP_LANGUAGES
from ml.evaluation.gates import ReleaseGate
from ml.evaluation.gold_set import GoldQuery, GoldSet, assign_split
from ml.evaluation.metrics import (
    MetricSnapshot,
    QueryOutcome,
    acceptance_rate,
    evaluate,
    expected_calibration_error,
    mean_reciprocal_rank,
    ndcg_at_k,
    recall_at_k,
    reciprocal_rank,
    reverted_insert_rate,
)


class TestRecall:
    def test_counts_a_hit_inside_k_only(self):
        assert recall_at_k(["a", "b", "c"], {"c"}, 3) == 1.0
        assert recall_at_k(["a", "b", "c"], {"c"}, 2) == 0.0

    def test_partial_credit_across_several_relevant_entries(self):
        assert recall_at_k(["a", "b"], {"a", "x"}, 2) == 0.5

    def test_degenerate_inputs(self):
        assert recall_at_k(["a"], set(), 5) == 0.0
        assert recall_at_k(["a"], {"a"}, 0) == 0.0


class TestNDCG:
    def test_perfect_order_scores_one(self):
        assert ndcg_at_k(["a", "b"], {"a": 3, "b": 1}) == pytest.approx(1.0)

    def test_wrong_order_scores_less(self):
        assert ndcg_at_k(["b", "a"], {"a": 3, "b": 1}) < 1.0

    def test_exponential_gain_prefers_the_strong_match_higher(self):
        """A grade-3 at rank 2 should beat a grade-1 at rank 1 being perfect."""
        strong_second = ndcg_at_k(["b", "a"], {"a": 3, "b": 1})
        assert 0.0 < strong_second < 1.0

    def test_no_relevant_entries_scores_zero(self):
        assert ndcg_at_k(["a"], {}) == 0.0

    def test_k_truncates(self):
        assert ndcg_at_k(["x", "a"], {"a": 3}, 1) == 0.0


class TestReciprocalRank:
    def test_is_the_inverse_of_the_first_hit_position(self):
        assert reciprocal_rank(["x", "a"], {"a"}) == 0.5
        assert reciprocal_rank(["a"], {"a"}) == 1.0

    def test_no_hit_scores_zero(self):
        assert reciprocal_rank(["x"], {"a"}) == 0.0

    def test_mean_over_queries(self):
        assert mean_reciprocal_rank([["a"], ["x", "b"]], [{"a"}, {"b"}]) == pytest.approx(0.75)

    def test_rejects_misaligned_inputs(self):
        with pytest.raises(ValueError, match="ranked lists"):
            mean_reciprocal_rank([["a"]], [{"a"}, {"b"}])


class TestCalibrationError:
    def test_perfectly_calibrated_scores_zero(self):
        scores = [0.0] * 50 + [1.0] * 50
        outcomes = [0.0] * 50 + [1.0] * 50
        assert expected_calibration_error(scores, outcomes) == pytest.approx(0.0)

    def test_confidently_wrong_scores_high(self):
        assert expected_calibration_error([0.9] * 10, [0.0] * 10) == pytest.approx(0.9)

    def test_empty_bins_are_skipped_not_counted_as_perfect(self):
        """More bins must not make a miscalibrated model look better."""
        scores, outcomes = [0.9] * 10, [0.0] * 10
        assert expected_calibration_error(scores, outcomes, bins=10) == pytest.approx(
            expected_calibration_error(scores, outcomes, bins=50)
        )

    def test_score_of_one_lands_in_the_last_bin(self):
        assert expected_calibration_error([1.0], [1.0]) == pytest.approx(0.0)

    def test_rejects_bad_input(self):
        with pytest.raises(ValueError, match="align"):
            expected_calibration_error([0.5], [1.0, 0.0])
        with pytest.raises(ValueError, match="bins"):
            expected_calibration_error([0.5], [1.0], bins=0)


class TestLiveMetrics:
    def test_acceptance_rate(self):
        assert acceptance_rate([1.0, 1.0, 0.0, 0.0]) == 0.5

    def test_reverted_insert_rate(self):
        assert reverted_insert_rate(10, 3) == 0.3
        assert reverted_insert_rate(0, 0) == 0.0

    def test_more_reverts_than_inserts_is_rejected(self):
        with pytest.raises(ValueError, match="cannot exceed"):
            reverted_insert_rate(2, 5)


class TestEvaluate:
    def test_reports_per_language_as_well_as_overall(self):
        outcomes = [
            QueryOutcome(
                query_id="q1",
                language="cuda",
                retrieved_ids=("a", "b"),
                ranked_ids=("b", "a"),
                relevance={"a": 3},
            ),
            QueryOutcome(
                query_id="q2",
                language="python",
                retrieved_ids=("a",),
                ranked_ids=("a",),
                relevance={"a": 3},
            ),
        ]
        snapshot = evaluate(outcomes, recall_k=2)
        assert snapshot.queries == 2
        assert set(snapshot.per_language) == {"cuda", "python"}
        assert snapshot.per_language["python"].ndcg_at_10 == pytest.approx(1.0)
        assert snapshot.per_language["cuda"].ndcg_at_10 < 1.0

    def test_a_strong_average_can_hide_a_weak_language(self):
        """The reason every metric is reported per language."""
        outcomes = [
            QueryOutcome(
                query_id=f"good{i}",
                language="python",
                retrieved_ids=("a",),
                ranked_ids=("a",),
                relevance={"a": 3},
            )
            for i in range(9)
        ] + [
            QueryOutcome(
                query_id="bad",
                language="cuda",
                retrieved_ids=("x",),
                ranked_ids=("x",),
                relevance={"a": 3},
            )
        ]
        snapshot = evaluate(outcomes, recall_k=1)
        assert snapshot.ndcg_at_10 > 0.85, "the average looks healthy"
        assert snapshot.per_language["cuda"].ndcg_at_10 == 0.0, "while cuda is broken"

    def test_empty_input(self):
        assert evaluate([]).queries == 0


class TestGoldSet:
    def test_split_assignment_is_deterministic(self):
        assert assign_split("g1") == assign_split("g1")

    def test_splits_are_roughly_proportional(self):
        queries = [
            GoldQuery(query_id=f"g{i}", query="q", language="python", relevance={"a": 3})
            for i in range(600)
        ]
        gold = GoldSet.from_queries(queries, assign_splits=True)
        assert 0.2 < len(gold.holdout) / len(gold) < 0.4

    def test_coverage_report_names_missing_mvp_languages(self):
        gold = GoldSet.from_queries(
            [GoldQuery(query_id="g1", query="q", language="python", relevance={"a": 3})]
        )
        report = gold.coverage_report()
        assert set(report["missing_languages"]) == set(MVP_LANGUAGES) - {"python"}

    def test_coverage_report_flags_a_thin_language(self):
        gold = GoldSet.from_queries(
            [
                GoldQuery(query_id=f"g{i}", query="q", language="cuda", relevance={"a": 3})
                for i in range(3)
            ]
        )
        assert "cuda" in gold.coverage_report(minimum_per_language=20)["thin_languages"]

    def test_rejects_a_query_with_no_relevant_entry(self):
        with pytest.raises(ValueError, match="no relevant entries"):
            GoldQuery(query_id="g", query="q", language="python", relevance={})

    def test_rejects_duplicate_ids(self):
        gold = GoldSet()
        gold.add(GoldQuery(query_id="g", query="q", language="python", relevance={"a": 3}))
        with pytest.raises(ValueError, match="duplicate"):
            gold.add(GoldQuery(query_id="g", query="q2", language="python", relevance={"b": 3}))

    def test_round_trips_through_jsonl(self, tmp_path):
        from ml.schemas import ProjectContext

        original = GoldSet.from_queries(
            [
                GoldQuery(
                    query_id="g1",
                    query="split a dataset",
                    language="python",
                    relevance={"hf_0412": 3.0},
                    project_context=ProjectContext(languages=("python",), framework="pytorch"),
                    category="data-processing",
                    split="holdout",
                )
            ]
        )
        path = tmp_path / "gold.jsonl"
        original.to_jsonl(path)
        reloaded = GoldSet.from_jsonl(path)
        assert len(reloaded) == 1
        q = reloaded.queries[0]
        assert q.split == "holdout"
        assert q.relevance == {"hf_0412": 3.0}
        assert q.project_context.framework == "pytorch"


class TestReleaseGate:
    def baseline(self) -> MetricSnapshot:
        return MetricSnapshot(
            recall_at_1000=0.90, ndcg_at_10=0.60, calibration_error=0.05, queries=200
        )

    def test_improvement_passes(self):
        candidate = MetricSnapshot(
            recall_at_1000=0.91, ndcg_at_10=0.62, calibration_error=0.04, queries=200
        )
        assert ReleaseGate().check(self.baseline(), candidate).passed

    def test_recall_drop_blocks(self):
        candidate = MetricSnapshot(
            recall_at_1000=0.85, ndcg_at_10=0.62, calibration_error=0.05, queries=200
        )
        result = ReleaseGate().check(self.baseline(), candidate)
        assert not result.passed
        assert any("recall_at_1000" in reason for reason in result.blocking)

    def test_calibration_rise_blocks(self):
        candidate = MetricSnapshot(
            recall_at_1000=0.90, ndcg_at_10=0.60, calibration_error=0.09, queries=200
        )
        assert not ReleaseGate().check(self.baseline(), candidate).passed

    def test_noise_inside_tolerance_passes(self):
        candidate = MetricSnapshot(
            recall_at_1000=0.8995, ndcg_at_10=0.5995, calibration_error=0.0505, queries=200
        )
        assert ReleaseGate().check(self.baseline(), candidate).passed

    def test_too_few_queries_blocks_rather_than_passing(self):
        """A measurement too small to detect a regression is not evidence."""
        result = ReleaseGate().check(self.baseline(), MetricSnapshot(queries=10))
        assert not result.passed
        assert any("below the minimum" in reason for reason in result.blocking)

    def test_language_regression_warns_by_default(self):
        base = MetricSnapshot(
            recall_at_1000=0.9,
            ndcg_at_10=0.6,
            calibration_error=0.05,
            queries=200,
            per_language={"cuda": MetricSnapshot(ndcg_at_10=0.5, recall_at_1000=0.8, queries=30)},
        )
        candidate = MetricSnapshot(
            recall_at_1000=0.9,
            ndcg_at_10=0.62,
            calibration_error=0.05,
            queries=200,
            per_language={"cuda": MetricSnapshot(ndcg_at_10=0.40, recall_at_1000=0.8, queries=30)},
        )
        result = ReleaseGate().check(base, candidate)
        assert result.passed
        assert any("cuda" in w for w in result.warnings)

    def test_language_regression_can_be_made_blocking(self):
        base = MetricSnapshot(
            recall_at_1000=0.9,
            ndcg_at_10=0.6,
            calibration_error=0.05,
            queries=200,
            per_language={"cuda": MetricSnapshot(ndcg_at_10=0.5, recall_at_1000=0.8, queries=30)},
        )
        candidate = MetricSnapshot(
            recall_at_1000=0.9,
            ndcg_at_10=0.62,
            calibration_error=0.05,
            queries=200,
            per_language={"cuda": MetricSnapshot(ndcg_at_10=0.40, recall_at_1000=0.8, queries=30)},
        )
        result = ReleaseGate(block_on_language_regression=True).check(base, candidate)
        assert not result.passed

    def test_summary_is_readable(self):
        result = ReleaseGate().check(
            self.baseline(),
            MetricSnapshot(recall_at_1000=0.5, ndcg_at_10=0.6, calibration_error=0.05, queries=200),
        )
        assert "BLOCKED" in result.summary()
