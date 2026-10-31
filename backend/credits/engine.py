"""B16 engine: sqlite3 connection factory for the credits store.

Three tables: ``contribution`` (one row per §15.1 submission), the
``contribution_block`` association, and ``credit_award`` (§15.2 outcome).
OD-3 freezes the credit types/units later; v1 only accepts 'compute'.
"""

from __future__ import annotations

import os
import sqlite3
import threading
from collections.abc import Iterator
from contextlib import contextmanager


# Input: path (str)
# Output: Engine
# Processing: Hold the DB path and apply the schema once per process.
# Changed by: Nirmal
# Date: 2026-10-31
class Engine:
    def __init__(self, path: str = ":memory:") -> None:
        self.path = path
        self._lock = threading.Lock()
        self._shared: sqlite3.Connection | None = None
        self._initialised = False

    # Input: none
    # Output: sqlite3.Connection
    # Processing: One shared connection per Engine.
    # Changed by: Nirmal
    # Date: 2026-10-31
    def connect(self) -> sqlite3.Connection:
        with self._lock:
            if self._shared is None:
                self._shared = sqlite3.connect(self.path, check_same_thread=False,
                                               isolation_level=None)
                self._shared.row_factory = sqlite3.Row
                self._shared.execute("PRAGMA foreign_keys = ON")
            if not self._initialised:
                self._shared.executescript(SCHEMA_SQL)
                self._initialised = True
            return self._shared

    # Input: none
    # Output: Iterator[sqlite3.Connection]
    # Processing: Context manager wrapping ``connect``.
    # Changed by: Nirmal
    # Date: 2026-10-31
    @contextmanager
    def session(self) -> Iterator[sqlite3.Connection]:
        yield self.connect()


SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS contribution (
    contribution_id TEXT PRIMARY KEY,
    user_id         TEXT NOT NULL,
    source          TEXT NOT NULL CHECK (source IN ('manual_submission', 'completed_project')),
    received_at     TEXT NOT NULL,
    status          TEXT NOT NULL CHECK (status IN ('accepted'))
);

CREATE TABLE IF NOT EXISTS contribution_block (
    contribution_id TEXT NOT NULL REFERENCES contribution(contribution_id) ON DELETE CASCADE,
    block_id        TEXT NOT NULL,
    PRIMARY KEY (contribution_id, block_id)
);

CREATE TABLE IF NOT EXISTS credit_award (
    contribution_id TEXT NOT NULL REFERENCES contribution(contribution_id) ON DELETE CASCADE,
    credit_type     TEXT NOT NULL,
    amount          INTEGER NOT NULL,
    status          TEXT NOT NULL CHECK (status IN ('pending', 'awarded')),
    awarded_at      TEXT,
    PRIMARY KEY (contribution_id, credit_type)
);

CREATE INDEX IF NOT EXISTS contribution_user ON contribution(user_id, received_at);
"""


# Input: url (str | None)
# Output: Engine
# Processing: Pick the DB from PLOVANE_CREDITS_DB, defaulting to in-memory.
# Changed by: Nirmal
# Date: 2026-10-31
def default_engine(url: str | None = None) -> Engine:
    return Engine(url or os.environ.get("PLOVANE_CREDITS_DB", ":memory:"))
