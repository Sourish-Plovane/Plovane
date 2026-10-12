"""The reusability suggestion model: scoring, saturation, and the five verdicts."""

from __future__ import annotations

import numpy as np
import pytest

from ml.ingest.extract import extract_python
from ml.reuse import (
    DUPLICATE_THRESHOLD,
    LibraryIndex,
    ReusabilityScore,
    ReusabilitySuggester,
    Verdict,
    documentation,
    generality,
    score_unit,
    token_value,
)
from ml.schemas import Entry, EntryType

GENERAL = '''
def chunked(items, size):
    """Split a sequence into consecutive chunks of at most size elements."""
    out = []
    for i in range(0, len(items), size):
        out.append(items[i:i + size])
    return out
'''

HARDCODED = '''
def load_config():
    """Read the config file."""
    import json
    with open("/home/sourish/ml/config.json") as fh:
        data = json.load(fh)
    return data
'''

UNDOCUMENTED = '''
def flatten(nested):
    out = []
    for group in nested:
        for item in group:
            out.append(item)
    return out
'''

COUPLED = '''
LIMIT = 10

def truncate(items):
    """Cut a list down to the configured limit."""
    out = items[:LIMIT]
    return out
'''


def entry(entry_id: str, vector, *, language: str = "python") -> Entry:
    return Entry(
        id=entry_id, type=EntryType.HELPER, name=entry_id, description="d",
        category="c", language=language,
        embedding=np.asarray(vector, dtype=np.float32),
    )


def unit(src: str):
    return extract_python(src)[0]


class TestGenerality:
    def test_parameterised_code_scores_high(self):
        assert generality("def f(a, b):\n    return a + b", param_count=2, annotated=2) == 1.0

    @pytest.mark.parametrize(
        "code",
        [
            'open("/home/me/project/data.csv")',
            'requests.get("https://internal.example.com/v1")',
            'connect("127.0.0.1:5432")',
            'headers = {"API_KEY": key}',
        ],
    )
    def test_project_specific_literals_are_penalised(self, code):
        assert generality(code, param_count=0, annotated=0) < 0.5


class TestDocumentation:
    @pytest.mark.parametrize(
        ("text", "expected"),
        [("", 0.0), ("Splits.", 0.3), ("Splits a frame by label.", 0.7)],
    )
    def test_credit_scales_with_substance(self, text, expected):
        assert documentation(text) == expected

    def test_a_full_sentence_scores_one(self):
        assert documentation("Splits a dataframe while preserving the label distribution.") == 1.0


class TestTokenValue:
    def test_unknown_is_neutral_not_zero(self):
        """Missing data is not evidence the code was cheap."""
        assert token_value(None) == 0.5

    def test_scales_then_saturates(self):
        assert token_value(0) == 0.0
        assert token_value(500) < token_value(2000) < token_value(4000)
        assert token_value(4000) == token_value(999_999) == 1.0


class TestBlockers:
    def test_coupled_code_is_blocked(self):
        score = score_unit(unit(COUPLED))
        assert "not self-contained" in score.blockers

    def test_hardcoded_code_is_blocked(self):
        score = score_unit(unit(HARDCODED))
        assert "tied to one project" in score.blockers

    def test_undocumented_code_is_blocked(self):
        score = score_unit(unit(UNDOCUMENTED))
        assert "no description" in score.blockers

    def test_good_code_has_no_blockers(self):
        assert score_unit(unit(GENERAL), tokens_spent=2500).blockers == ()

    def test_blocked_score_is_capped_below_the_publish_bar(self):
        """A neutral token value and a line bonus must not carry a blocked unit over."""
        from ml.reuse.suggest import PUBLISH_THRESHOLD

        for src in (HARDCODED, UNDOCUMENTED, COUPLED):
            assert score_unit(unit(src)).score < PUBLISH_THRESHOLD

    def test_capped_not_zeroed_so_it_still_ranks(self):
        """Two blocked candidates are not equally bad."""
        worse = ReusabilityScore(
            self_contained=False, has_description=False, generality=0.0,
            documentation=0.0, token_value=0.0, lines=200,
        )
        better = ReusabilityScore(
            self_contained=False, has_description=True, generality=0.9,
            documentation=1.0, token_value=1.0, lines=10,
        )
        assert 0.0 == worse.score < better.score <= ReusabilityScore.BLOCKED_CEILING


class TestLibraryIndex:
    def test_finds_the_duplicate(self):
        index = LibraryIndex([entry("a", [1, 0, 0]), entry("b", [0, 1, 0])])
        report = index.compare(np.array([1.0, 0.0, 0.02]))
        assert report.best.entry.id == "a"
        assert report.has_duplicate

    def test_novel_code_has_no_duplicate(self):
        index = LibraryIndex([entry("a", [1, 0, 0])])
        assert not index.compare(np.array([0.0, 0.0, 1.0])).has_duplicate

    def test_counts_the_crowded_neighbourhood(self):
        index = LibraryIndex(
            [entry("a", [1, 0, 0]), entry("a2", [0.99, 0.1, 0]), entry("a3", [0.97, 0.2, 0]),
             entry("far", [0, 1, 0])]
        )
        assert index.compare(np.array([1.0, 0.0, 0.0])).saturation == 3

    def test_other_languages_are_not_duplicates(self):
        """A Python helper cannot replace a JavaScript one in a project."""
        index = LibraryIndex([entry("js", [1, 0, 0], language="javascript")])
        assert not index.compare(np.array([1.0, 0.0, 0.0]), language="python").has_duplicate

    def test_exclude_ids_skips_the_entry_itself(self):
        index = LibraryIndex([entry("self", [1, 0, 0]), entry("other", [0, 1, 0])])
        report = index.compare(np.array([1.0, 0.0, 0.0]), exclude_ids=("self",))
        assert report.best.entry.id == "other"

    def test_entries_without_embeddings_are_skipped_not_rejected(self):
        plain = Entry(
            id="x", type=EntryType.HELPER, name="x", description="d",
            category="c", language="python",
        )
        index = LibraryIndex([plain, entry("a", [1, 0, 0])])
        assert len(index) == 1

    def test_empty_library(self):
        report = LibraryIndex([]).compare(np.array([1.0, 0.0]))
        assert report.best is None and report.saturation == 0


class TestVerdicts:
    @pytest.fixture
    def suggester(self) -> ReusabilitySuggester:
        return ReusabilitySuggester(library=LibraryIndex([entry("existing", [1, 0, 0])]))

    def test_publish_new_when_nothing_matches(self, suggester):
        s = suggester.judge(unit(GENERAL), np.array([0.0, 0.0, 1.0]), tokens_spent=2500)
        assert s.verdict is Verdict.PUBLISH_NEW
        assert s.should_prompt

    def test_reuse_existing_when_a_duplicate_exists(self, suggester):
        s = suggester.judge(unit(GENERAL), np.array([1.0, 0.0, 0.0]), tokens_spent=2500)
        assert s.verdict is Verdict.REUSE_EXISTING
        assert s.existing.entry.id == "existing"

    def test_improve_existing_when_the_candidate_scores_better(self, suggester):
        s = suggester.judge(
            unit(GENERAL), np.array([1.0, 0.0, 0.0]), tokens_spent=2500, existing_score=0.30
        )
        assert s.verdict is Verdict.IMPROVE_EXISTING

    def test_never_improves_on_an_unknown_score(self, suggester):
        """Replacing a tested entry on a guess is the costly error here."""
        s = suggester.judge(unit(GENERAL), np.array([1.0, 0.0, 0.0]), tokens_spent=2500)
        assert s.verdict is Verdict.REUSE_EXISTING

    def test_a_narrow_margin_is_not_an_improvement(self, suggester):
        score = score_unit(unit(GENERAL), tokens_spent=2500).score
        s = suggester.judge(
            unit(GENERAL), np.array([1.0, 0.0, 0.0]), tokens_spent=2500,
            existing_score=score - 0.01,
        )
        assert s.verdict is Verdict.REUSE_EXISTING

    def test_skip_saturated_when_the_neighbourhood_is_crowded(self):
        crowded = LibraryIndex(
            [entry(f"dup{i}", [1, 0.01 * i, 0]) for i in range(5)]
        )
        suggester = ReusabilitySuggester(library=crowded, saturation_limit=3)
        s = suggester.judge(unit(GENERAL), np.array([1.0, 0.0, 0.0]), tokens_spent=2500)
        assert s.verdict is Verdict.SKIP_SATURATED
        assert not s.should_prompt
        assert s.saturation >= 3

    def test_saturation_outranks_an_exact_duplicate(self):
        """Crowded means stay quiet, twin or no twin."""
        crowded = LibraryIndex([entry(f"dup{i}", [1, 0.01 * i, 0]) for i in range(5)])
        s = ReusabilitySuggester(library=crowded, saturation_limit=2).judge(
            unit(GENERAL), np.array([1.0, 0.0, 0.0]), tokens_spent=2500
        )
        assert s.verdict is Verdict.SKIP_SATURATED

    @pytest.mark.parametrize("src", [HARDCODED, UNDOCUMENTED, COUPLED])
    def test_skip_not_worth_names_the_blocker(self, suggester, src):
        s = suggester.judge(unit(src), np.array([0.0, 0.0, 1.0]))
        assert s.verdict is Verdict.SKIP_NOT_WORTH
        assert not s.should_prompt
        assert s.reason

    def test_no_embedding_means_no_duplicate_check(self, suggester):
        s = suggester.judge(unit(GENERAL), None, tokens_spent=2500)
        assert s.verdict is Verdict.PUBLISH_NEW

    def test_as_dict_is_serialisable(self, suggester):
        import json

        s = suggester.judge(unit(GENERAL), np.array([1.0, 0.0, 0.0]), tokens_spent=2500)
        payload = json.loads(json.dumps(s.as_dict()))
        assert payload["verdict"] == "reuse_existing"
        assert payload["existing_id"] == "existing"

    def test_duplicate_threshold_is_strict(self):
        """A false duplicate silently cancels a suggestion the user wanted."""
        assert DUPLICATE_THRESHOLD >= 0.9
