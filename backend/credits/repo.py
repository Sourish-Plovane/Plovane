"""B16 repo: record §15.1 contributions, produce §15.2 outcomes.

OD-3 keeps the full credit vocabulary open; v1 awards one 'compute' credit per
admitted block as a placeholder, matching the handbook's example. The schema
is already open to more credit types.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from backend.credits.engine import Engine


# Input: none
# Output: str
# Processing: UTC ISO-8601 timestamp for ``received_at`` / ``awarded_at``.
# Changed by: Nirmal
# Date: 2026-10-31
def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


ALLOWED_SOURCES = ("manual_submission", "completed_project")
DEFAULT_CREDIT_TYPE = "compute"


# Input: engine (Engine)
# Output: Repo
# Processing: Hold the engine so §15 operations share one connection.
# Changed by: Nirmal
# Date: 2026-10-31
class Repo:
    def __init__(self, engine: Engine) -> None:
        self.engine = engine

    # Input: payload (dict), credit_type (str), per_block (int)
    # Output: dict
    # Processing: Store a §15.1 submission; return the §15.2 outcome.
    # Changed by: Nirmal
    # Date: 2026-10-31
    def record(self, payload: dict[str, Any], credit_type: str = DEFAULT_CREDIT_TYPE,
               per_block: int = 1) -> dict[str, Any]:
        contribution_id = payload["contribution_id"]
        user_id = payload["user_id"]
        source = payload["source"]
        if source not in ALLOWED_SOURCES:
            raise ValueError(f"source must be one of {ALLOWED_SOURCES}")
        blocks = payload.get("code_blocks", [])
        conn = self.engine.connect()
        conn.execute(
            "INSERT OR REPLACE INTO contribution "
            "(contribution_id, user_id, source, received_at, status) "
            "VALUES (?, ?, ?, ?, 'accepted')",
            (contribution_id, user_id, source, _now()),
        )
        conn.execute("DELETE FROM contribution_block WHERE contribution_id = ?",
                     (contribution_id,))
        rows = [(contribution_id, b["block_id"]) for b in blocks if "block_id" in b]
        if rows:
            conn.executemany(
                "INSERT INTO contribution_block (contribution_id, block_id) VALUES (?, ?)",
                rows,
            )
        amount = per_block * len(rows)
        conn.execute(
            "INSERT OR REPLACE INTO credit_award "
            "(contribution_id, credit_type, amount, status, awarded_at) "
            "VALUES (?, ?, ?, 'pending', NULL)",
            (contribution_id, credit_type, amount),
        )
        return {
            "contribution_id": contribution_id,
            "status": "accepted",
            "credit_outcome": {"status": "pending"},
        }

    # Input: contribution_id (str), credit_type (str)
    # Output: dict
    # Processing: Mark an award as 'awarded'; return the §15.2 outcome.
    # Changed by: Nirmal
    # Date: 2026-10-31
    def mark_awarded(self, contribution_id: str,
                     credit_type: str = DEFAULT_CREDIT_TYPE) -> dict[str, Any]:
        conn = self.engine.connect()
        conn.execute(
            "UPDATE credit_award SET status = 'awarded', awarded_at = ? "
            "WHERE contribution_id = ? AND credit_type = ?",
            (_now(), contribution_id, credit_type),
        )
        row = conn.execute(
            "SELECT amount FROM credit_award "
            "WHERE contribution_id = ? AND credit_type = ?",
            (contribution_id, credit_type),
        ).fetchone()
        if row is None:
            raise ValueError(f"no credit_award for {contribution_id}/{credit_type}")
        return {
            "contribution_id": contribution_id,
            "status": "accepted",
            "credit_outcome": {
                "status": "awarded",
                "credit_type": credit_type,
                "amount": int(row["amount"]),
            },
        }

    # Input: user_id (str)
    # Output: dict
    # Processing: Return the user's totals per credit type.
    # Changed by: Nirmal
    # Date: 2026-10-31
    def balances(self, user_id: str) -> dict[str, Any]:
        rows = self.engine.connect().execute(
            "SELECT ca.credit_type AS credit_type, SUM(ca.amount) AS amount "
            "FROM credit_award ca "
            "JOIN contribution c ON c.contribution_id = ca.contribution_id "
            "WHERE c.user_id = ? AND ca.status = 'awarded' "
            "GROUP BY ca.credit_type",
            (user_id,),
        ).fetchall()
        return {
            "user_id": user_id,
            "balances": [{"credit_type": r["credit_type"], "amount": int(r["amount"] or 0)}
                         for r in rows],
        }

    # Input: contribution_id (str)
    # Output: dict | None
    # Processing: Return the full §15 record for a contribution, or None.
    # Changed by: Nirmal
    # Date: 2026-10-31
    def get(self, contribution_id: str) -> dict[str, Any] | None:
        conn = self.engine.connect()
        header = conn.execute(
            "SELECT * FROM contribution WHERE contribution_id = ?", (contribution_id,)
        ).fetchone()
        if header is None:
            return None
        blocks = conn.execute(
            "SELECT block_id FROM contribution_block WHERE contribution_id = ?",
            (contribution_id,),
        ).fetchall()
        awards = conn.execute(
            "SELECT credit_type, amount, status, awarded_at FROM credit_award "
            "WHERE contribution_id = ?", (contribution_id,),
        ).fetchall()
        return {
            "contribution_id": contribution_id,
            "user_id": header["user_id"],
            "source": header["source"],
            "status": header["status"],
            "received_at": header["received_at"],
            "code_blocks": [{"block_id": b["block_id"]} for b in blocks],
            "credit_awards": [
                {
                    "credit_type": a["credit_type"],
                    "amount": int(a["amount"]),
                    "status": a["status"],
                    "awarded_at": a["awarded_at"],
                }
                for a in awards
            ],
        }


# Input: user_id (str), block_ids (list[str])
# Output: dict
# Processing: §15 payload for a 'completed_project' contribution.
# Changed by: Nirmal
# Date: 2026-10-31
def contribution_from_admission(user_id: str, block_ids: list[str]) -> dict[str, Any]:
    import uuid
    return {
        "contribution_id": "contrib_" + uuid.uuid4().hex,
        "user_id": user_id,
        "source": "completed_project",
        "code_blocks": [{"block_id": bid} for bid in block_ids],
    }
