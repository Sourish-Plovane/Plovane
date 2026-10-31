"""B13 engine: sqlite3 connection factory for the unverified-code store.

Mirrors backend/library_db/engine.py: same pattern, different schema. Rows
here are short-lived; they move to B08 once B14 admits them.
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
    # Processing: One connection per Engine; sqlite is file-backed and safe here.
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
    # Processing: Context manager wrapping ``connect`` for ``with`` users.
    # Changed by: Nirmal
    # Date: 2026-10-31
    @contextmanager
    def session(self) -> Iterator[sqlite3.Connection]:
        yield self.connect()


SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS unverified_submission (
    submission_id TEXT PRIMARY KEY,
    project_id    TEXT NOT NULL,
    user_id       TEXT,
    status        TEXT NOT NULL CHECK (status IN
                       ('pending_verification', 'in_progress', 'passed', 'failed')),
    received_at   TEXT NOT NULL,
    updated_at    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS unverified_block (
    submission_id TEXT NOT NULL REFERENCES unverified_submission(submission_id) ON DELETE CASCADE,
    block_id      TEXT NOT NULL,
    revision      INTEGER NOT NULL DEFAULT 1,
    name          TEXT NOT NULL,
    language      TEXT NOT NULL,
    code          TEXT NOT NULL,
    comments      TEXT NOT NULL,
    dependencies  TEXT NOT NULL,     -- JSON array of strings
    last_result   TEXT,              -- JSON §12.2 or §13.2 result
    PRIMARY KEY (submission_id, block_id, revision)
);

CREATE INDEX IF NOT EXISTS unverified_submission_status
    ON unverified_submission(status, received_at);
"""


# Input: url (str | None)
# Output: Engine
# Processing: Pick the DB from PLOVANE_UNVERIFIED_DB, defaulting to in-memory.
# Changed by: Nirmal
# Date: 2026-10-31
def default_engine(url: str | None = None) -> Engine:
    return Engine(url or os.environ.get("PLOVANE_UNVERIFIED_DB", ":memory:"))
