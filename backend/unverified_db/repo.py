"""B13 repo: store §11.3 submissions, serve them to §12 verification.

Submissions carry a status; blocks carry a revision so §13 reverification
rows do not clobber the original. The repo returns the §11.3 acknowledgement
on write and the §12.1 or §13.1 verification input on read.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from backend.unverified_db.engine import Engine


# Input: none
# Output: str
# Processing: UTC ISO-8601 string for the ``received_at`` columns.
# Changed by: Nirmal
# Date: 2026-10-31
def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# Input: code_block (dict), submission_id (str), revision (int)
# Output: dict
# Processing: Row ready for insertion into unverified_block.
# Changed by: Nirmal
# Date: 2026-10-31
def _block_row(code_block: dict[str, Any], submission_id: str, revision: int) -> dict[str, Any]:
    return {
        "submission_id": submission_id,
        "block_id": code_block["block_id"],
        "revision": revision,
        "name": code_block["name"],
        "language": code_block["language"],
        "code": code_block["code"],
        "comments": code_block["comments"],
        "dependencies": json.dumps(list(code_block.get("dependencies", []))),
    }


# Input: row (sqlite3.Row)
# Output: dict
# Processing: §5.2 code block from a stored row.
# Changed by: Nirmal
# Date: 2026-10-31
def _block_from_row(row: Any) -> dict[str, Any]:
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
# Processing: Hold the engine so writes and reads share a connection.
# Changed by: Nirmal
# Date: 2026-10-31
class Repo:
    def __init__(self, engine: Engine) -> None:
        self.engine = engine

    # Input: payload (dict)
    # Output: dict
    # Processing: Store §11.3; return the ``pending_verification`` ack.
    # Changed by: Nirmal
    # Date: 2026-10-31
    def submit(self, payload: dict[str, Any]) -> dict[str, Any]:
        submission_id = payload["submission_id"]
        project_id = payload["project_id"]
        user_id = payload.get("user_id")
        now = _now()
        conn = self.engine.connect()
        conn.execute(
            "INSERT OR REPLACE INTO unverified_submission "
            "(submission_id, project_id, user_id, status, received_at, updated_at) "
            "VALUES (?, ?, ?, 'pending_verification', ?, ?)",
            (submission_id, project_id, user_id, now, now),
        )
        conn.execute("DELETE FROM unverified_block WHERE submission_id = ?", (submission_id,))
        rows = [_block_row(b, submission_id, 1) for b in payload.get("code_blocks", [])]
        if rows:
            conn.executemany(
                "INSERT INTO unverified_block "
                "(submission_id, block_id, revision, name, language, code, comments, dependencies) "
                "VALUES (:submission_id, :block_id, :revision, :name, :language, :code, "
                " :comments, :dependencies)",
                rows,
            )
        return {"submission_id": submission_id, "status": "pending_verification"}

    # Input: submission_id (str)
    # Output: dict | None
    # Processing: Return the submission header, or None.
    # Changed by: Nirmal
    # Date: 2026-10-31
    def get_submission(self, submission_id: str) -> dict[str, Any] | None:
        row = self.engine.connect().execute(
            "SELECT * FROM unverified_submission WHERE submission_id = ?", (submission_id,)
        ).fetchone()
        if row is None:
            return None
        return {
            "submission_id": row["submission_id"],
            "project_id": row["project_id"],
            "user_id": row["user_id"],
            "status": row["status"],
            "received_at": row["received_at"],
            "updated_at": row["updated_at"],
        }

    # Input: submission_id (str), block_id (str), revision (int | None)
    # Output: dict | None
    # Processing: Return §12.1 verification input for one block.
    # Changed by: Nirmal
    # Date: 2026-10-31
    def pull_block(self, submission_id: str, block_id: str,
                   revision: int | None = None) -> dict[str, Any] | None:
        if revision is None:
            row = self.engine.connect().execute(
                "SELECT * FROM unverified_block WHERE submission_id = ? AND block_id = ? "
                "ORDER BY revision DESC LIMIT 1",
                (submission_id, block_id),
            ).fetchone()
        else:
            row = self.engine.connect().execute(
                "SELECT * FROM unverified_block "
                "WHERE submission_id = ? AND block_id = ? AND revision = ?",
                (submission_id, block_id, revision),
            ).fetchone()
        if row is None:
            return None
        return {"submission_id": submission_id, "code_block": _block_from_row(row)}

    # Input: submission_id (str), status (str)
    # Output: None
    # Processing: Advance the submission header to a new status.
    # Changed by: Nirmal
    # Date: 2026-10-31
    def set_status(self, submission_id: str, status: str) -> None:
        if status not in ("pending_verification", "in_progress", "passed", "failed"):
            raise ValueError(f"unknown status: {status}")
        self.engine.connect().execute(
            "UPDATE unverified_submission SET status = ?, updated_at = ? "
            "WHERE submission_id = ?",
            (status, _now(), submission_id),
        )

    # Input: result (dict)
    # Output: None
    # Processing: Record a §12.2 or §13.2 result on the matching block row.
    # Changed by: Nirmal
    # Date: 2026-10-31
    def record_result(self, result: dict[str, Any]) -> None:
        submission_id = result["submission_id"]
        block_id = result["block_id"]
        revision = int(result.get("revision", 1))
        status = result["status"]
        if status not in ("passed", "failed"):
            raise ValueError("status must be 'passed' or 'failed'")
        payload = json.dumps(result)
        conn = self.engine.connect()
        conn.execute(
            "UPDATE unverified_block SET last_result = ? "
            "WHERE submission_id = ? AND block_id = ? AND revision = ?",
            (payload, submission_id, block_id, revision),
        )
        self.set_status(submission_id, status)

    # Input: payload (dict)
    # Output: int
    # Processing: Store a §13.1 revised block; return the new revision.
    # Changed by: Nirmal
    # Date: 2026-10-31
    def record_revision(self, payload: dict[str, Any]) -> int:
        submission_id = payload["submission_id"]
        block_id = payload["block_id"]
        revision = int(payload["revision"])
        if revision < 1:
            raise ValueError("revision must be a positive integer")
        code_block = {
            "block_id": block_id,
            "name": payload.get("name") or "",
            "language": payload["language"],
            "code": payload["code"],
            "comments": payload["comments"],
            "dependencies": payload.get("dependencies", []),
        }
        conn = self.engine.connect()
        conn.execute(
            "INSERT OR REPLACE INTO unverified_block "
            "(submission_id, block_id, revision, name, language, code, comments, dependencies) "
            "VALUES (:submission_id, :block_id, :revision, :name, :language, :code, "
            " :comments, :dependencies)",
            _block_row(code_block, submission_id, revision),
        )
        self.set_status(submission_id, "pending_verification")
        return revision

    # Input: submission_id (str), block_id (str)
    # Output: None
    # Processing: Drop a block once it has been promoted to B08.
    # Changed by: Nirmal
    # Date: 2026-10-31
    def drop_block(self, submission_id: str, block_id: str) -> None:
        self.engine.connect().execute(
            "DELETE FROM unverified_block WHERE submission_id = ? AND block_id = ?",
            (submission_id, block_id),
        )

    # Input: submission_id (str)
    # Output: None
    # Processing: Drop a submission and its remaining blocks.
    # Changed by: Nirmal
    # Date: 2026-10-31
    def drop_submission(self, submission_id: str) -> None:
        self.engine.connect().execute(
            "DELETE FROM unverified_submission WHERE submission_id = ?", (submission_id,))
