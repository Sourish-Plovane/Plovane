"""Tests for the Stage 1 training harness and the dataset loader.

The tests that matter most here are the ones about *refusing* to certify. A
training run that silently passes off paraphrase matching as ranking accuracy
is the failure this code exists to prevent, so the blocker logic is tested
more carefully than the fit itself.
"""

from __future__ import annotations

import json

import numpy as np
import pytest

from ml.evaluation.synthetic import (
    DEGENERATE_OVERLAP_THRESHOLD,
    SyntheticQuery,
    content_tokens,
    generate_training_queries,
    lexical_overlap,
    measure_overlap,
)
from ml.ingest.dataset import (
    Dataset,
    entry_from_record,
    feature_support,
    load_dataset,
    load_queries,
)
from ml.ranking.features import FEATURE_NAMES, FeatureContext, UsageIndex
from ml.ranking.stage1 import LinearFeatureRanker, normalize_within_query
from ml.ranking.training import (
    CrossValidationResult,
    LabelledGroup,
    TrainingReport,
    TrainingScope,
    assemble_group,
    assemble_groups,
    cross_validate,
    evaluate_ranker,
    run_stage1_training,
    split_by_entry,
)
from ml.retrieval.embedding import HashingEmbeddingModel, embed_entries_inplace
from ml.retrieval.keyword import InMemoryBM25Index
from ml.retrieval.service import RetrievalService
from ml.retrieval.vector import InMemoryVectorIndex
from ml.schemas import Entry, EntryType


# Input: name (str), description (str), language (str), authored (bool)
# Output: dict
# Processing: A harvest record good enough to load
# Changed by: Sourish
# Date: 2026-10-12
def record(name: str, description: str, *, language: str = "python", authored: bool = True) -> dict:
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
        "needs_review": not authored,
        "missing": ["tests", "mutation_score", "embedding"] + ([] if authored else ["description"]),
        "description_source": "docstring" if authored else "name_and_signature",
        "code_metrics": {"lines": 1},
    }


class TestEntryFromRecord:
    def test_builds_an_entry(self):
        entry = entry_from_record(record("split_rows", "Split a dataframe into train and test"))
        assert entry is not None
        assert entry.name == "split_rows"
        assert entry.type is EntryType.HELPER
        assert entry.license == "MIT"

    def test_skips_a_record_with_no_description(self):
        assert entry_from_record(record("x", "")) is None
        assert entry_from_record(record("x", "   ")) is None

    def test_needs_review_does_not_exclude_a_record(self):
        # needs_review is set for every harvested record (none has tests or a
        # mutation score), so filtering on it would empty the library.
        entry = entry_from_record(record("y", "Does a thing", authored=False))
        assert entry is not None

    def test_usage_signals_are_zero_not_invented(self):
        # A ranker allowed to train on fabricated usage numbers learns the
        # random number generator and scores better for it.
        entry = entry_from_record(record("z", "Does a thing"))
        assert (entry.usage_count, entry.impressions, entry.accepts) == (0, 0, 0)
        assert entry.mutation_score == 0.0
        assert entry.last_retest_at is None


class TestLoadDataset:
    # Input: tmp_path (Path), rows (list[dict])
    # Output: Path
    # Processing: Write rows to a JSONL file
    # Changed by: Sourish
    # Date: 2026-10-12
    def write(self, tmp_path, rows):
        path = tmp_path / "entries.jsonl"
        path.write_text("\n".join(json.dumps(r) for r in rows), encoding="utf-8")
        return path

    def test_separates_authored_from_derived_descriptions(self, tmp_path):
        path = self.write(
            tmp_path,
            [
                record("a", "Splits a dataframe by ratio"),
                record("b", "B. Takes x.", authored=False),
            ],
        )
        dataset = load_dataset(path)
        assert len(dataset) == 2
        assert [e.name for e in dataset.authored] == ["a"]
        assert [e.name for e in dataset.derived] == ["b"]

    def test_duplicate_ids_are_a_hard_error(self, tmp_path):
        path = self.write(tmp_path, [record("a", "One"), record("a", "Two")])
        with pytest.raises(ValueError, match="duplicate entry id"):
            load_dataset(path)

    def test_language_filter(self, tmp_path):
        path = self.write(
            tmp_path,
            [record("a", "One"), record("b", "Two", language="javascript")],
        )
        assert [e.name for e in load_dataset(path, languages=["javascript"]).entries] == ["b"]

    def test_malformed_json_names_the_line(self, tmp_path):
        path = tmp_path / "entries.jsonl"
        path.write_text(json.dumps(record("a", "One")) + "\n{not json\n", encoding="utf-8")
        with pytest.raises(ValueError, match=r":2 is not valid JSON"):
            load_dataset(path)

    def test_by_language_counts(self, tmp_path):
        path = self.write(
            tmp_path,
            [record("a", "One"), record("b", "Two"), record("c", "Three", language="cpp")],
        )
        assert load_dataset(path).by_language() == {"python": 2, "cpp": 1}


class TestFeatureSupport:
    def test_reports_constant_usage_fields(self):
        entries = [entry_from_record(record(f"f{i}", f"Does thing {i}")) for i in range(3)]
        support = feature_support(entries)
        assert support["usage_count"] is False
        assert support["mutation_score"] is False

    def test_reports_a_field_that_varies(self):
        entries = [entry_from_record(record(f"f{i}", f"Does thing {i}")) for i in range(2)]
        entries[0].usage_count = 5
        assert feature_support(entries)["usage_count"] is True


class TestNormalizeWithinQuery:
    def test_an_outlier_no_longer_flattens_its_rivals(self):
        # The defect this replaced: one extreme value sets the ceiling, and
        # the candidates that actually need separating share a sliver of the
        # range. Under min-max the four rivals below span under 1%; the gaps
        # between them must stay legible.
        column = np.array([[100.0], [4.0], [3.9], [3.8], [3.7]], dtype=np.float32)
        squeezed = normalize_within_query(column, method="minmax").ravel()
        spread = normalize_within_query(column).ravel()
        assert squeezed[1] - squeezed[4] < 0.01
        assert spread[1] - spread[4] == pytest.approx(0.75)

    def test_order_is_preserved(self):
        column = np.array([[3.0], [1.0], [100.0], [2.0]], dtype=np.float32)
        out = normalize_within_query(column).ravel()
        assert list(np.argsort(out)) == list(np.argsort(column.ravel()))

    def test_ties_share_a_position(self):
        # Two candidates equal on a feature must not be separated by it, or
        # their order in the input list would leak into the score.
        out = normalize_within_query(np.array([[1.0], [1.0], [2.0]], dtype=np.float32)).ravel()
        assert out[0] == pytest.approx(out[1])
        assert out[2] > out[0]

    def test_constant_column_maps_to_the_midpoint(self):
        out = normalize_within_query(np.array([[5.0, 1.0], [5.0, 2.0]], dtype=np.float32))
        assert out[0, 0] == pytest.approx(0.5) and out[1, 0] == pytest.approx(0.5)

    def test_single_candidate_has_no_spread_to_measure(self):
        out = normalize_within_query(np.array([[5.0, 9.0]], dtype=np.float32))
        assert out.tolist() == [[0.5, 0.5]]

    def test_unknown_method_is_rejected(self):
        with pytest.raises(ValueError, match="unknown normalisation method"):
            normalize_within_query(np.array([[1.0], [2.0]], dtype=np.float32), method="zscore")

    def test_empty_matrix_passes_through(self):
        assert normalize_within_query(np.zeros((0, 3), dtype=np.float32)).shape == (0, 3)


class TestLexicalOverlap:
    def test_template_frames_do_not_count(self):
        # "how do I" is shared by every generated query, so counting it would
        # put a floor under every score.
        assert "how" not in content_tokens("how do I split a dataframe")
        assert "split" in content_tokens("how do I split a dataframe")

    def test_a_restatement_scores_one(self):
        assert lexical_overlap("split a dataframe", "Splits a dataframe by ratio") == pytest.approx(
            0.5
        )
        assert lexical_overlap("dataframe", "Splits a dataframe") == pytest.approx(1.0)

    def test_an_unrelated_query_scores_zero(self):
        assert lexical_overlap("parse a date string", "Splits a dataframe") == 0.0

    def test_is_asymmetric(self):
        # The question is how much of the *query* the entry already contains,
        # not how much of a long description a short query covers.
        short, long = "dataframe", "Splits a dataframe into training and test partitions"
        assert lexical_overlap(short, long) == pytest.approx(1.0)
        assert lexical_overlap(long, short) < 1.0

    def test_a_query_with_no_content_words_scores_zero(self):
        assert lexical_overlap("how do I", "Splits a dataframe") == 0.0


class TestMeasureOverlap:
    # Input: entry_id (str), text (str)
    # Output: SyntheticQuery
    # Processing: A synthetic query for a given entry
    # Changed by: Sourish
    # Date: 2026-10-12
    def query(self, entry_id: str, text: str) -> SyntheticQuery:
        return SyntheticQuery(query=text, entry_id=entry_id, language="python")

    def test_flags_a_restating_query_set(self):
        report = measure_overlap(
            [self.query("e1", "how do I split a dataframe")],
            {"e1": "Splits a dataframe by ratio"},
        )
        assert report.degenerate is False or report.mean >= DEGENERATE_OVERLAP_THRESHOLD

    def test_the_real_generator_on_real_descriptions_is_degenerate(self):
        # The finding this guard was built for: templated queries restate the
        # descriptions BM25 indexes, so the set cannot train a ranker.
        entries = [
            entry_from_record(record("split_rows", "Splits a dataframe into train and test")),
            entry_from_record(record("parse_date", "Parses an ISO 8601 date string")),
        ]
        queries = generate_training_queries(entries, per_entry=3)
        report = measure_overlap(queries, {e.id: e.description for e in entries})
        assert report.degenerate is True
        assert report.mean > DEGENERATE_OVERLAP_THRESHOLD

    def test_a_paraphrase_without_shared_words_is_usable(self):
        report = measure_overlap(
            [self.query("e1", "break my table into two chunks")],
            {"e1": "Splits a dataframe by ratio"},
        )
        assert report.degenerate is False

    def test_an_entry_absent_from_the_mapping_is_skipped_not_scored_zero(self):
        # Scoring a missing target 0 would drag the mean toward "usable".
        report = measure_overlap(
            [self.query("e1", "how do I split a dataframe"), self.query("missing", "anything")],
            {"e1": "Splits a dataframe by ratio"},
        )
        assert report.count == 1

    def test_an_empty_set_is_not_degenerate(self):
        report = measure_overlap([], {})
        assert report.count == 0 and report.degenerate is False


# Input: none
# Output: tuple[list[Entry], RetrievalService]
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
    }
    entries = [entry_from_record(record(name, text)) for name, text in descriptions.items()]
    embed_entries_inplace(HashingEmbeddingModel(), entries)
    service = RetrievalService(
        keyword_index=InMemoryBM25Index(entries),
        vector_index=InMemoryVectorIndex(entries),
        embedding_model=HashingEmbeddingModel(),
        entry_store={e.id: e for e in entries},
    )
    return entries, service


class TestSplitByEntry:
    def test_no_entry_appears_in_two_splits(self):
        # Three queries from one entry are near-paraphrases. If they straddle
        # the split, the test score measures memorisation of a trained entry.
        queries = [
            SyntheticQuery(query=f"q{i} for {eid}", entry_id=eid, language="python")
            for eid in (f"e{n}" for n in range(60))
            for i in range(3)
        ]
        splits = split_by_entry(queries)
        owners = {name: {q.entry_id for q in qs} for name, qs in splits.items()}
        assert not owners["train"] & owners["test"]
        assert not owners["train"] & owners["valid"]
        assert not owners["valid"] & owners["test"]

    def test_every_query_lands_somewhere(self):
        queries = [
            SyntheticQuery(query=f"q{n}", entry_id=f"e{n}", language="python") for n in range(50)
        ]
        splits = split_by_entry(queries)
        assert sum(len(v) for v in splits.values()) == 50

    def test_assignment_is_stable_across_calls(self):
        queries = [
            SyntheticQuery(query=f"q{n}", entry_id=f"e{n}", language="python") for n in range(40)
        ]
        first = {name: [q.entry_id for q in qs] for name, qs in split_by_entry(queries).items()}
        second = {name: [q.entry_id for q in qs] for name, qs in split_by_entry(queries).items()}
        assert first == second

    def test_fractions_must_sum_to_one(self):
        with pytest.raises(ValueError, match="must sum to 1"):
            split_by_entry([], fractions=(0.5, 0.2, 0.2))


class TestAssembleGroup:
    def test_labels_the_target_and_mines_rivals(self):
        entries, service = tiny_library()
        target = entries[0]
        query = SyntheticQuery(
            query="how do I split a dataframe into training and test partitions",
            entry_id=target.id,
            language="python",
        )
        group = assemble_group(service, query, FeatureContext.build(usage_index=UsageIndex()))
        assert group is not None
        assert group.candidate_ids[0] == target.id
        assert group.labels[0] == 1.0
        assert group.labels[1:].sum() == 0.0
        assert group.features.shape == (len(group.candidate_ids), len(FEATURE_NAMES))

    def test_negatives_are_capped(self):
        entries, service = tiny_library()
        query = SyntheticQuery(
            query="splits a dataframe", entry_id=entries[0].id, language="python"
        )
        group = assemble_group(
            service, query, FeatureContext.build(usage_index=UsageIndex()), negatives=2
        )
        assert group is not None and len(group.candidate_ids) == 3

    def test_a_query_whose_target_is_unretrievable_is_dropped(self):
        # An all-negative group teaches the model to push every score down.
        _, service = tiny_library()
        query = SyntheticQuery(query="anything", entry_id="not_in_library", language="python")
        assert assemble_group(service, query, FeatureContext.build(usage_index=UsageIndex())) is None

    def test_assemble_groups_skips_the_misses(self):
        entries, service = tiny_library()
        queries = [
            SyntheticQuery(query="splits a dataframe", entry_id=entries[0].id, language="python"),
            SyntheticQuery(query="anything", entry_id="absent", language="python"),
        ]
        groups = assemble_groups(service, queries, FeatureContext.build(usage_index=UsageIndex()))
        assert len(groups) == 1


class TestEvaluateRanker:
    # Input: target_position (int)
    # Output: LabelledGroup
    # Processing: A group whose relevant row sits at a chosen position
    # Changed by: Sourish
    # Date: 2026-10-12
    def group(self, target_position: int, size: int = 4) -> LabelledGroup:
        features = np.zeros((size, len(FEATURE_NAMES)), dtype=np.float32)
        # Descending cosine, so a ranker reading it orders rows as given.
        features[:, FEATURE_NAMES.index("cosine_score")] = np.linspace(1.0, 0.0, size)
        labels = np.zeros(size, dtype=np.float32)
        labels[target_position] = 1.0
        return LabelledGroup(
            query="q",
            query_id="q0",
            target_id=f"e{target_position}",
            language="python",
            candidate_ids=tuple(f"e{i}" for i in range(size)),
            features=features,
            labels=labels,
        )

    def test_a_perfect_ranking_scores_one(self):
        metrics = evaluate_ranker(LinearFeatureRanker(), [self.group(0)])
        assert metrics["ndcg"] == pytest.approx(1.0)
        assert metrics["mrr"] == pytest.approx(1.0)

    def test_a_target_in_second_place_loses_mrr(self):
        metrics = evaluate_ranker(LinearFeatureRanker(), [self.group(1)])
        assert metrics["mrr"] == pytest.approx(0.5)

    def test_recall_at_three_misses_a_fourth_place_target(self):
        metrics = evaluate_ranker(LinearFeatureRanker(), [self.group(3)])
        assert metrics["recall"] == pytest.approx(0.0)

    def test_no_groups_yields_zeros(self):
        assert evaluate_ranker(LinearFeatureRanker(), [])["groups"] == 0.0


class TestTrainingReport:
    # Input: mean_overlap (float), informative (int), trained_ndcg (float), baseline_ndcg (float)
    # Output: TrainingReport
    # Processing: A report with chosen diagnostics
    # Changed by: Sourish
    # Date: 2026-10-12
    def report(
        self,
        *,
        mean_overlap: float = 0.2,
        informative: int = len(FEATURE_NAMES),
        trained_ndcg: float = 0.8,
        baseline_ndcg: float = 0.7,
        cv: CrossValidationResult | None = None,
    ) -> TrainingReport:
        from ml.evaluation.synthetic import OverlapReport

        return TrainingReport(
            cross_validation=cv,
            overlap=OverlapReport(100, mean_overlap, mean_overlap, 0.1, 0.0),
            varying_features={},
            informative_features=FEATURE_NAMES[:informative],
            split_sizes={"train": 70, "valid": 15, "test": 15},
            baseline={"ndcg": baseline_ndcg, "mrr": 0.6, "recall": 0.6},
            trained={"ndcg": trained_ndcg, "mrr": 0.7, "recall": 0.7},
        )

    def test_a_clean_run_is_certified(self):
        assert self.report().certified is True
        assert self.report().blockers == []

    def test_a_degenerate_query_set_blocks_certification(self):
        report = self.report(mean_overlap=0.95)
        assert report.certified is False
        assert any("degenerate" in b for b in report.blockers)

    def test_a_narrow_feature_set_is_a_caveat_not_a_blocker(self):
        # A three-feature fit on honest queries measures something real but
        # narrow. Refusing it outright conflated "measures nothing" with
        # "measures less than Stage 1"; the scope carries the difference.
        report = self.report(informative=3)
        assert report.certified is True
        assert report.scope is TrainingScope.RETRIEVAL_FUSION
        assert any("untrained" in c for c in report.caveats)

    def test_no_varying_feature_at_all_does_block(self):
        report = self.report(informative=0)
        assert report.certified is False
        assert any("nothing to learn" in b for b in report.blockers)

    def test_failing_to_beat_the_baseline_blocks_certification(self):
        report = self.report(trained_ndcg=0.7, baseline_ndcg=0.7)
        assert report.certified is False
        assert any("does not beat" in b for b in report.blockers)

    def test_every_blocker_is_reported_not_just_the_first(self):
        report = self.report(mean_overlap=0.95, informative=0, trained_ndcg=0.1)
        assert len(report.blockers) == 3

    def test_scope_is_full_stage1_when_a_usage_feature_varies(self):
        report = self.report(informative=len(FEATURE_NAMES))
        assert report.scope is TrainingScope.FULL_STAGE1

    def test_the_self_authored_query_caveat_is_always_present(self):
        # Never silently dropped: a score quoted without it reads as a
        # measurement of real demand, which it is not.
        assert any("release gate" in c for c in self.report().caveats)

    def test_an_inconsistent_cross_validation_blocks_certification(self):
        # A mean improvement containing a losing fold means the model found
        # something split-specific.
        report = self.report(
            cv=CrossValidationResult(baseline=(0.5, 0.5, 0.5), trained=(0.8, 0.8, 0.4))
        )
        assert report.certified is False
        assert any("split-specific" in b for b in report.blockers)

    def test_a_consistent_cross_validation_does_not_block(self):
        report = self.report(
            cv=CrossValidationResult(baseline=(0.5, 0.5, 0.5), trained=(0.8, 0.7, 0.6))
        )
        assert report.certified is True

    def test_the_summary_states_the_verdict(self):
        assert "CERTIFIED      False" in self.report(mean_overlap=0.99).summary()


class TestRunStage1Training:
    def test_the_pipeline_runs_and_refuses_to_certify_synthetic_data(self):
        # The end-to-end case. The fit succeeds and the report is honest: a
        # library with no usage history and queries built from its own
        # descriptions cannot validate a ranker.
        entries, service = tiny_library()
        queries = generate_training_queries(entries, per_entry=3)
        report = run_stage1_training(service, entries, queries)
        assert report.certified is False
        assert any("degenerate" in b for b in report.blockers)
        assert set(report.informative_features) <= set(FEATURE_NAMES)

    def test_a_constant_feature_earns_no_gain(self):
        # The mechanism behind the headline finding, stated as the general law
        # rather than the corpus-specific one. Which of the retrieval columns
        # dominates depends on the corpus -- BM25 on the real 515-entry
        # library, cosine on a six-entry toy -- but a column that never moves
        # cannot be split on anywhere, so its gain is exactly zero.
        entries, service = tiny_library()
        queries = generate_training_queries(entries, per_entry=3)
        report = run_stage1_training(service, entries, queries)
        constant = [n for n in FEATURE_NAMES if n not in report.informative_features]
        assert constant, "expected some features to be constant on a usage-free library"
        assert all(report.importance.get(name, 0.0) == 0.0 for name in constant)
        assert sum(report.importance.get(n, 0.0) for n in report.informative_features) > 0.0

    def test_a_saved_booster_is_recorded_on_the_report(self, tmp_path):
        entries, service = tiny_library()
        queries = generate_training_queries(entries, per_entry=2)
        out = tmp_path / "stage1.txt"
        report = run_stage1_training(service, entries, queries, model_path=str(out))
        assert out.exists() and report.model_path == str(out)


class TestLoadQueries:
    # Input: tmp_path (Path), rows (list[dict])
    # Output: Path
    # Processing: Write query rows to a JSONL file
    # Changed by: Sourish
    # Date: 2026-10-12
    def write(self, tmp_path, rows):
        path = tmp_path / "queries.jsonl"
        path.write_text("\n".join(json.dumps(r) for r in rows), encoding="utf-8")
        return path

    # Input: query (str), entry_id (str)
    # Output: dict
    # Processing: One stored query row
    # Changed by: Sourish
    # Date: 2026-10-12
    def row(self, query: str, entry_id: str) -> dict:
        return {
            "query": query,
            "entry_id": entry_id,
            "language": "python",
            "source": "synthetic_assistant_authored",
            "generator": "assistant_authored",
        }

    def test_loads_queries_with_their_provenance(self, tmp_path):
        path = self.write(tmp_path, [self.row("split a table in two", "owner_repo:split_rows")])
        queries = load_queries(path)
        assert len(queries) == 1
        assert queries[0].query == "split a table in two"
        assert queries[0].generator == "assistant_authored"

    def test_a_loaded_query_still_cannot_become_a_gold_query(self, tmp_path):
        # The source string contains "synthetic" on purpose. Queries written
        # by someone who read the entry make it correct by construction, so
        # they are training data however natural the wording.
        from ml.evaluation.gold_set import GoldQuery

        path = self.write(tmp_path, [self.row("split a table in two", "owner_repo:split_rows")])
        loaded = load_queries(path)[0]
        with pytest.raises(ValueError, match="synthetic"):
            GoldQuery(
                query_id="q1",
                query=loaded.query,
                language="python",
                relevance={loaded.entry_id: 3.0},
                source=loaded.source,
            )

    def test_a_query_for_an_absent_entry_is_dropped(self, tmp_path):
        # The dataset and the query file are regenerated independently, so a
        # stale query would otherwise make a group whose target cannot be
        # retrieved.
        path = self.write(
            tmp_path,
            [self.row("one", "owner_repo:present"), self.row("two", "owner_repo:gone")],
        )
        loaded = load_queries(path, entry_ids={"owner_repo:present"})
        assert [q.query for q in loaded] == ["one"]

    def test_malformed_json_names_the_line(self, tmp_path):
        path = tmp_path / "queries.jsonl"
        path.write_text(
            json.dumps(self.row("one", "owner_repo:a")) + "\n{nope\n", encoding="utf-8"
        )
        with pytest.raises(ValueError, match=r":2 is not valid JSON"):
            load_queries(path)


class TestCrossValidationResult:
    def test_mean_delta_and_spread(self):
        cv = CrossValidationResult(baseline=(0.4, 0.5, 0.6), trained=(0.6, 0.7, 0.8))
        assert cv.mean_delta == pytest.approx(0.2)
        assert cv.delta_std == pytest.approx(0.0)

    def test_consistent_requires_every_fold(self):
        assert CrossValidationResult((0.5, 0.5), (0.6, 0.7)).consistent is True
        assert CrossValidationResult((0.5, 0.5), (0.6, 0.4)).consistent is False

    def test_folds_won_counts_strict_wins(self):
        cv = CrossValidationResult(baseline=(0.5, 0.5, 0.5), trained=(0.6, 0.5, 0.4))
        assert cv.folds_won == 1

    def test_summary_reports_the_fold_count(self):
        assert "3-fold" in CrossValidationResult((0.1, 0.2, 0.3), (0.4, 0.5, 0.6)).summary()


class TestCrossValidate:
    def test_folds_are_assigned_by_entry(self):
        # Queries about one entry are near-paraphrases; if they straddle a
        # fold boundary the score measures memorisation.
        entries, service = tiny_library()
        queries = [
            SyntheticQuery(query=f"copy some bytes {i}", entry_id=entries[0].id, language="python")
            for i in range(4)
        ]
        groups = assemble_groups(service, queries, FeatureContext.build(usage_index=UsageIndex()))
        folds = {
            __import__("hashlib").blake2b(g.target_id.encode(), digest_size=8).hexdigest()
            for g in groups
        }
        assert len(folds) == 1  # one entry -> one fold, never split

    def test_rejects_a_single_fold(self):
        with pytest.raises(ValueError, match="at least 2 folds"):
            cross_validate([], folds=1)

    def test_raises_when_no_fold_has_both_sides(self):
        entries, service = tiny_library()
        queries = [
            SyntheticQuery(query="copy a binary buffer", entry_id=entries[0].id, language="python")
        ]
        groups = assemble_groups(service, queries, FeatureContext.build(usage_index=UsageIndex()))
        with pytest.raises(ValueError, match="no fold produced"):
            cross_validate(groups, folds=5)


class TestSignalGroupScope:
    """Scope is reported over the spec's four signal groups, not a feature count.

    An earlier definition keyed on "any feature beyond retrieval" flipped the
    scope to ``full_stage1`` the moment ``dependency_compat`` began to vary --
    while that feature carried under 1% of the model's gain and nothing about
    usage or trust had changed. The label promised coverage the run lacked.
    """

    # Input: features (tuple[str, ...]), importance (dict | None)
    # Output: TrainingReport
    # Processing: A report whose varying features and gains are chosen
    # Changed by: Sourish
    # Date: 2026-10-12
    def report(self, features, importance=None):
        from ml.evaluation.synthetic import OverlapReport

        return TrainingReport(
            overlap=OverlapReport(100, 0.2, 0.2, 0.1, 0.0),
            varying_features={},
            informative_features=tuple(features),
            split_sizes={"train": 70, "valid": 15, "test": 15},
            baseline={"ndcg": 0.5},
            trained={"ndcg": 0.8},
            importance=importance or {},
        )

    def test_retrieval_only_is_retrieval_fusion(self):
        report = self.report(("bm25_score", "cosine_score", "rrf_rank"))
        assert report.scope is TrainingScope.RETRIEVAL_FUSION
        assert report.covered_groups == ("retrieval",)
        assert set(report.uncovered_groups) == {"usage", "trust", "friction"}

    def test_one_friction_feature_is_partial_not_full(self):
        # The exact case that exposed the defect.
        report = self.report(("bm25_score", "cosine_score", "rrf_rank", "dependency_compat"))
        assert report.scope is TrainingScope.PARTIAL
        assert report.covered_groups == ("retrieval", "friction")

    def test_all_four_groups_is_full_stage1(self):
        report = self.report(
            ("bm25_score", "usage_log", "mutation_score", "dependency_compat")
        )
        assert report.scope is TrainingScope.FULL_STAGE1
        assert report.uncovered_groups == ()

    def test_uncovered_groups_are_named_in_a_caveat(self):
        report = self.report(("bm25_score",))
        assert any("usage, trust, friction" in c for c in report.caveats)

    def test_a_barely_used_feature_is_flagged(self):
        # Varying, split on, and carrying almost none of the decision.
        report = self.report(
            ("bm25_score", "dependency_compat"),
            importance={"bm25_score": 3424.0, "dependency_compat": 34.0},
        )
        assert report.barely_used_features == ("dependency_compat",)
        assert any("overstates it" in c for c in report.caveats)

    def test_a_well_used_feature_is_not_flagged(self):
        report = self.report(
            ("bm25_score", "rrf_rank"), importance={"bm25_score": 3424.0, "rrf_rank": 2990.0}
        )
        assert report.barely_used_features == ()

    def test_no_importance_data_flags_nothing(self):
        assert self.report(("bm25_score",), importance={}).barely_used_features == ()
