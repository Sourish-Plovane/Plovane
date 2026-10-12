"""Ranking primitives: features, calibration, MMR, the stage rankers."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import numpy as np
import pytest

from ml.ranking.calibration import IsotonicCalibrator, pool_adjacent_violators
from ml.ranking.features import (
    FEATURE_NAMES,
    MISSING_RANK,
    FeatureContext,
    UsageIndex,
    build_feature_matrix,
    co_usage_lift,
    smoothed_acceptance,
)
from ml.ranking.mmr import maximal_marginal_relevance
from ml.ranking.pipeline import new_query_id
from ml.ranking.stage1 import LinearFeatureRanker, normalize_within_query, rank_stage1
from ml.ranking.stage3 import FinalRanker, LinearFinalRanker, build_stage3_matrix
from ml.schemas import Candidate, Entry, EntryType

NOW = datetime(2026, 10, 11, tzinfo=timezone.utc)


def candidate(entry_id: str, **kwargs) -> Candidate:
    entry_kwargs = {
        k: kwargs.pop(k)
        for k in list(kwargs)
        if k in {"usage_count", "impressions", "accepts", "mutation_score", "last_retest_at", "embedding", "owner_id"}
    }
    entry = Entry(
        id=entry_id,
        type=EntryType.HELPER,
        name=entry_id,
        description="d",
        category="c",
        language="python",
        **entry_kwargs,
    )
    return Candidate(entry=entry, **kwargs)


class TestSmoothedAcceptance:
    def test_low_impression_entry_is_pulled_to_the_global_average(self):
        ctx = FeatureContext(global_acceptance_rate=0.30)
        lucky = candidate("lucky", impressions=2, accepts=2)
        proven = candidate("proven", impressions=600, accepts=400)
        assert smoothed_acceptance(lucky, ctx) < smoothed_acceptance(proven, ctx)

    def test_raw_rate_would_have_got_it_backwards(self):
        lucky = candidate("lucky", impressions=2, accepts=2)
        proven = candidate("proven", impressions=600, accepts=400)
        assert lucky.entry.acceptance_rate > proven.entry.acceptance_rate

    def test_no_history_lands_on_the_global_average(self):
        ctx = FeatureContext(global_acceptance_rate=0.42)
        assert smoothed_acceptance(candidate("new"), ctx) == pytest.approx(0.42)


class TestCoUsageLift:
    def test_divides_out_overall_popularity(self):
        """A locally-favoured entry beats a globally-popular one."""
        usage = UsageIndex(
            project_embeddings={"near": np.array([1.0, 0.0]), "far": np.array([0.0, 1.0])},
            project_usage={"near": {"local": 9}, "far": {"popular": 30}},
        )
        ctx = FeatureContext.build(
            usage_index=usage, project_embedding=np.array([1.0, 0.05]), neighbours=1
        )
        local = candidate("local", usage_count=9)
        popular = candidate("popular", usage_count=30)
        assert co_usage_lift(local, ctx) > co_usage_lift(popular, ctx)

    def test_returns_zero_when_there_is_no_signal(self):
        assert co_usage_lift(candidate("x", usage_count=5), FeatureContext()) == 0.0
        ctx = FeatureContext(neighbour_count=3, total_usage=10, co_usage={"x": 2})
        assert co_usage_lift(candidate("x", usage_count=0), ctx) == 0.0

    def test_nearest_projects_needs_an_embedding(self):
        usage = UsageIndex(project_embeddings={"p": np.array([1.0])}, project_usage={"p": {"a": 1}})
        assert usage.nearest_projects(None) == []


class TestFeatureMatrix:
    def test_column_order_and_width(self):
        matrix = build_feature_matrix([candidate("a", rrf_rank=1)], FeatureContext())
        assert matrix.shape == (1, len(FEATURE_NAMES))

    def test_missing_rank_is_finite(self):
        """LightGBM should read 'not retrieved' as a worse rank, not as NaN."""
        matrix = build_feature_matrix([candidate("a")], FeatureContext())
        assert matrix[0, FEATURE_NAMES.index("rrf_rank")] == MISSING_RANK
        assert np.all(np.isfinite(matrix))

    def test_never_retested_is_penalised_not_rewarded(self):
        stale = candidate("stale")
        fresh = candidate("fresh", last_retest_at=NOW - timedelta(days=1))
        idx = FEATURE_NAMES.index("days_since_retest")
        ctx = FeatureContext(now=NOW)
        assert build_feature_matrix([stale], ctx)[0, idx] > build_feature_matrix([fresh], ctx)[0, idx]

    def test_empty_candidate_list(self):
        assert build_feature_matrix([], FeatureContext()).shape == (0, len(FEATURE_NAMES))


class TestNormalizeWithinQuery:
    def test_constant_column_maps_to_the_midpoint(self):
        out = normalize_within_query(np.array([[5.0, 1.0], [5.0, 2.0]], dtype=np.float32))
        assert out[0, 0] == pytest.approx(0.5) and out[1, 0] == pytest.approx(0.5)

    def test_spans_zero_to_one(self):
        out = normalize_within_query(np.array([[1.0], [3.0], [5.0]], dtype=np.float32))
        assert out.min() == pytest.approx(0.0) and out.max() == pytest.approx(1.0)


class TestStageRankers:
    def test_stage1_keeps_the_top_k_and_writes_scores(self):
        candidates = [candidate(f"e{i}", rrf_rank=i + 1, cosine_score=1 - i * 0.1) for i in range(6)]
        kept = rank_stage1(candidates, FeatureContext(), LinearFeatureRanker(), top_k=3)
        assert [c.id for c in kept] == ["e0", "e1", "e2"]
        assert all(c.stage1_score is not None for c in candidates)

    def test_negative_weights_point_the_right_way(self):
        """Rank 1 must beat rank 500; compatible must beat conflicting."""
        ranker = LinearFeatureRanker()
        good = np.zeros((2, len(FEATURE_NAMES)), dtype=np.float32)
        good[0, FEATURE_NAMES.index("rrf_rank")] = 1
        good[1, FEATURE_NAMES.index("rrf_rank")] = 500
        assert ranker.score(good)[0] > ranker.score(good)[1]

        dep = np.zeros((2, len(FEATURE_NAMES)), dtype=np.float32)
        dep[0, FEATURE_NAMES.index("dependency_compat")] = 0
        dep[1, FEATURE_NAMES.index("dependency_compat")] = 2
        assert ranker.score(dep)[0] > ranker.score(dep)[1]

    def test_stage3_appends_the_cross_encoder_score_last(self):
        c = candidate("a", cross_encoder_score=2.5)
        matrix = build_stage3_matrix([c], FeatureContext())
        assert matrix.shape[1] == len(FEATURE_NAMES) + 1
        assert matrix[0, -1] == pytest.approx(2.5)

    def test_missing_cross_encoder_score_does_not_crash(self):
        matrix = build_stage3_matrix([candidate("a")], FeatureContext())
        assert matrix[0, -1] == 0.0

    def test_final_ranker_orders_by_calibrated_score(self):
        candidates = [candidate(f"e{i}", cross_encoder_score=float(i)) for i in range(4)]
        ordered = FinalRanker(model=LinearFinalRanker()).rank(candidates, FeatureContext())
        assert [c.id for c in ordered] == ["e3", "e2", "e1", "e0"]
        assert all(0.0 <= c.calibrated_score <= 1.0 for c in ordered)

    def test_uncalibrated_final_ranker_reports_itself(self):
        assert not FinalRanker(model=LinearFinalRanker()).is_calibrated

    def test_rejects_unknown_weight_names(self):
        with pytest.raises(ValueError, match="unknown feature names"):
            LinearFeatureRanker({"not_a_feature": 1.0})


class TestIsotonicCalibration:
    @pytest.fixture
    def fitted(self) -> IsotonicCalibrator:
        rng = np.random.default_rng(1)
        scores = rng.normal(size=4000)
        accepted = (rng.random(4000) < 1 / (1 + np.exp(-2 * scores))).astype(float)
        return IsotonicCalibrator().fit(scores, accepted)

    def test_recovers_a_known_sigmoid(self, fitted):
        for raw in (-1.0, 0.0, 1.0):
            truth = 1 / (1 + np.exp(-2 * raw))
            assert fitted.predict([raw])[0] == pytest.approx(truth, abs=0.08)

    def test_is_monotone(self, fitted):
        out = fitted.predict(np.linspace(-3, 3, 200))
        assert np.all(np.diff(out) >= -1e-6)

    def test_output_stays_in_range(self, fitted):
        out = fitted.predict([-99.0, 99.0])
        assert 0.0 <= out.min() and out.max() <= 1.0

    def test_pava_pools_a_violation(self):
        thresholds, values = pool_adjacent_violators([1, 2, 3], [1, 0, 1])
        assert list(values) == pytest.approx([0.5, 0.5, 1.0])
        assert np.all(np.diff(values) >= 0)

    def test_single_distinct_score_returns_that_rate(self):
        calibrator = IsotonicCalibrator().fit([5.0, 5.0], [1.0, 0.0])
        assert calibrator.predict([0.0, 9.0]) == pytest.approx([0.5, 0.5])

    def test_unfitted_raises(self):
        with pytest.raises(RuntimeError, match="not fitted"):
            IsotonicCalibrator().predict([0.0])

    def test_round_trips_through_disk(self, fitted, tmp_path):
        path = tmp_path / "cal.npz"
        fitted.save(path)
        reloaded = IsotonicCalibrator().load(path)
        probe = np.linspace(-2, 2, 25)
        assert reloaded.predict(probe) == pytest.approx(fitted.predict(probe))

    def test_rejects_misaligned_input(self):
        with pytest.raises(ValueError, match="align"):
            IsotonicCalibrator().fit([1.0, 2.0], [1.0])


class TestMMR:
    def _candidate(self, entry_id, score, vector):
        c = candidate(entry_id, embedding=np.asarray(vector, dtype=np.float32))
        c.calibrated_score = score
        return c

    def test_drops_a_near_duplicate(self):
        items = [
            self._candidate("a", 0.90, [1.0, 0.0]),
            self._candidate("a_dup", 0.88, [0.999, 0.01]),
            self._candidate("b", 0.60, [0.0, 1.0]),
        ]
        assert [c.id for c in maximal_marginal_relevance(items, k=2)] == ["a", "b"]

    def test_pure_relevance_keeps_it(self):
        items = [
            self._candidate("a", 0.90, [1.0, 0.0]),
            self._candidate("a_dup", 0.88, [0.999, 0.01]),
            self._candidate("b", 0.60, [0.0, 1.0]),
        ]
        assert [c.id for c in maximal_marginal_relevance(items, k=2, lambda_=1.0)] == ["a", "a_dup"]

    def test_first_pick_is_always_the_most_relevant(self):
        items = [
            self._candidate("low", 0.2, [1.0, 0.0]),
            self._candidate("high", 0.9, [0.0, 1.0]),
        ]
        assert maximal_marginal_relevance(items, k=1)[0].id == "high"

    def test_without_embeddings_falls_back_to_top_k(self):
        a, b = candidate("a"), candidate("b")
        a.calibrated_score, b.calibrated_score = 0.9, 0.5
        assert [c.id for c in maximal_marginal_relevance([a, b], k=2)] == ["a", "b"]

    def test_edge_cases(self):
        assert maximal_marginal_relevance([], k=3) == []
        assert maximal_marginal_relevance([candidate("a")], k=0) == []

    def test_rejects_lambda_out_of_range(self):
        with pytest.raises(ValueError, match="lambda_"):
            maximal_marginal_relevance([candidate("a")], k=1, lambda_=1.5)


class TestQueryId:
    def test_is_wide_enough_to_not_collide(self):
        """Four hex characters would collide within days at projected volume."""
        ids = {new_query_id() for _ in range(20000)}
        assert len(ids) == 20000
        assert all(i.startswith("q_") for i in ids)
        assert len(next(iter(ids))) >= 10
