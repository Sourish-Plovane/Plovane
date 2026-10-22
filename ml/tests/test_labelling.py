"""Gold-set labelling tooling: shortlists, the LLM labeller, verification."""

from __future__ import annotations

import pytest

from ml.evaluation.labelling import (
    GRADE_EXACT,
    GRADE_PARTIAL,
    Label,
    LLMLabeller,
    Shortlist,
    agreement,
    build_shortlists,
    gold_set_from_labels,
    shortlist_recall,
    verification_sample,
)
from ml.schemas import Candidate, Entry, EntryType, ProjectContext, Request


def candidate(entry_id: str, name: str, description: str, language: str = "python") -> Candidate:
    return Candidate(
        entry=Entry(
            id=entry_id, type=EntryType.HELPER, name=name, description=description,
            category="data-processing", language=language, signature="(a)",
        )
    )


@pytest.fixture
def shortlist() -> Shortlist:
    return Shortlist(
        query="split a dataset keeping class balance",
        language="python",
        candidates=(
            candidate("a", "random_split", "Splits a frame at random"),
            candidate("b", "stratified_split", "Splits preserving label distribution"),
        ),
    )


def label(query: str, language: str, relevance: dict[str, float], **kwargs) -> Label:
    return Label(
        query=query, language=language, relevance=relevance,
        labeller=kwargs.pop("labeller", "llm"), **kwargs,
    )


class TestShortlist:
    def test_reports_candidate_order(self, shortlist):
        assert shortlist.candidate_ids == ("a", "b")

    def test_rank_is_one_indexed(self, shortlist):
        assert shortlist.rank_of("a") == 1
        assert shortlist.rank_of("b") == 2

    def test_absent_entry_has_no_rank(self, shortlist):
        assert shortlist.rank_of("ghost") is None


class TestBuildShortlists:
    def test_retrieves_per_query_and_caps_the_size(self):
        class FakeRetrieval:
            def retrieve(self, request):
                class Result:
                    candidates = [candidate(f"e{i}", f"n{i}", "d") for i in range(50)]

                return Result()

        requests = [
            Request(query="q1", project_context=ProjectContext(languages=("python",))),
            Request(query="q2", project_context=ProjectContext(languages=("cuda",))),
        ]
        lists = build_shortlists(requests, FakeRetrieval(), size=5)
        assert len(lists) == 2
        assert len(lists[0].candidates) == 5
        assert lists[0].language == "python"
        assert lists[1].language == "cuda"

    def test_language_is_unknown_without_project_context(self):
        class FakeRetrieval:
            def retrieve(self, request):
                class Result:
                    candidates = []

                return Result()

        lists = build_shortlists([Request(query="q")], FakeRetrieval())
        assert lists[0].language == "unknown"


class TestLLMLabeller:
    def test_prompt_offers_a_way_out(self, shortlist):
        """A model asked to pick from a list will pick from the list."""
        prompt = LLMLabeller().build_prompt(shortlist)
        assert "NONE" in prompt
        assert "stratified_split" in prompt
        assert shortlist.query in prompt

    def test_parses_exact_and_partial(self, shortlist):
        parsed = LLMLabeller().parse_reply(shortlist, "EXACT=b PARTIAL=a")
        assert parsed.relevance == {"b": GRADE_EXACT, "a": GRADE_PARTIAL}
        assert parsed.chosen_rank == 2
        assert not parsed.not_in_shortlist

    def test_discards_an_id_that_was_not_offered(self, shortlist):
        """A hallucinated id would write a gold label pointing at nothing."""
        parsed = LLMLabeller().parse_reply(shortlist, "EXACT=ghost PARTIAL=NONE")
        assert parsed.relevance == {}
        assert parsed.not_in_shortlist

    def test_none_is_recorded_as_a_miss(self, shortlist):
        parsed = LLMLabeller().parse_reply(shortlist, "EXACT=NONE PARTIAL=NONE")
        assert parsed.not_in_shortlist

    def test_an_exact_grade_is_not_downgraded_by_a_later_partial(self, shortlist):
        parsed = LLMLabeller().parse_reply(shortlist, "EXACT=b PARTIAL=b")
        assert parsed.relevance["b"] == GRADE_EXACT

    def test_raises_without_a_client(self, shortlist):
        """A guessed label is worse than a missing one."""
        with pytest.raises(RuntimeError, match="no client"):
            LLMLabeller().label(shortlist)

    def test_uses_the_client(self, shortlist):
        class Stub:
            def complete(self, prompt):
                return "EXACT=b"

        assert LLMLabeller(Stub()).label(shortlist).relevance == {"b": GRADE_EXACT}


class TestShortlistRecall:
    def test_counts_misses_as_retrieval_failures(self):
        labels = [
            label("q1", "python", {"a": 3.0}),
            label("q2", "python", {}, not_in_shortlist=True),
            label("q3", "cuda", {"b": 3.0}),
        ]
        report = shortlist_recall(labels)
        assert report["queries"] == 3
        assert report["misses"] == 1
        assert report["recall"] == pytest.approx(2 / 3, abs=1e-4)

    def test_reports_per_language(self):
        labels = [
            label("q1", "python", {"a": 3.0}),
            label("q2", "cuda", {}, not_in_shortlist=True),
        ]
        report = shortlist_recall(labels)
        assert report["by_language"]["cuda"]["recall"] == 0.0
        assert report["by_language"]["python"]["recall"] == 1.0

    def test_empty_input(self):
        assert shortlist_recall([])["queries"] == 0


class TestGoldSetFromLabels:
    def test_builds_gold_queries(self):
        gold = gold_set_from_labels([label("q1", "python", {"a": 3.0})])
        assert len(gold) == 1
        assert gold.queries[0].relevance == {"a": 3.0}
        assert gold.queries[0].source.startswith("labelled:")

    def test_drops_labels_with_no_relevant_entry(self):
        """A query with no correct answer cannot contribute to NDCG."""
        labels = [
            label("q1", "python", {"a": 3.0}),
            label("q2", "python", {}, not_in_shortlist=True),
        ]
        assert len(gold_set_from_labels(labels)) == 1

    def test_query_ids_are_deterministic(self):
        first = gold_set_from_labels([label("q1", "python", {"a": 3.0})])
        second = gold_set_from_labels([label("q1", "python", {"a": 3.0})])
        assert first.queries[0].query_id == second.queries[0].query_id

    def test_splits_are_assigned(self):
        labels = [label(f"q{i}", "python", {"a": 3.0}) for i in range(60)]
        gold = gold_set_from_labels(labels)
        assert gold.holdout
        assert len(gold.split("train")) > 0


class TestVerificationSample:
    def test_is_stratified_across_languages(self):
        """A random sample would be mostly Python and say nothing about CUDA."""
        labels = [label(f"py{i}", "python", {"a": 3.0}) for i in range(100)]
        labels += [label(f"cu{i}", "cuda", {"a": 3.0}) for i in range(3)]
        sample = verification_sample(labels, per_language=5)
        languages = {lab.language for lab in sample}
        assert "cuda" in languages
        assert sum(1 for lab in sample if lab.language == "python") == 5
        assert sum(1 for lab in sample if lab.language == "cuda") == 3

    def test_is_deterministic(self):
        """Two people checking "the sample" must check the same one."""
        labels = [label(f"q{i}", "python", {"a": 3.0}) for i in range(50)]
        first = [lab.query for lab in verification_sample(labels, per_language=6)]
        second = [lab.query for lab in verification_sample(labels, per_language=6)]
        assert first == second

    def test_ignores_languages_outside_the_mvp_set(self):
        labels = [label("q1", "haskell", {"a": 3.0}), label("q2", "python", {"a": 3.0})]
        sample = verification_sample(labels)
        assert {lab.language for lab in sample} == {"python"}

    def test_empty_input(self):
        assert verification_sample([]) == []


class TestAgreement:
    def test_perfect_agreement(self):
        machine = [label("q1", "python", {"a": 3.0}), label("q2", "cuda", {"b": 3.0})]
        human = [label("q1", "python", {"a": 3.0}, labeller="human")]
        report = agreement(machine, human)
        assert report["compared"] == 1
        assert report["agreement"] == 1.0
        assert report["disputes"] == []

    def test_disagreement_is_itemised(self):
        machine = [label("q1", "python", {"a": 3.0})]
        human = [label("q1", "python", {"b": 3.0}, labeller="human")]
        report = agreement(machine, human)
        assert report["agreement"] == 0.0
        assert report["disputes"][0]["labeller"] == "a"
        assert report["disputes"][0]["human"] == "b"

    def test_partial_grades_do_not_count_as_disagreement(self):
        """Two careful people can differ on a partial and both be reasonable."""
        machine = [label("q1", "python", {"a": 3.0, "b": 1.0})]
        human = [label("q1", "python", {"a": 3.0}, labeller="human")]
        assert agreement(machine, human)["agreement"] == 1.0

    def test_a_shared_miss_counts_as_agreement(self):
        machine = [label("q1", "python", {}, not_in_shortlist=True)]
        human = [label("q1", "python", {}, labeller="human", not_in_shortlist=True)]
        assert agreement(machine, human)["agreement"] == 1.0

    def test_unmatched_human_labels_are_skipped(self):
        report = agreement([], [label("q1", "python", {"a": 3.0}, labeller="human")])
        assert report["compared"] == 0
        assert report["agreement"] == 0.0
