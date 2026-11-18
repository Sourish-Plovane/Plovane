"""B13 repo tests: §11.3 submit, §12 result, §13 revision."""

from __future__ import annotations

import pytest

from backend.unverified_db.engine import Engine
from backend.unverified_db.repo import Repo


# Input: none
# Output: dict
# Processing: A §11.3 submission payload.
# Changed by: Nirmal
# Date: 2026-10-31
def payload(submission_id="sub_1", block_id="blk_1"):
    return {
        "submission_id": submission_id,
        "project_id": "proj_1",
        "code_blocks": [{
            "block_id": block_id,
            "name": "csv_validator",
            "language": "python",
            "code": "def validate(path):\n    ...",
            "comments": "Validates CSV input.",
            "dependencies": ["pandas"],
        }],
    }


# Input: none
# Output: None
# Processing: submit returns the §11.3 pending_verification ack.
# Changed by: Nirmal
# Date: 2026-10-31
def test_submit_returns_pending_ack():
    repo = Repo(Engine())
    assert repo.submit(payload()) == {"submission_id": "sub_1",
                                       "status": "pending_verification"}
    header = repo.get_submission("sub_1")
    assert header["status"] == "pending_verification"


# Input: none
# Output: None
# Processing: pull_block hands §12 the §12.1 verification input.
# Changed by: Nirmal
# Date: 2026-10-31
def test_pull_block_returns_verification_input():
    repo = Repo(Engine())
    repo.submit(payload())
    pulled = repo.pull_block("sub_1", "blk_1")
    assert pulled["submission_id"] == "sub_1"
    assert pulled["code_block"]["name"] == "csv_validator"


# Input: none
# Output: None
# Processing: record_result stores the §12.2 result and advances the status.
# Changed by: Nirmal
# Date: 2026-10-31
def test_record_result_transitions_status():
    repo = Repo(Engine())
    repo.submit(payload())
    repo.record_result({"submission_id": "sub_1", "block_id": "blk_1",
                        "status": "passed", "issues": []})
    assert repo.get_submission("sub_1")["status"] == "passed"


# Input: none
# Output: None
# Processing: §13 revisions bump revision and reset the status.
# Changed by: Nirmal
# Date: 2026-10-31
def test_record_revision_stores_revision_two():
    repo = Repo(Engine())
    repo.submit(payload())
    repo.record_result({"submission_id": "sub_1", "block_id": "blk_1",
                        "status": "failed", "issues": [{"code": "TEST_FAILURE", "message": "x"}]})
    revised = {
        "submission_id": "sub_1", "block_id": "blk_1", "revision": 2,
        "language": "python", "code": "def validate(path):\n    pass",
        "comments": "Validates CSV input.", "dependencies": ["pandas"],
    }
    assert repo.record_revision(revised) == 2
    pulled = repo.pull_block("sub_1", "blk_1")
    assert pulled["code_block"]["code"].endswith("pass")
    assert repo.get_submission("sub_1")["status"] == "pending_verification"


# Input: none
# Output: None
# Processing: Unknown status is a hard error.
# Changed by: Nirmal
# Date: 2026-10-31
def test_bad_status_rejected():
    repo = Repo(Engine())
    with pytest.raises(ValueError):
        repo.set_status("sub_x", "something_else")
