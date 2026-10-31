"""B16 repo tests: §15.1 record, §15.2 outcome, balances."""

from __future__ import annotations

import pytest

from backend.credits.engine import Engine
from backend.credits.repo import Repo, contribution_from_admission


# Input: none
# Output: dict
# Processing: A §15.1 contribution payload with two blocks.
# Changed by: Nirmal
# Date: 2026-10-31
def payload():
    return {
        "contribution_id": "contrib_1",
        "user_id": "usr_1",
        "source": "manual_submission",
        "code_blocks": [
            {"block_id": "blk_1", "name": "a", "language": "python",
             "code": "", "comments": "", "dependencies": []},
            {"block_id": "blk_2", "name": "b", "language": "python",
             "code": "", "comments": "", "dependencies": []},
        ],
    }


# Input: none
# Output: None
# Processing: Recording returns the §15.2 pending outcome.
# Changed by: Nirmal
# Date: 2026-10-31
def test_record_returns_pending_outcome():
    repo = Repo(Engine())
    outcome = repo.record(payload())
    assert outcome["status"] == "accepted"
    assert outcome["credit_outcome"] == {"status": "pending"}


# Input: none
# Output: None
# Processing: Marking the award shows up in balances with the right amount.
# Changed by: Nirmal
# Date: 2026-10-31
def test_mark_awarded_credits_user_balance():
    repo = Repo(Engine())
    repo.record(payload())
    awarded = repo.mark_awarded("contrib_1")
    assert awarded["credit_outcome"]["status"] == "awarded"
    balances = repo.balances("usr_1")
    assert balances == {"user_id": "usr_1",
                        "balances": [{"credit_type": "compute", "amount": 2}]}


# Input: none
# Output: None
# Processing: A bad ``source`` is rejected.
# Changed by: Nirmal
# Date: 2026-10-31
def test_bad_source_rejected():
    repo = Repo(Engine())
    bad = payload()
    bad["source"] = "something_else"
    with pytest.raises(ValueError):
        repo.record(bad)


# Input: none
# Output: None
# Processing: Admission helper builds a 'completed_project' contribution.
# Changed by: Nirmal
# Date: 2026-10-31
def test_contribution_from_admission():
    payload = contribution_from_admission("usr_1", ["blk_1", "blk_2"])
    assert payload["user_id"] == "usr_1"
    assert payload["source"] == "completed_project"
    assert [b["block_id"] for b in payload["code_blocks"]] == ["blk_1", "blk_2"]
