"""Tests for the handbook §5.2 and §6 wire shapes B06 produces.

B06 serves across §5.2 and reads across §6. These tests pin what the ML
code emits on both boundaries, so changing either shape under a handbook
revision is a single-file edit with the tests to anchor it.
"""

from __future__ import annotations

import pytest

from ml.schemas import (
    Candidate,
    DependencyCompat,
    Entry,
    EntryType,
    Response,
    SlimCard,
    Visibility,
)
from ml.schemas_library import (
    LibraryBlock,
    LibraryGetBlocksRequest,
    LibrarySearchRequest,
    LibrarySearchResponse,
)


# Input: name (str), language (str)
# Output: Entry
# Processing: A library entry with every attribution field set
# Changed by: Claude Code
# Date: 2026-10-13
def entry(name: str = "parse_date", language: str = "python") -> Entry:
    return Entry(
        id=f"owner_repo:{name}",
        type=EntryType.HELPER,
        name=name,
        description="Parses an ISO 8601 date string into a datetime",
        category="date",
        language=language,
        signature="(text: str) -> datetime",
        code="def parse_date(text):\n    ...",
        license="BSD-3-Clause",
        source_url="https://github.com/owner/repo",
        source_permalink="https://github.com/owner/repo/blob/deadbeef/src/dates.py#L10-L18",
        dependencies=("python-dateutil",),
        visibility=Visibility.PUBLIC,
    )


class TestSlimCardCarriesEveryField:
    """The §5.2 proposed additions all travel from Entry → SlimCard → wire."""

    def test_from_candidate_populates_the_handbook_block_fields(self):
        card = SlimCard.from_candidate(
            Candidate(entry=entry()),
            score=0.87,
            score_is_calibrated=True,
        )
        assert card.code.startswith("def parse_date")
        assert card.license == "BSD-3-Clause"
        assert card.source_url == "https://github.com/owner/repo"
        assert card.source_permalink.endswith("#L10-L18")
        assert card.signature == "(text: str) -> datetime"
        assert card.dependencies == ("python-dateutil",)
        assert card.score_is_calibrated is True

    def test_the_handbook_block_uses_block_id_not_id(self):
        # A client reading the handbook consumes ``block_id``, not ``id``.
        card = SlimCard.from_candidate(Candidate(entry=entry()), score=0.4)
        block = card.to_handbook_block()
        assert "block_id" in block and "id" not in block

    def test_the_handbook_block_has_the_six_required_fields(self):
        # The handbook §5.2 names these as required: adding to the list is
        # safe (§3.5 rule 1), dropping or renaming any of them is not.
        block = SlimCard.from_candidate(Candidate(entry=entry()), 0.5).to_handbook_block()
        for required in ("block_id", "name", "language", "code", "comments", "dependencies"):
            assert required in block, f"missing: {required}"

    def test_the_handbook_block_carries_the_proposed_additions(self):
        # These are the fields I'd add to the handbook draft. If the review
        # removes any, this test is the one place the removal lands.
        block = SlimCard.from_candidate(Candidate(entry=entry()), 0.5).to_handbook_block()
        for field_name in ("score", "score_is_calibrated", "license"):
            assert field_name in block, f"missing proposed field: {field_name}"

    def test_source_url_is_present_when_the_entry_has_it(self):
        block = SlimCard.from_candidate(Candidate(entry=entry()), 0.5).to_handbook_block()
        assert block["source_url"] == "https://github.com/owner/repo"

    def test_source_url_is_omitted_when_the_entry_lacks_it(self):
        # Legacy entries may lack provenance (none of the harvested ones do,
        # but a hand-authored entry might). Absent is absent, not an empty
        # string that reads as "I was credited to no one".
        e = entry()
        card = SlimCard.from_candidate(
            Candidate(entry=Entry(
                id=e.id, type=e.type, name=e.name, description=e.description,
                category=e.category, language=e.language, code=e.code,
                license=e.license, dependencies=e.dependencies,
            )), 0.5)
        assert "source_url" not in card.to_handbook_block()

    def test_comments_mirrors_the_description(self):
        # The handbook uses the field name ``comments``; my Entry stores it
        # as ``description``. The mapping has to be stable or a UI shows an
        # empty description.
        block = SlimCard.from_candidate(Candidate(entry=entry()), 0.5).to_handbook_block()
        assert block["comments"] == "Parses an ISO 8601 date string into a datetime"

    def test_score_rounds_to_four_places(self):
        # Logistic scores run deep. Floating-point churn in the fourth
        # decimal reads as a legitimate change to a UI's "did the ranking
        # move?" diff. Rounded to four is enough resolution to order 10,000
        # entries and no more.
        card = SlimCard.from_candidate(Candidate(entry=entry()), score=0.812345)
        assert card.score == 0.8123


class TestHandbookResponseEnvelope:
    """§4.1 wrapping §5.2: an uncalibrated response is still a successful one."""

    # Input: cards (tuple), reason (str | None), cutoff_enforced (bool)
    # Output: Response
    # Processing: Build a Response with chosen state
    # Changed by: Claude Code
    # Date: 2026-10-13
    def response(self, cards=(), reason=None, cutoff_enforced=True):
        return Response(
            query_id="q_123", results=cards, reason=reason, cutoff_enforced=cutoff_enforced
        )

    def test_the_envelope_is_ok_true_on_a_successful_recommendation(self):
        # The handbook §16.3 rule: a domain outcome (nothing matched well
        # enough) is an operation result, not a transport failure.
        payload = self.response(reason="no entry above the relevance cutoff").to_handbook_response()
        assert payload["ok"] is True
        assert payload["error"] is None
        assert payload["data"]["recommendations"] == []
        assert payload["data"]["reason"] == "no entry above the relevance cutoff"

    def test_cutoff_enforced_is_carried_through(self):
        # The pipeline reports whether the cutoff was applied; the client has
        # to see it or it treats an uncalibrated 0.9 as "90% likely to accept".
        payload = self.response(cutoff_enforced=False).to_handbook_response()
        assert payload["data"]["cutoff_enforced"] is False

    def test_recommendations_is_an_array_not_absent(self):
        # §3.1: "an empty array represents a known empty collection, not a
        # missing value." The field is always present even when empty.
        payload = self.response().to_handbook_response()
        assert "recommendations" in payload["data"]
        assert payload["data"]["recommendations"] == []

    def test_results_round_trip_through_the_handbook_shape(self):
        card = SlimCard.from_candidate(Candidate(entry=entry()), 0.9, score_is_calibrated=True)
        payload = self.response(cards=(card,)).to_handbook_response()
        assert len(payload["data"]["recommendations"]) == 1
        block = payload["data"]["recommendations"][0]
        assert block["block_id"] == card.id
        assert block["score"] == 0.9
        assert block["score_is_calibrated"] is True


class TestLibrarySearchRequest:
    """§6 search: BM12 → library access."""

    def test_only_required_fields_appear_when_no_filter_is_set(self):
        # A payload that lists every filter as ``null`` or ``[]`` is more
        # expensive to transmit and more work for B07 to interpret. §3.1 rule
        # 7: an unknown optional value should be omitted.
        payload = LibrarySearchRequest(
            keyword_text="csv", query_vector=(0.1, 0.2), limit=100
        ).to_handbook_payload()
        assert set(payload.keys()) == {"keyword_text", "query_vector", "limit", "drop_in_only"}

    def test_filters_are_present_only_when_requested(self):
        payload = LibrarySearchRequest(
            keyword_text="csv",
            query_vector=(0.1, 0.2),
            languages=("python",),
            installed_dependencies=("pandas==2.1",),
            drop_in_only=True,
        ).to_handbook_payload()
        assert payload["languages"] == ["python"]
        assert payload["installed_dependencies"] == ["pandas==2.1"]
        assert payload["drop_in_only"] is True

    def test_installed_dependencies_accepts_name_or_name_version(self):
        # §5.1 ``tech_stack`` was undefined on this point. The §6 side has to
        # accept both so a project with torch and a project with torch==2.1
        # both make sense.
        plain = LibrarySearchRequest(
            keyword_text="x", query_vector=(0.0,), installed_dependencies=("torch",)
        ).to_handbook_payload()
        pinned = LibrarySearchRequest(
            keyword_text="x", query_vector=(0.0,), installed_dependencies=("torch==2.1",)
        ).to_handbook_payload()
        assert plain["installed_dependencies"] == ["torch"]
        assert pinned["installed_dependencies"] == ["torch==2.1"]

    def test_entry_types_use_the_enum_value_not_the_name(self):
        payload = LibrarySearchRequest(
            keyword_text="x", query_vector=(0.0,), entry_types=(EntryType.TEMPLATE,)
        ).to_handbook_payload()
        assert payload["entry_types"] == ["template"]


class TestLibraryBlockCarriesRankingSignals:
    """§6 response blocks carry fields §5.2's blocks do not."""

    def test_ranking_signals_live_in_their_own_sub_object(self):
        # Grouping keeps the §6 block legible and lets B06 pull one slice
        # off instead of filtering keys. The §5.2 projection drops the
        # sub-object entirely.
        block = LibraryBlock(
            block_id="b", name="n", type=EntryType.HELPER, language="python",
            category="x", description="d", code="c", license="MIT",
            bm25_score=12.4, bm25_rank=3, rrf_score=0.08, rrf_rank=2,
            dependency_compat=DependencyCompat.NEEDS_INSTALL,
        ).to_handbook_payload()
        assert "ranking_signals" in block
        assert block["ranking_signals"]["bm25_score"] == 12.4
        assert block["ranking_signals"]["dependency_compat"] == "needs_install"

    def test_trust_signals_live_in_their_own_sub_object(self):
        block = LibraryBlock(
            block_id="b", name="n", type=EntryType.HELPER, language="python",
            category="x", description="d", code="c", license="MIT",
            usage_count=47, impressions=200, accepts=140, mutation_score=0.78,
        ).to_handbook_payload()
        assert block["trust_signals"] == {
            "usage_count": 47, "impressions": 200, "accepts": 140,
            "mutation_score": 0.78, "last_retest_at": None,
        }


class TestGetBlocksRequest:
    def test_projection_defaults_to_ranking(self):
        payload = LibraryGetBlocksRequest(block_ids=("a", "b")).to_handbook_payload()
        assert payload == {"block_ids": ["a", "b"], "projection": "ranking"}

    def test_explicit_projection_is_honoured(self):
        payload = LibraryGetBlocksRequest(block_ids=("a",), projection="text").to_handbook_payload()
        assert payload["projection"] == "text"


class TestSearchResponseShape:
    def test_arm_counts_and_shortfall_travel_with_the_blocks(self):
        payload = LibrarySearchResponse(
            blocks=(), keyword_hits=0, vector_hits=0, shortfall=True
        ).to_handbook_payload()
        assert payload["shortfall"] is True
        assert payload["blocks"] == []
