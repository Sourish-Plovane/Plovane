"""B08 repo: the SQL that keeps §5.2 code blocks on disk.

Keep SQL in one place so B07 and the load_seed script are the only callers.
The §14.2 ``accepted`` acknowledgement is produced by B07 (the admission
caller), not here: this module only stores and retrieves rows.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from datetime import datetime, timezone
from typing import Any

from backend.library_db.engine import Engine


# Input: code_block (dict), verification (dict | None)
# Output: dict
# Processing: A library_block row with verification stamp defaults.
# Changed by: Nirmal
# Date: 2026-10-31
def row_from_block(code_block: dict[str, Any], verification: dict[str, Any] | None = None) -> dict[str, Any]:
    verification = verification or {}
    return {
        "block_id": code_block["block_id"],
        "name": code_block["name"],
        "language": code_block["language"],
        "code": code_block["code"],
        "comments": code_block["comments"],
        "dependencies": json.dumps(list(code_block.get("dependencies", []))),
        "submission_id": verification.get("submission_id"),
        "revision": verification.get("revision"),
        "verified_at": verification.get("verified_at")
                       or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }


# Input: row (sqlite3.Row | dict)
# Output: dict
# Processing: Turn a row back into the §5.2 shape.
# Changed by: Nirmal
# Date: 2026-10-31
def block_from_row(row: Any) -> dict[str, Any]:
    return {
        "block_id": row["block_id"],
        "name": row["name"],
        "language": row["language"],
        "code": row["code"],
        "comments": row["comments"],
        "dependencies": json.loads(row["dependencies"] or "[]"),
    }


# Input: engine (Engine)
# Output: Repo
# Processing: Hold the engine so queries can share one connection.
# Changed by: Nirmal
# Date: 2026-10-31
class Repo:
    # Input: engine (Engine)
    # Output: None
    # Processing: Store the engine; queries reuse its connection.
    # Changed by: Nirmal
    # Date: 2026-10-31
    def __init__(self, engine: Engine) -> None:
        self.engine = engine

    # Input: code_block (dict), verification (dict | None)
    # Output: dict
    # Processing: Insert or replace a block; return the §14.2 ack.
    # Changed by: Nirmal
    # Date: 2026-10-31
    def admit(self, code_block: dict[str, Any],
              verification: dict[str, Any] | None = None) -> dict[str, Any]:
        row = row_from_block(code_block, verification)
        self.engine.connect().execute(
            "INSERT OR REPLACE INTO library_block "
            "(block_id, name, language, code, comments, dependencies, submission_id, "
            " revision, verified_at) "
            "VALUES (:block_id, :name, :language, :code, :comments, :dependencies, "
            " :submission_id, :revision, :verified_at)",
            row,
        )
        return {"block_id": row["block_id"], "status": "accepted"}

    # Input: block_id (str)
    # Output: dict | None
    # Processing: Return the §5.2 block, or None if there is no such id.
    # Changed by: Nirmal
    # Date: 2026-10-31
    def get(self, block_id: str) -> dict[str, Any] | None:
        row = self.engine.connect().execute(
            "SELECT * FROM library_block WHERE block_id = ?", (block_id,)
        ).fetchone()
        return block_from_row(row) if row else None

    # Input: language (str | None), name_like (str | None), limit (int)
    # Output: list[dict]
    # Processing: Simple candidate search: language filter plus LIKE on name.
    # Changed by: Nirmal
    # Date: 2026-10-31
    def search(self, language: str | None = None, name_like: str | None = None,
               limit: int = 20) -> list[dict[str, Any]]:
        clauses: list[str] = []
        params: list[Any] = []
        if language:
            clauses.append("language = ?")
            params.append(language)
        if name_like:
            clauses.append("name LIKE ?")
            params.append(f"%{name_like}%")
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        params.append(int(limit))
        rows = self.engine.connect().execute(
            f"SELECT * FROM library_block {where} ORDER BY verified_at DESC LIMIT ?",
            params,
        ).fetchall()
        return [block_from_row(row) for row in rows]

    # Input: block_id (str), outcome (str)
    # Output: None
    # Processing: Bump the usage counter for ``accepted`` or ``rejected``.
    # Changed by: Nirmal
    # Date: 2026-10-31
    def bump_counter(self, block_id: str, outcome: str) -> None:
        column = {"accepted": "usage_accepted", "rejected": "usage_rejected"}.get(outcome)
        if column is None:
            raise ValueError("outcome must be 'accepted' or 'rejected'")
        self.engine.connect().execute(
            f"UPDATE library_block SET {column} = {column} + 1 WHERE block_id = ?",
            (block_id,),
        )

    # Input: blocks (Iterable[dict])
    # Output: int
    # Processing: Bulk-insert for the seed loader; returns the inserted count.
    # Changed by: Nirmal
    # Date: 2026-10-31
    def bulk_insert(self, blocks: Iterable[dict[str, Any]]) -> int:
        rows = [row_from_block(b) for b in blocks]
        if not rows:
            return 0
        conn = self.engine.connect()
        conn.executemany(
            "INSERT OR REPLACE INTO library_block "
            "(block_id, name, language, code, comments, dependencies, submission_id, "
            " revision, verified_at) "
            "VALUES (:block_id, :name, :language, :code, :comments, :dependencies, "
            " :submission_id, :revision, :verified_at)",
            rows,
        )
        return len(rows)

    # Input: none
    # Output: int
    # Processing: How many blocks are in the library.
    # Changed by: Nirmal
    # Date: 2026-10-31
    def count(self) -> int:
        return int(self.engine.connect().execute(
            "SELECT COUNT(*) AS n FROM library_block").fetchone()["n"])
