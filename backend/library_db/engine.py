"""B08 engine: a sqlite3 connection factory.

Prod will swap this out for Postgres + pgvector (library_db/README.md names the
plan). sqlite is enough to keep the handbook contracts honest in development
and in tests: §5.2 code blocks go in, §14.2 acknowledgements come back out.

    conn = Engine(":memory:").connect()           # tests
    conn = Engine("./library.db").connect()       # single-process dev
    conn = Engine(os.environ["PLOVANE_DB_URL"]).connect()
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
                _apply_schema(self._shared)
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


# Input: conn (sqlite3.Connection)
# Output: None
# Processing: Create the tables if they are not already there.
# Changed by: Nirmal
# Date: 2026-10-31
def _apply_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA_SQL)


SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS library_block (
    block_id       TEXT PRIMARY KEY,
    name           TEXT NOT NULL,
    language       TEXT NOT NULL,
    code           TEXT NOT NULL,
    comments       TEXT NOT NULL,
    dependencies   TEXT NOT NULL,       -- JSON array of strings
    submission_id  TEXT,
    revision       INTEGER,
    verified_at    TEXT NOT NULL,
    usage_accepted INTEGER NOT NULL DEFAULT 0,
    usage_rejected INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS library_block_language ON library_block(language);
CREATE INDEX IF NOT EXISTS library_block_name ON library_block(name);
"""


# Input: url (str | None)
# Output: Engine
# Processing: Pick the DB from PLOVANE_LIBRARY_DB, defaulting to in-memory.
# Changed by: Nirmal
# Date: 2026-10-31
def default_engine(url: str | None = None) -> Engine:
    return Engine(url or os.environ.get("PLOVANE_LIBRARY_DB", ":memory:"))
