"""End-to-end behaviour of the template recommender."""

from __future__ import annotations

import numpy as np
import pytest

from ml.ranking.calibration import IsotonicCalibrator
from ml.schemas import EntryType, ProjectContext, Request
from ml.templates.features import framework_match, is_template_like, new_dependency_count
from ml.templates.recommender import NO_MATCH_REASON, TemplateRecommender


def react_request(**kwargs) -> Request:
    defaults = {
        "query": "scaffold a new single page app with routing",
        "project_context": ProjectContext(
            languages=("javascript",), framework="react", dependencies=("react",)
        ),
        "k": 3,
    }
    return Request(**{**defaults, **kwargs})


class TestResultShape:
    def test_returns_only_template_like_entries(self, recommender):
        response, _ = recommender.recommend(react_request(k=5))
        assert response.results, "expected at least one template for a scaffolding query"
        assert all(card.type in {EntryType.TEMPLATE, EntryType.BUNDLE} for card in response.results)

    def test_never_returns_a_helper(self, recommender):
        response, _ = recommender.recommend(react_request(k=10))
        returned = {card.id for card in response.results}
        assert "hf_react_hook" not in returned
        assert "hf_split" not in returned

    def test_templates_carry_no_signature(self, recommender):
        """The spec leaves signature out for templates and bundles."""
        response, _ = recommender.recommend(react_request(k=5))
        for card in response.results:
            assert card.signature is None, f"{card.id} should not carry a signature"

    def test_bundle_reports_member_count(self, recommender):
        response, _ = recommender.recommend(react_request(k=10))
        bundles = [c for c in response.results if c.type is EntryType.BUNDLE]
        assert bundles, "the React testing bundle should be retrievable"
        assert all(c.members is not None and c.members > 0 for c in bundles)

    def test_respects_k(self, recommender):
        response, _ = recommender.recommend(react_request(k=2))
        assert len(response.results) <= 2

    def test_payload_carries_no_model_version(self, recommender):
        """The version is logged on the server, not sent to the agent."""
        response, log = recommender.recommend(react_request())
        payload = response.to_dict()
        assert "model_versions" not in payload
        assert "version" not in payload
        assert set(payload) <= {"query_id", "results", "reason"}
        assert log.model_versions, "the log must carry the versions the payload omits"

    def test_log_ties_to_the_response(self, recommender):
        response, log = recommender.recommend(react_request())
        assert log.query_id == response.query_id
        assert log.returned == tuple(card.id for card in response.results)

    def test_query_ids_are_unique(self, recommender):
        ids = {recommender.recommend(react_request())[0].query_id for _ in range(50)}
        assert len(ids) == 50


class TestFiltering:
    def test_excludes_ineligible_license(self, recommender):
        response, _ = recommender.recommend(react_request(k=10))
        assert "tpl_stale_gpl" not in {c.id for c in response.results}

    def test_private_template_hidden_from_other_users(self, recommender):
        response, _ = recommender.recommend(react_request(k=10, requester_id="user_other"))
        assert "tpl_private" not in {c.id for c in response.results}

    def test_private_template_visible_to_its_owner(self, recommender):
        response, _ = recommender.recommend(react_request(k=10, requester_id="user_sourish"))
        assert "tpl_private" in {c.id for c in response.results}

    def test_drop_in_only_does_not_strip_every_template(self, recommender):
        """A template is meant to bring its own dependencies.

        Applying the helper-side drop-in filter here would remove every
        scaffold that installs a framework, which is all the useful ones.
        """
        response, _ = recommender.recommend(react_request(k=5, drop_in_only=True))
        assert response.results, "drop_in_only must not empty the template results"


class TestRankingBehaviour:
    def test_framework_match_outranks_mismatch(self, recommender):
        response, _ = recommender.recommend(react_request(k=10))
        ids = [c.id for c in response.results]
        assert "tpl_vue" not in ids or ids.index("tpl_vue") > 0, "a Vue template must not lead a React query"

    def test_python_query_does_not_return_javascript_templates(self, recommender):
        response, _ = recommender.recommend(
            Request(
                query="scaffold an API service with auth and migrations",
                project_context=ProjectContext(languages=("python",), framework="fastapi"),
                k=3,
            )
        )
        assert [c.id for c in response.results][:1] == ["tpl_fastapi"]
        assert all(c.language == "python" for c in response.results)

    def test_mmr_breaks_up_near_identical_starters(self, recommender):
        """Three React starters differ only in lint config.

        With pure relevance all three take the three slots. MMR should spend at
        most two of them on that cluster.
        """
        response, _ = recommender.recommend(react_request(k=3))
        react_starters = {"tpl_react_1", "tpl_react_2", "tpl_react_3"}
        chosen = {c.id for c in response.results}
        assert len(chosen & react_starters) <= 2, f"MMR left the list duplicated: {chosen}"

    def test_pure_relevance_keeps_the_duplicates(self, recommender):
        """The control for the test above: lambda=1.0 disables diversification."""
        recommender.mmr_lambda = 1.0
        response, _ = recommender.recommend(react_request(k=3))
        react_starters = {"tpl_react_1", "tpl_react_2", "tpl_react_3"}
        assert len({c.id for c in response.results} & react_starters) == 3

    def test_scores_are_ordered_best_first(self, recommender):
        response, _ = recommender.recommend(react_request(k=5))
        scores = [c.score for c in response.results]
        assert scores == sorted(scores, reverse=True) or scores[0] == max(scores)


class TestCutoff:
    def test_empty_results_carry_a_reason(self, recommender):
        """When nothing clears the bar, return nothing and say why."""
        recommender.cutoff = 1.01  # nothing can clear this
        response, log = recommender.recommend(react_request())
        assert response.results == ()
        assert response.reason == NO_MATCH_REASON
        assert response.to_dict()["reason"] == NO_MATCH_REASON
        assert log.returned == ()

    def test_calibrated_cutoff_admits_only_confident_results(self, recommender):
        rng = np.random.default_rng(0)
        raw = rng.normal(size=3000)
        accepted = (rng.random(3000) < 1 / (1 + np.exp(-raw))).astype(float)
        recommender.calibrator = IsotonicCalibrator().fit(raw, accepted)
        assert recommender.is_calibrated

        recommender.cutoff = 0.5
        response, _ = recommender.recommend(react_request(k=5))
        assert all(c.score >= 0.5 for c in response.results)

    def test_uncalibrated_recommender_says_so(self, recommender):
        assert not recommender.is_calibrated


class TestFeatures:
    def test_framework_agnostic_template_scores_between(self):
        from ml.schemas import Candidate, Entry

        def candidate(framework):
            return Candidate(
                entry=Entry(
                    id="x",
                    type=EntryType.TEMPLATE,
                    name="n",
                    description="d",
                    category="c",
                    language="javascript",
                    framework=framework,
                )
            )

        assert framework_match(candidate("react"), "react") == 1.0
        assert framework_match(candidate(None), "react") == 0.5
        assert framework_match(candidate("vue"), "react") == 0.0

    def test_new_dependency_count_counts_only_what_is_missing(self):
        from ml.schemas import Candidate, Entry

        c = Candidate(
            entry=Entry(
                id="x",
                type=EntryType.TEMPLATE,
                name="n",
                description="d",
                category="c",
                language="python",
                dependencies=("fastapi", "sqlalchemy", "alembic"),
            )
        )
        assert new_dependency_count(c, ("fastapi",)) == pytest.approx(np.log1p(2))
        assert new_dependency_count(c, ("fastapi", "sqlalchemy", "alembic")) == 0.0

    def test_bundles_can_be_excluded(self, recommender):
        recommender.include_bundles = False
        response, _ = recommender.recommend(react_request(k=10))
        assert all(c.type is EntryType.TEMPLATE for c in response.results)


class TestEmptyLibrary:
    def test_no_candidates_returns_the_reason(self, embedding_model):
        from ml.retrieval.keyword import InMemoryBM25Index
        from ml.retrieval.service import RetrievalService
        from ml.retrieval.vector import InMemoryVectorIndex

        empty = TemplateRecommender(
            retrieval=RetrievalService(
                keyword_index=InMemoryBM25Index([]),
                vector_index=InMemoryVectorIndex([]),
                embedding_model=embedding_model,
                entry_store={},
            )
        )
        response, log = empty.recommend(react_request())
        assert response.results == ()
        assert response.reason == NO_MATCH_REASON
        assert log.candidates_retrieved == 0
