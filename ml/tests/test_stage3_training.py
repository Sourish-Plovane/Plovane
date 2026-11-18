"""Stage 3, calibration, and the serving-shape rule that caught a bad model.

The most useful test here is the simplest: a training group must be shaped
like a serving request. Training on the 15 hardest negatives and scoring on
the same 16-row groups produced NDCG@10 0.7736 in cross-validation and 0.0066
on a real retrieved set -- a model 50x worse than the hand-weighted baseline
it was meant to replace, certified by an evaluation of a task nobody performs.
"""

from __future__ import annotations

import numpy as np
import pytest

from ml.evaluation.synthetic import SyntheticQuery, generate_training_queries
from ml.ingest.dataset import entry_from_record
from ml.ranking.calibration import IsotonicCalibrator
from ml.ranking.features import FeatureContext, UsageIndex
from ml.ranking.pipeline import UNCALIBRATED_REASON, RecommendationPipeline
from ml.ranking.stage1 import LinearFeatureRanker
from ml.ranking.stage3 import STAGE3_FEATURE_NAMES, FinalRanker, LinearFinalRanker
from ml.ranking.stage3_training import (
    MIN_CALIBRATION_OBSERVATIONS,
    CalibrationReport,
    Stage3Report,
    fit_and_measure_calibration,
    run_stage3_training,
    to_stage3_groups,
)
from ml.ranking.training import assemble_group, assemble_groups, run_stage1_training
from ml.retrieval.embedding import HashingEmbeddingModel, embed_entries_inplace
from ml.retrieval.keyword import InMemoryBM25Index
from ml.retrieval.service import RetrievalService
from ml.retrieval.vector import InMemoryVectorIndex
from ml.schemas import ProjectContext, Request


# Input: name (str), description (str), language (str)
# Output: dict
# Processing: A harvest record good enough to load
# Changed by: Sourish
# Date: 2026-10-12
def record(name: str, description: str, *, language: str = "python") -> dict:
    return {
        "id": f"owner_repo:{name}",
        "name": name,
        "type": "helper",
        "language": language,
        "category": "data-processing",
        "signature": "(x) -> int",
        "description": description,
        "code": f"def {name}(x): return x",
        "author": "owner",
        "license_spdx": "MIT",
        "needs_review": False,
        "missing": [],
        "description_source": "docstring",
        "code_metrics": {"lines": 1},
    }


# Input: none
# Output: tuple[list, RetrievalService]
# Processing: A small library and a retrieval service over it
# Changed by: Sourish
# Date: 2026-10-12
def tiny_library():
    descriptions = {
        "split_rows": "Splits a dataframe into training and test partitions",
        "parse_date": "Parses an ISO 8601 date string into a datetime",
        "deep_copy": "Returns a deep copy of a nested mapping",
        "slugify": "Converts a title into a url safe slug",
        "retry_call": "Retries a callable with exponential backoff",
        "chunk_list": "Yields fixed size chunks from a sequence",
        "pad_text": "Pads a string on the left to a fixed width",
        "clamp_value": "Bounds a number between a low and high limit",
    }
    entries = [entry_from_record(record(n, t)) for n, t in descriptions.items()]
    embed_entries_inplace(HashingEmbeddingModel(), entries)
    service = RetrievalService(
        keyword_index=InMemoryBM25Index(entries),
        vector_index=InMemoryVectorIndex(entries),
        embedding_model=HashingEmbeddingModel(),
        entry_store={e.id: e for e in entries},
    )
    return entries, service


class TestServingShape:
    """A training group must look like a serving request."""

    def test_the_default_keeps_every_retrieved_candidate(self):
        # The property that was missing. Nothing asserted it, so changing the
        # default from 15 hardest negatives to the full set broke no test --
        # while the 15-negative default was producing an unusable model.
        entries, service = tiny_library()
        query = SyntheticQuery(
            query="splits a dataframe", entry_id=entries[0].id, language="python"
        )
        retrieved = service.retrieve(
            Request(query=query.query, project_context=ProjectContext(languages=("python",)))
        ).candidates
        group = assemble_group(service, query, FeatureContext.build(usage_index=UsageIndex()))
        assert group is not None
        assert len(group.candidate_ids) == len(retrieved)

    def test_an_explicit_count_truncates(self):
        entries, service = tiny_library()
        query = SyntheticQuery(
            query="splits a dataframe", entry_id=entries[0].id, language="python"
        )
        group = assemble_group(
            service, query, FeatureContext.build(usage_index=UsageIndex()), negatives=2
        )
        assert group is not None and len(group.candidate_ids) == 3

    def test_truncation_blocks_certification(self):
        # A run that trains on a shape it will never serve cannot be read as a
        # measurement of serving quality, however good its metric.
        entries, service = tiny_library()
        queries = generate_training_queries(entries, per_entry=2)
        report = run_stage1_training(service, entries, queries, negatives=2, cv_folds=0)
        assert report.negatives_per_group == 2
        assert report.certified is False
        assert any("will never serve" in b for b in report.blockers)

    def test_the_full_set_run_records_its_group_size(self):
        entries, service = tiny_library()
        queries = generate_training_queries(entries, per_entry=2)
        report = run_stage1_training(service, entries, queries, cv_folds=0)
        assert report.negatives_per_group is None
        assert report.median_group_size == len(entries)
        assert not any("will never serve" in b for b in report.blockers)


class TestCalibrationCorrectness:
    """What can actually be established about the calibrator: that it works."""

    def test_it_recovers_a_known_accept_curve(self):
        # Scores whose true accept probability is a known logistic curve. The
        # calibrator should return roughly that probability. This is a property
        # of the implementation, not a claim about the product.
        rng = np.random.default_rng(7)
        raw = rng.normal(0, 2, 4000)
        true_p = 1 / (1 + np.exp(-raw))
        accepted = (rng.random(4000) < true_p).astype(float)

        calibrator, report = fit_and_measure_calibration(
            raw, accepted, outcome_source="usage:2026-10-12"
        )
        assert report.ece < 0.05
        for score in (-2.0, 0.0, 2.0):
            predicted = float(calibrator.predict([score])[0])
            assert abs(predicted - 1 / (1 + np.exp(-score))) < 0.15

    def test_it_never_reverses_the_ranker(self):
        # Isotonic regression is monotone by construction, which is why it is
        # safe here: it can rescale a score but not reorder two candidates.
        rng = np.random.default_rng(3)
        raw = rng.normal(0, 1, 500)
        accepted = (rng.random(500) < 1 / (1 + np.exp(-raw))).astype(float)
        calibrator = IsotonicCalibrator().fit(raw, accepted)
        probe = np.linspace(-3, 3, 50)
        out = calibrator.predict(probe)
        assert np.all(np.diff(out) >= -1e-9)

    def test_ece_is_measured_on_held_out_points(self):
        # Isotonic fits any monotone shape, so ECE on the fitting data is near
        # zero by construction and says nothing about the next request.
        rng = np.random.default_rng(11)
        raw = rng.normal(0, 2, 1200)
        accepted = (rng.random(1200) < 1 / (1 + np.exp(-raw))).astype(float)
        _, report = fit_and_measure_calibration(raw, accepted, outcome_source="live")
        assert report.observations == 1200
        assert report.ece > 0.0

    def test_mismatched_lengths_are_rejected(self):
        with pytest.raises(ValueError, match="must align"):
            fit_and_measure_calibration([0.1, 0.2], [1.0], outcome_source="live")

    def test_nothing_to_calibrate_is_rejected(self):
        with pytest.raises(ValueError, match="nothing to calibrate"):
            fit_and_measure_calibration([], [], outcome_source="live")


class TestCalibrationReport:
    # Input: observations (int), distinct (int), accept_rate (float), source (str)
    # Output: CalibrationReport
    # Processing: A calibration report with chosen properties
    # Changed by: Sourish
    # Date: 2026-10-12
    def report(self, *, observations=500, distinct=40, accept_rate=0.4, source="usage:2026-10-12"):
        return CalibrationReport(
            observations=observations,
            distinct_scores=distinct,
            accept_rate=accept_rate,
            ece=0.03,
            outcome_source=source,
        )

    def test_real_traffic_certifies(self):
        assert self.report().certified is True

    def test_synthetic_outcomes_never_certify(self):
        # Identical numbers, invented provenance. A cutoff fitted on invented
        # accept decisions is arbitrary while looking principled.
        report = self.report(source="synthetic_assistant_authored")
        assert report.certified is False
        assert any("not real traffic" in b for b in report.blockers)

    def test_an_unknown_source_does_not_certify(self):
        assert self.report(source="guessed").certified is False

    def test_too_few_observations_blocks(self):
        report = self.report(observations=MIN_CALIBRATION_OBSERVATIONS - 1)
        assert report.certified is False
        assert any("below the floor" in b for b in report.blockers)

    def test_too_few_distinct_scores_blocks(self):
        report = self.report(distinct=3)
        assert any("distinct scores" in b for b in report.blockers)

    def test_a_single_outcome_class_blocks(self):
        for rate in (0.0, 1.0):
            report = self.report(accept_rate=rate)
            assert any("same class" in b for b in report.blockers)


class TestStage3Report:
    # Input: features (tuple), calibration (CalibrationReport | None)
    # Output: Stage3Report
    # Processing: A Stage 3 report with chosen properties
    # Changed by: Sourish
    # Date: 2026-10-12
    def report(self, features, calibration=None):
        from ml.evaluation.synthetic import OverlapReport

        return Stage3Report(
            overlap=OverlapReport(100, 0.2, 0.2, 0.1, 0.0),
            informative_features=tuple(features),
            split_sizes={"train": 70, "valid": 15, "test": 15},
            baseline={"ndcg": 0.4},
            trained={"ndcg": 0.6},
            calibration=calibration,
        )

    def test_an_absent_cross_encoder_blocks_certification(self):
        # Stage 3 without Stage 2 is Stage 1's features with a constant column.
        report = self.report(("bm25_score", "cosine_score"))
        assert report.has_cross_encoder is False
        assert report.certified is False
        assert any("cross_encoder_score never varied" in b for b in report.blockers)

    def test_a_missing_calibration_blocks_certification(self):
        report = self.report(("bm25_score", "cross_encoder_score"))
        assert report.has_cross_encoder is True
        assert any("no calibration was fitted" in b for b in report.blockers)

    def test_an_uncertified_calibration_is_reported_through(self):
        bad = CalibrationReport(
            observations=10, distinct_scores=2, accept_rate=0.5, ece=0.4, outcome_source="synthetic"
        )
        report = self.report(("bm25_score", "cross_encoder_score"), calibration=bad)
        assert report.certified is False
        assert any(b.startswith("calibration:") for b in report.blockers)

    def test_everything_present_certifies(self):
        good = CalibrationReport(
            observations=500, distinct_scores=40, accept_rate=0.4, ece=0.03, outcome_source="usage"
        )
        report = self.report(("bm25_score", "cross_encoder_score"), calibration=good)
        assert report.certified is True


class TestStage3Groups:
    def test_the_matrix_gains_the_cross_encoder_column(self):
        entries, service = tiny_library()
        ctx = FeatureContext.build(usage_index=UsageIndex())
        queries = generate_training_queries(entries, per_entry=1)
        groups = assemble_groups(service, queries, ctx)
        widened = to_stage3_groups(groups, ctx)
        assert widened[0].features.shape[1] == len(STAGE3_FEATURE_NAMES)
        assert groups[0].features.shape[1] == len(STAGE3_FEATURE_NAMES) - 1

    def test_the_run_reports_the_absent_cross_encoder(self):
        entries, service = tiny_library()
        queries = generate_training_queries(entries, per_entry=2)
        report = run_stage3_training(service, entries, queries)
        assert report.has_cross_encoder is False
        assert report.certified is False


class TestCutoffHonesty:
    """Without a calibrator the cutoff cannot be applied, so it is not claimed."""

    # Input: calibrator (IsotonicCalibrator | None)
    # Output: RecommendationPipeline
    # Processing: A pipeline over the tiny library
    # Changed by: Sourish
    # Date: 2026-10-12
    def pipeline(self, calibrator=None):
        _, service = tiny_library()
        return RecommendationPipeline(
            retrieval=service,
            stage1_ranker=LinearFeatureRanker(),
            final_ranker=FinalRanker(model=LinearFinalRanker(), calibrator=calibrator),
            cross_encoder=None,
        )

    def test_an_uncalibrated_response_says_so(self):
        # Applying 0.5 to the uncalibrated sigmoid passed 100 candidates out of
        # 100 on the real library, and returned three confident results for a
        # query about warp cores. Serving the list unfiltered and saying so is
        # honest; reporting a cutoff that never fired is not.
        response, log = self.pipeline().recommend(Request(query="splits a dataframe", k=3))
        assert log.cutoff_enforced is False
        assert response.reason == UNCALIBRATED_REASON

    def test_a_calibrated_pipeline_enforces_the_cutoff(self):
        # A calibrator mapping everything to 0.01 must empty the result.
        calibrator = IsotonicCalibrator().fit(
            np.array([0.0, 1.0, 2.0, 3.0]), np.array([0.0, 0.0, 0.0, 0.0])
        )
        response, log = self.pipeline(calibrator).recommend(
            Request(query="splits a dataframe", k=3)
        )
        assert log.cutoff_enforced is True
        assert response.results == ()
        assert response.reason is not None and "cutoff" in response.reason

    def test_a_calibrated_pipeline_returns_matches_above_the_cutoff(self):
        calibrator = IsotonicCalibrator().fit(
            np.array([0.0, 1.0, 2.0, 3.0]), np.array([1.0, 1.0, 1.0, 1.0])
        )
        response, log = self.pipeline(calibrator).recommend(
            Request(query="splits a dataframe", k=3)
        )
        assert log.cutoff_enforced is True
        assert response.results
        assert response.reason is None
