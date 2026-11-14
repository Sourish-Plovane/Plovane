"""Integration flow: B13 -> B14 fake -> B07 admit -> B16 contribution.

    python scripts/flow_admit.py

B14 is Sourish's and does not exist yet, so this script fakes the verification
step: it submits a §11.3 block to B13, immediately records a passing §12.2
result, admits the block to B08 through B07, and credits the user through B16.
No HTTP is involved: everything is in-process and the DBs are in-memory.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.credits.engine import Engine as CreditsEngine
from backend.credits.repo import Repo as CreditsRepo, contribution_from_admission
from backend.library_access.access import LibraryAccess
from backend.library_db.engine import Engine as LibraryEngine
from backend.unverified_db.engine import Engine as UnverifiedEngine
from backend.unverified_db.repo import Repo as UnverifiedRepo


# Input: none
# Output: dict
# Processing: A §11.3 submission with one §5.2 block.
# Changed by: Nirmal
# Date: 2026-10-31
def submission():
    return {
        "submission_id": "sub_1",
        "project_id": "proj_1",
        "user_id": "usr_1",
        "code_blocks": [{
            "block_id": "blk_1",
            "name": "csv_validator",
            "language": "python",
            "code": "def validate(path):\n    return True\n",
            "comments": "Validates CSV input.",
            "dependencies": ["pandas"],
        }],
    }


# Input: none
# Output: int
# Processing: Walk one block from novelty acceptance to credit award.
# Changed by: Nirmal
# Date: 2026-10-31
def main() -> int:
    unverified = UnverifiedRepo(UnverifiedEngine())
    library = LibraryAccess(LibraryEngine())
    credits = CreditsRepo(CreditsEngine())

    ack = unverified.submit(submission())
    print("B13 submit:", json.dumps(ack))

    pulled = unverified.pull_block("sub_1", "blk_1")
    print("B14 input:", json.dumps(pulled))
    verification = {"submission_id": "sub_1", "block_id": "blk_1",
                    "status": "passed", "issues": [], "revision": 1}
    unverified.record_result(verification)

    admission = {
        "submission_id": "sub_1",
        "code_block": pulled["code_block"],
        "verification": {"status": "passed", "revision": 1},
    }
    print("B07 admit:", json.dumps(library.admit(admission)))
    unverified.drop_block("sub_1", "blk_1")

    contribution = contribution_from_admission("usr_1", ["blk_1"])
    print("B16 record:", json.dumps(credits.record(contribution)))
    awarded = credits.mark_awarded(contribution["contribution_id"])
    print("B16 award:", json.dumps(awarded))
    print("B16 balance:", json.dumps(credits.balances("usr_1")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
