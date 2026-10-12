"""Retrieval: tokenisation, BM25, fusion, filters, vector search."""

from __future__ import annotations

import numpy as np
import pytest

from ml.retrieval.filters import FilterSpec, classify_dependency_compat
from ml.retrieval.fusion import reciprocal_rank_fusion
from ml.retrieval.keyword import InMemoryBM25Index, render_bm25_sql, tokenize
from ml.retrieval.query import build_query
from ml.retrieval.vector import (
    InMemoryVectorIndex,
    l2_normalize,
    pgvector_session_sql,
    render_vector_sql,
)
from ml.schemas import DependencyCompat, Entry, EntryType, ProjectContext, Request, Visibility


def entry(entry_id: str, **kwargs) -> Entry:
    defaults = {
        "type": EntryType.HELPER,
        "name": entry_id,
        "description": "",
        "category": "data",
        "language": "python",
    }
    return Entry(id=entry_id, **{**defaults, **kwargs})


class TestTokenize:
    def test_splits_snake_and_camel_case_but_keeps_the_whole_identifier(self):
        tokens = set(tokenize("DataLoader stratified_split"))
        assert {"dataloader", "data", "loader", "stratified_split", "stratified", "split"} <= tokens

    def test_is_case_insensitive(self):
        assert set(tokenize("FastAPI")) == set(tokenize("fastapi")) | {"fast", "api"}

    def test_empty_input(self):
        assert tokenize("") == []


class TestBM25:
    @pytest.fixture
    def index(self) -> InMemoryBM25Index:
        return InMemoryBM25Index(
            [
                entry("a", name="stratified_split", docstring="stratify by label", description="splits a dataframe preserving label distribution"),
                entry("b", name="random_split", description="splits a dataframe at random"),
                entry("c", name="DataLoader", description="builds a batching loader"),
            ]
        )

    def test_exact_identifier_wins(self, index):
        assert index.search("stratified_split")[0][0] == "a"

    def test_finds_camel_case_identifier_from_spaced_words(self, index):
        assert index.search("data loader")[0][0] == "c"

    def test_reindexing_is_idempotent(self, index):
        before = index.search("stratified split")
        index.add(entry("a", name="stratified_split", docstring="stratify by label", description="splits a dataframe preserving label distribution"))
        assert len(index) == 3
        assert index.search("stratified split") == before

    def test_remove_drops_the_entry(self, index):
        index.remove("a")
        assert len(index) == 2
        assert "a" not in {e for e, _ in index.search("stratified split")}

    def test_respects_the_limit(self, index):
        assert len(index.search("splits a dataframe", limit=1)) == 1

    def test_filter_runs_inside_the_search(self, index):
        hits = index.search("splits a dataframe", filters=FilterSpec(languages=("javascript",)))
        assert hits == []

    def test_unmatched_query_returns_nothing(self, index):
        assert index.search("quantum chromodynamics") == []

    def test_empty_index(self):
        assert InMemoryBM25Index([]).search("anything") == []

    def test_idf_is_never_negative(self, index):
        """A term in most documents must not penalise a match."""
        assert all(score > 0 for _, score in index.search("splits a dataframe"))


class TestFusion:
    def test_agreement_across_arms_beats_a_single_top_hit(self):
        fused = dict(reciprocal_rank_fusion([["a", "b"], ["b", "a"]]))
        assert fused["a"] == pytest.approx(fused["b"])

    def test_rank_one_in_both_arms_wins(self):
        fused = reciprocal_rank_fusion([["x", "y"], ["x", "z"]])
        assert fused[0][0] == "x"

    def test_is_deterministic_on_ties(self):
        a = reciprocal_rank_fusion([["p", "q"], ["q", "p"]])
        b = reciprocal_rank_fusion([["p", "q"], ["q", "p"]])
        assert a == b

    def test_handles_empty_and_uneven_lists(self):
        assert reciprocal_rank_fusion([[], []]) == []
        assert [i for i, _ in reciprocal_rank_fusion([["a"], ["b", "c", "d"]])][0] == "a"

    def test_rejects_non_positive_k(self):
        with pytest.raises(ValueError, match="positive"):
            reciprocal_rank_fusion([["a"]], k=0)


class TestFilters:
    def test_public_entries_visible_without_a_requester(self):
        assert FilterSpec().matches(entry("a"))

    def test_private_entry_needs_its_owner(self):
        private = entry("p", visibility=Visibility.PRIVATE, owner_id="u1")
        assert not FilterSpec().matches(private)
        assert not FilterSpec(requester_id="u2").matches(private)
        assert FilterSpec(requester_id="u1").matches(private)

    def test_framework_agnostic_entry_stays_eligible(self):
        assert FilterSpec(framework="pytorch").matches(entry("a", framework=None))
        assert not FilterSpec(framework="pytorch").matches(entry("a", framework="tensorflow"))

    def test_license_must_be_eligible(self):
        assert not FilterSpec().matches(entry("a", license="GPL-3.0"))
        assert FilterSpec(allowed_licenses=("GPL-3.0",)).matches(entry("a", license="GPL-3.0"))

    def test_sql_binds_every_value(self):
        clause, params = FilterSpec(
            requester_id="u1", languages=("python",), category="data-processing"
        ).where_clause()
        assert ":requester_id" in clause and params["requester_id"] == "u1"
        assert "u1" not in clause, "values must be bound, never interpolated"
        assert params["category"] == "data-processing"

    def test_sql_and_python_agree_on_type_restriction(self):
        spec = FilterSpec(entry_types=("template",))
        clause, params = spec.where_clause()
        assert "e.type = ANY(:entry_types)" in clause
        assert params["entry_types"] == ["template"]
        assert spec.matches(entry("t", type=EntryType.TEMPLATE))
        assert not spec.matches(entry("h", type=EntryType.HELPER))

    def test_rendered_sql_includes_the_clause(self):
        spec = FilterSpec(languages=("python",))
        for renderer in (render_bm25_sql, render_vector_sql):
            sql, params = renderer(spec)
            assert "lower(e.language) = ANY(:languages)" in sql
            assert params["languages"] == ["python"]


class TestDependencyCompat:
    def test_all_present_is_compatible(self):
        e = entry("a", dependencies=("torch",))
        assert classify_dependency_compat(e, ("torch",)) is DependencyCompat.COMPATIBLE

    def test_missing_package_needs_install(self):
        e = entry("a", dependencies=("torch",))
        assert classify_dependency_compat(e, ()) is DependencyCompat.NEEDS_INSTALL

    def test_pinned_mismatch_conflicts(self):
        e = entry("a", dependencies=("torch==2.3",))
        assert classify_dependency_compat(e, ("torch==2.2",)) is DependencyCompat.CONFLICTS

    def test_conflict_wins_over_needs_install(self):
        e = entry("a", dependencies=("numpy", "torch==2.3"))
        assert classify_dependency_compat(e, ("torch==2.2",)) is DependencyCompat.CONFLICTS

    def test_ordinals_rank_best_to_worst(self):
        assert (
            DependencyCompat.COMPATIBLE.ordinal
            < DependencyCompat.NEEDS_INSTALL.ordinal
            < DependencyCompat.CONFLICTS.ordinal
        )


class TestVectorIndex:
    @pytest.fixture
    def index(self) -> InMemoryVectorIndex:
        rng = np.random.default_rng(0)
        return InMemoryVectorIndex(
            [entry(f"e{i}", embedding=rng.normal(size=16)) for i in range(6)]
        )

    def test_finds_itself_first(self, index):
        target = index._entries["e2"]
        assert index.search(target.embedding, limit=1)[0] == ("e2", pytest.approx(1.0, abs=1e-5))

    def test_respects_limit_and_filter(self, index):
        assert len(index.search(np.ones(16), limit=2)) == 2
        assert index.search(np.ones(16), filters=FilterSpec(languages=("rust",))) == []

    def test_rejects_entry_without_embedding(self):
        with pytest.raises(ValueError, match="no embedding"):
            InMemoryVectorIndex([entry("a")])

    def test_empty_index(self):
        assert InMemoryVectorIndex([]).search(np.ones(4)) == []


class TestNormalisation:
    def test_unit_length(self):
        v = l2_normalize(np.array([3.0, 4.0]))
        assert np.linalg.norm(v) == pytest.approx(1.0)

    def test_zero_vector_does_not_produce_nan(self):
        v = l2_normalize(np.zeros(4))
        assert np.all(np.isfinite(v)) and np.all(v == 0)


class TestPgvectorSettings:
    def test_includes_ef_search_and_iterative_scan(self):
        statements = pgvector_session_sql(ef_search=200)
        assert any("hnsw.ef_search = 200" in s for s in statements)
        assert any("iterative_scan = relaxed_order" in s for s in statements)

    def test_iterative_scan_can_be_disabled(self):
        assert pgvector_session_sql(iterative_scan="off") == ["SET LOCAL hnsw.ef_search = 100"]

    def test_rejects_an_unknown_mode(self):
        with pytest.raises(ValueError, match="iterative_scan"):
            pgvector_session_sql(iterative_scan="sometimes")


class TestQueryBuilding:
    def test_vector_text_carries_the_full_context(self):
        query = build_query(
            Request(
                query="split a dataset",
                project_context=ProjectContext(
                    languages=("python",),
                    framework="pytorch",
                    dependencies=("torch",),
                    file_summary="training loop",
                ),
            )
        )
        text = query.vector_text
        for expected in ("split a dataset", "python", "pytorch", "torch", "training loop"):
            assert expected in text

    def test_keyword_text_leads_with_the_request(self):
        query = build_query(
            Request(query="stratified_split", project_context=ProjectContext(languages=("python",)))
        )
        assert query.keyword_text.startswith("stratified_split")

    def test_cross_encoder_text_matches_vector_text(self):
        query = build_query(Request(query="q"))
        assert query.cross_encoder_text == query.vector_text
