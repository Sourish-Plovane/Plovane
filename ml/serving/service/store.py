"""Append-only record of what was served and what users did with it.

Two JSONL files: ``recommendations.jsonl`` (one :class:`RecommendationLog`
per response) and ``feedback.jsonl`` (one accept or reject per line, keyed by
``query_id``). Joined on ``query_id`` they are the training data for the
rankers and the calibrator; without them the scores never become
probabilities. Files, not a database, until B08's Postgres exists: the
schema is the same either way, and an append is safe under one lock.
"""

from __future__ import annotations

import json
import os
import threading
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ml.schemas import RecommendationLog

DEFAULT_DIR = Path(__file__).resolve().parent.parent / ".logs"
OUTCOMES = ("accepted", "rejected")


class FeedbackStore:
    """Served recommendations and their feedback, appended to JSONL."""

    # Input: directory (Path | str | None)
    # Output: None
    # Processing: Open the store and load the query ids already served.
    # Changed by: Claude Code
    # Date: 2026-10-22
    def __init__(self, directory: Path | str | None = None) -> None:
        self.directory = Path(directory or os.environ.get("PLOVANE_LOG_DIR") or DEFAULT_DIR)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.recommendations = self.directory / "recommendations.jsonl"
        self.feedback = self.directory / "feedback.jsonl"
        self._lock = threading.Lock()
        # query_id -> the block ids it returned, so feedback can be checked
        # against what the user was actually shown.
        self._served: dict[str, set[str]] = {}
        if self.recommendations.exists():
            for line in self.recommendations.read_text().splitlines():
                if line.strip():
                    row = json.loads(line)
                    self._served[row["query_id"]] = set(row["returned"])

    # Input: path (Path), row (dict)
    # Output: None
    # Processing: Append one JSON line under the lock.
    # Changed by: Claude Code
    # Date: 2026-10-22
    def _append(self, path: Path, row: dict[str, Any]) -> None:
        with self._lock, path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(row, default=str) + "\n")

    # Input: log (RecommendationLog), extra (dict | None)
    # Output: None
    # Processing: Record one served response.
    # Changed by: Claude Code
    # Date: 2026-10-22
    def record_recommendation(self, log: RecommendationLog, extra: dict[str, Any] | None = None) -> None:
        row = asdict(log) | (extra or {})
        self._served[log.query_id] = set(log.returned)
        self._append(self.recommendations, row)

    # Input: query_id (str), block_id (str), outcome (str), user_id (str | None)
    # Output: dict[str, Any]
    # Processing: Record an accept or reject, refusing ids that were never served.
    # Changed by: Claude Code
    # Date: 2026-10-22
    def record_feedback(
        self, query_id: str, block_id: str, outcome: str, user_id: str | None = None
    ) -> dict[str, Any]:
        """Raises ``KeyError`` for an unknown query or a block it did not
        return, and ``ValueError`` for an outcome other than accepted/rejected.
        Feedback on something never shown would be a fabricated label."""
        if outcome not in OUTCOMES:
            raise ValueError(f"outcome must be one of {OUTCOMES}")
        if query_id not in self._served:
            raise KeyError(f"unknown query_id {query_id!r}")
        if block_id not in self._served[query_id]:
            raise KeyError(f"{block_id!r} was not returned for {query_id!r}")
        row = {
            "query_id": query_id,
            "block_id": block_id,
            "outcome": outcome,
            "user_id": user_id,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        self._append(self.feedback, row)
        return row
