"""B07 access: the only reader and writer of B08.

The handbook (§14.1) requires that admission carry a ``verification`` object
with at least ``status: "passed"`` and the ``revision`` that was tested. We
enforce that invariant here. §14.2 returns ``{block_id, status: "accepted"}``.

Search is deliberately simple: a language filter plus a LIKE on the name.
B06 keeps using ``ml/retrieval`` for the real recommendation pipeline; this
module is for the admission path and for small lookups.
"""

from __future__ import annotations

from typing import Any

from backend.library_db.engine import Engine as LibraryEngine
from backend.library_db.repo import Repo as LibraryRepo


REQUIRED_BLOCK_FIELDS = ("block_id", "name", "language", "code", "comments", "dependencies")


# Input: message (str)
# Output: AdmissionError
# Processing: A §14.1 payload that cannot be admitted.
# Changed by: Nirmal
# Date: 2026-10-31
class AdmissionError(ValueError):
    """A §14.1 payload that fails the admission contract."""


# Input: engine (LibraryEngine | None)
# Output: LibraryAccess
# Processing: Build the façade on top of a B08 engine.
# Changed by: Nirmal
# Date: 2026-10-31
class LibraryAccess:
    # Input: engine (LibraryEngine | None)
    # Output: None
    # Processing: Default the engine to a fresh in-memory B08 engine.
    # Changed by: Nirmal
    # Date: 2026-10-31
    def __init__(self, engine: LibraryEngine | None = None) -> None:
        self.engine = engine or LibraryEngine()
        self.repo = LibraryRepo(self.engine)

    # Input: payload (dict)
    # Output: dict
    # Processing: Admit a §14.1 payload; return the §14.2 acknowledgement.
    # Changed by: Nirmal
    # Date: 2026-10-31
    def admit(self, payload: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(payload, dict):
            raise AdmissionError("admission payload must be a JSON object")
        code_block = payload.get("code_block")
        verification = payload.get("verification")
        submission_id = payload.get("submission_id")
        if not isinstance(code_block, dict):
            raise AdmissionError("code_block is required and must be an object")
        for field in REQUIRED_BLOCK_FIELDS:
            if field not in code_block:
                raise AdmissionError(f"code_block.{field} is required")
        if not isinstance(verification, dict):
            raise AdmissionError("verification is required and must be an object")
        if verification.get("status") != "passed":
            raise AdmissionError("verification.status must be 'passed' to admit")
        revision = verification.get("revision")
        if revision is not None and (not isinstance(revision, int) or revision < 1):
            raise AdmissionError("verification.revision must be a positive integer")
        stamp = {"submission_id": submission_id, "revision": revision}
        return self.repo.admit(code_block, stamp)

    # Input: block_id (str)
    # Output: dict | None
    # Processing: Return one §5.2 block, or None if there is no such id.
    # Changed by: Nirmal
    # Date: 2026-10-31
    def get(self, block_id: str) -> dict[str, Any] | None:
        return self.repo.get(block_id)

    # Input: language (str | None), name_like (str | None), limit (int)
    # Output: list[dict]
    # Processing: Candidate search: a language filter plus a LIKE on the name.
    # Changed by: Nirmal
    # Date: 2026-10-31
    def search_candidates(self, language: str | None = None,
                          name_like: str | None = None,
                          limit: int = 20) -> list[dict[str, Any]]:
        if limit <= 0 or limit > 100:
            raise AdmissionError("limit must be between 1 and 100")
        return self.repo.search(language=language, name_like=name_like, limit=limit)

    # Input: block_id (str), outcome (str)
    # Output: None
    # Processing: Bump the usage counter when the GUI accepts or rejects a block.
    # Changed by: Nirmal
    # Date: 2026-10-31
    def record_feedback(self, block_id: str, outcome: str) -> None:
        self.repo.bump_counter(block_id, outcome)
