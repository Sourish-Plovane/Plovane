"""Synthetic training queries, and the block that keeps them out of evaluation."""

from __future__ import annotations

import pytest

from ml.evaluation.gold_set import GoldQuery
from ml.evaluation.labelling import Label, gold_set_from_labels
from ml.evaluation.synthetic import (
    QUERY_PROVENANCE_SYNTHETIC,
    DeepSeekQueryGenerator,
    TemplatedQueryGenerator,
    action_phrase,
    generate_training_queries,
)
from ml.schemas import Entry, EntryType


def entry(entry_id: str, name: str, description: str, language: str = "python") -> Entry:
    return Entry(
        id=entry_id, type=EntryType.HELPER, name=name, description=description,
        category="data-processing", language=language, signature="(a)",
    )


class TestEvaluationIsBlocked:
    """The restriction is in the type, not in a comment."""

    def test_gold_query_refuses_a_synthetic_source(self):
        with pytest.raises(ValueError, match="synthetic"):
            GoldQuery(
                query_id="g1", query="q", language="python",
                relevance={"a": 3.0}, source=QUERY_PROVENANCE_SYNTHETIC,
            )

    def test_refusal_is_case_insensitive_and_matches_substrings(self):
        for source in ("Synthetic", "generated:SYNTHETIC", "synthetic/deepseek"):
            with pytest.raises(ValueError, match="synthetic"):
                GoldQuery(
                    query_id="g1", query="q", language="python",
                    relevance={"a": 3.0}, source=source,
                )

    def test_a_real_source_is_accepted(self):
        assert GoldQuery(
            query_id="g1", query="q", language="python", relevance={"a": 3.0},
        ).source == "hand-labelled"

    def test_labelling_cannot_launder_a_synthetic_query_into_a_gold_set(self):
        """gold_set_from_labels builds GoldQuery, so the guard applies there too."""
        with pytest.raises(ValueError, match="synthetic"):
            gold_set_from_labels(
                [
                    Label(
                        query="q", language="python", relevance={"a": 3.0},
                        labeller=QUERY_PROVENANCE_SYNTHETIC,
                    )
                ]
            )

    def test_every_generated_query_carries_the_marker(self):
        queries = generate_training_queries([entry("a", "split_frame", "Splits a frame")])
        assert queries
        assert all(q.source == QUERY_PROVENANCE_SYNTHETIC for q in queries)


class TestActionPhrase:
    def test_third_person_verb_becomes_base_form(self):
        """Docstrings are third person; a request frame is not."""
        phrase, is_verb = action_phrase(entry("a", "f", "Splits a dataframe by label"))
        assert phrase == "split a dataframe by label"
        assert is_verb

    def test_returns_prefix_yields_a_noun_phrase(self):
        """"how do I a deep copy" is what treating this as a verb produces."""
        phrase, is_verb = action_phrase(entry("a", "f", "Returns a deep copy of an object"))
        assert phrase == "a deep copy of an object"
        assert not is_verb

    def test_article_leading_description_is_a_noun_phrase(self):
        phrase, is_verb = action_phrase(entry("a", "f", "A sampler that draws each class"))
        assert not is_verb
        assert phrase.startswith("a sampler")

    def test_falls_back_to_the_identifier(self):
        phrase, _ = action_phrase(entry("a", "stratified_split", ""))
        assert phrase == "stratified split"

    def test_no_name_and_no_description(self):
        assert action_phrase(entry("a", "", "")) == ("", False)

    def test_trailing_full_stop_is_dropped(self):
        phrase, _ = action_phrase(entry("a", "f", "Splits a frame."))
        assert not phrase.endswith(".")


class TestTemplatedQueryGenerator:
    def test_produces_request_shaped_queries(self):
        queries = TemplatedQueryGenerator().generate(
            entry("a", "stratified_split", "Splits a dataframe by label"), count=4
        )
        assert len(queries) == 4
        assert all(q.entry_id == "a" for q in queries)
        assert any("how do I" in q.query for q in queries)

    def test_verb_frames_are_not_applied_to_noun_phrases(self):
        queries = TemplatedQueryGenerator().generate(
            entry("a", "cloneDeep", "Returns a deep copy of an object"), count=4
        )
        assert not any(q.query.startswith("how do I a ") for q in queries)

    def test_is_deterministic_per_entry(self):
        target = entry("a", "f", "Splits a frame by label")
        first = [q.query for q in TemplatedQueryGenerator().generate(target, count=3)]
        second = [q.query for q in TemplatedQueryGenerator().generate(target, count=3)]
        assert first == second

    def test_phrasing_varies_across_entries(self):
        """Seeded by entry id, so a training set is not one sentence shape.

        Asserted across many ids rather than between two: with four templates,
        any given pair collides a quarter of the time, so comparing two was a
        coin flip dressed up as a property.
        """
        gen = TemplatedQueryGenerator()
        phrasings = {
            gen.generate(entry(f"id{i}", "f", "Splits a frame by label"), count=1)[0].query
            for i in range(30)
        }
        assert len(phrasings) > 1

    def test_queries_are_unique_within_an_entry(self):
        queries = TemplatedQueryGenerator().generate(
            entry("a", "f", "Splits a frame"), count=10
        )
        assert len(queries) == len({q.query for q in queries})

    def test_an_undescribable_entry_yields_nothing(self):
        assert TemplatedQueryGenerator().generate(entry("a", "", "")) == []

    def test_needs_no_credential(self):
        assert TemplatedQueryGenerator().generate(entry("a", "f", "Splits a frame"))


class TestDeepSeekQueryGenerator:
    def test_raises_without_a_client(self):
        with pytest.raises(RuntimeError, match="no client"):
            DeepSeekQueryGenerator().generate(entry("a", "f", "Splits a frame"))

    def test_prompt_forbids_the_function_name(self):
        """A query containing the identifier is answerable by keyword search."""
        prompt = DeepSeekQueryGenerator().build_prompt(
            entry("a", "stratified_split", "Splits a frame"), 3
        )
        assert "Do NOT use the function's name" in prompt
        assert "stratified_split" in prompt

    def test_parses_a_numbered_reply(self):
        class Stub:
            def complete(self, prompt):
                return "1. split data by class\n2. keep label balance\n- third one\n"

        queries = DeepSeekQueryGenerator(Stub()).generate(
            entry("a", "f", "Splits a frame"), count=3
        )
        assert [q.query for q in queries] == [
            "split data by class", "keep label balance", "third one",
        ]
        assert all(q.generator == "deepseek" for q in queries)
        assert all(q.source == QUERY_PROVENANCE_SYNTHETIC for q in queries)

    def test_respects_the_count(self):
        class Stub:
            def complete(self, prompt):
                return "\n".join(f"query {i}" for i in range(10))

        assert len(DeepSeekQueryGenerator(Stub()).generate(entry("a", "f", "d"), count=2)) == 2


class TestGenerateTrainingQueries:
    def test_covers_every_entry(self):
        entries = [entry(f"e{i}", f"name_{i}", f"Splits thing {i}") for i in range(5)]
        queries = generate_training_queries(entries, per_entry=2)
        assert len({q.entry_id for q in queries}) == 5

    def test_records_are_serialisable(self):
        import json

        query = generate_training_queries([entry("a", "f", "Splits a frame")])[0]
        payload = json.loads(json.dumps(query.as_dict()))
        assert payload["source"] == QUERY_PROVENANCE_SYNTHETIC
        assert payload["entry_id"] == "a"
