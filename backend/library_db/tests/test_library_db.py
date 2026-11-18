"""B08 repo tests: admit, get, search, counters."""

from __future__ import annotations

from backend.library_db.engine import Engine
from backend.library_db.repo import Repo


# Input: none
# Output: dict
# Processing: A canonical §5.2 code block for tests.
# Changed by: Nirmal
# Date: 2026-10-31
def block(block_id="blk_1", name="csv_validator"):
    return {
        "block_id": block_id,
        "name": name,
        "language": "python",
        "code": "def validate(path):\n    ...",
        "comments": "Validates CSV input.",
        "dependencies": ["pandas"],
    }


# Input: none
# Output: None
# Processing: Admitting a block returns the §14.2 ack and makes it fetchable.
# Changed by: Nirmal
# Date: 2026-10-31
def test_admit_then_get_round_trip():
    repo = Repo(Engine())
    ack = repo.admit(block(), {"submission_id": "sub_1", "revision": 2})
    assert ack == {"block_id": "blk_1", "status": "accepted"}
    got = repo.get("blk_1")
    assert got == block()


# Input: none
# Output: None
# Processing: search returns rows matching language and LIKE on name.
# Changed by: Nirmal
# Date: 2026-10-31
def test_search_filters_by_language_and_name():
    repo = Repo(Engine())
    repo.admit(block("blk_py", "csv_validator"))
    repo.admit({**block("blk_js", "csv_validator"), "language": "javascript"})
    repo.admit(block("blk_other", "pdf_parser"))
    matches = repo.search(language="python", name_like="csv")
    assert {b["block_id"] for b in matches} == {"blk_py"}


# Input: none
# Output: None
# Processing: bump_counter increments the right column.
# Changed by: Nirmal
# Date: 2026-10-31
def test_bump_counter():
    engine = Engine()
    repo = Repo(engine)
    repo.admit(block())
    repo.bump_counter("blk_1", "accepted")
    repo.bump_counter("blk_1", "accepted")
    repo.bump_counter("blk_1", "rejected")
    row = engine.connect().execute(
        "SELECT usage_accepted, usage_rejected FROM library_block WHERE block_id = 'blk_1'"
    ).fetchone()
    assert row["usage_accepted"] == 2
    assert row["usage_rejected"] == 1


# Input: none
# Output: None
# Processing: bulk_insert returns the count and loads the rows.
# Changed by: Nirmal
# Date: 2026-10-31
def test_bulk_insert_loads_all():
    repo = Repo(Engine())
    inserted = repo.bulk_insert([block(f"blk_{i}", f"name_{i}") for i in range(5)])
    assert inserted == 5
    assert repo.count() == 5
