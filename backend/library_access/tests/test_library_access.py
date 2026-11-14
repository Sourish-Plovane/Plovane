"""B07 façade tests."""

from __future__ import annotations

import pytest

from backend.library_access.access import AdmissionError, LibraryAccess


# Input: none
# Output: dict
# Processing: A §14.1 admission payload with a passed verification.
# Changed by: Nirmal
# Date: 2026-10-31
def admission(block_id="blk_1", status="passed", revision=2):
    return {
        "submission_id": "sub_1",
        "code_block": {
            "block_id": block_id,
            "name": "csv_validator",
            "language": "python",
            "code": "def validate(path):\n    ...",
            "comments": "Validates CSV input.",
            "dependencies": ["pandas"],
        },
        "verification": {"status": status, "revision": revision},
    }


# Input: none
# Output: None
# Processing: Admit a passed block and read it back.
# Changed by: Nirmal
# Date: 2026-10-31
def test_admit_returns_accepted_and_stores_the_block():
    access = LibraryAccess()
    ack = access.admit(admission())
    assert ack == {"block_id": "blk_1", "status": "accepted"}
    assert access.get("blk_1")["name"] == "csv_validator"


# Input: none
# Output: None
# Processing: A verification that did not pass is rejected before storage.
# Changed by: Nirmal
# Date: 2026-10-31
def test_admit_rejects_non_passed_verification():
    access = LibraryAccess()
    with pytest.raises(AdmissionError):
        access.admit(admission(status="failed"))
    assert access.get("blk_1") is None


# Input: none
# Output: None
# Processing: Missing fields fail before touching the DB.
# Changed by: Nirmal
# Date: 2026-10-31
def test_admit_requires_every_block_field():
    access = LibraryAccess()
    bad = admission()
    del bad["code_block"]["comments"]
    with pytest.raises(AdmissionError):
        access.admit(bad)


# Input: none
# Output: None
# Processing: search_candidates composes the language and name filters.
# Changed by: Nirmal
# Date: 2026-10-31
def test_search_candidates_by_language():
    access = LibraryAccess()
    access.admit(admission("blk_py"))
    access.admit({**admission("blk_js"),
                  "code_block": {**admission("blk_js")["code_block"], "language": "javascript"}})
    matches = access.search_candidates(language="python")
    assert {b["block_id"] for b in matches} == {"blk_py"}
