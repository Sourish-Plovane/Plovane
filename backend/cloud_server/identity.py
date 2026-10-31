"""B05 identity store: users, password hashes, sessions.

Password storage is hashed with PBKDF2-HMAC-SHA256 from the standard library.
Argon2id is better for prod, but PBKDF2 keeps the backend dependency-free in
development; the hash column is already generic enough to carry Argon2id when
it is wired. OD-4 is still open: §8.3 says the schemas and session mechanics
need engineering review before freeze.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import secrets
import sqlite3
import threading
import uuid
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from typing import Any


# Input: none
# Output: str
# Processing: UTC ISO-8601 timestamp.
# Changed by: Nirmal
# Date: 2026-10-31
def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# Input: delta (timedelta)
# Output: str
# Processing: UTC ISO-8601 timestamp ``delta`` ahead of now.
# Changed by: Nirmal
# Date: 2026-10-31
def _future_iso(delta: timedelta) -> str:
    return (datetime.now(timezone.utc) + delta).strftime("%Y-%m-%dT%H:%M:%SZ")


# Input: password (str), salt (bytes), iterations (int)
# Output: str
# Processing: PBKDF2-HMAC-SHA256 hash encoded as ``pbkdf2$iter$salt$hash``.
# Changed by: Nirmal
# Date: 2026-10-31
def hash_password(password: str, salt: bytes | None = None, iterations: int = 240000) -> str:
    salt = salt or secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return f"pbkdf2${iterations}${salt.hex()}${digest.hex()}"


# Input: password (str), encoded (str)
# Output: bool
# Processing: Constant-time check that a plaintext matches an encoded hash.
# Changed by: Nirmal
# Date: 2026-10-31
def check_password(password: str, encoded: str) -> bool:
    try:
        scheme, iter_str, salt_hex, digest_hex = encoded.split("$")
    except ValueError:
        return False
    if scheme != "pbkdf2":
        return False
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"),
                                 bytes.fromhex(salt_hex), int(iter_str))
    return hmac.compare_digest(digest.hex(), digest_hex)


SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS plovane_user (
    user_id       TEXT PRIMARY KEY,
    email         TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    machine_id    TEXT,
    created_at    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS plovane_session (
    session_token TEXT PRIMARY KEY,
    user_id       TEXT NOT NULL REFERENCES plovane_user(user_id) ON DELETE CASCADE,
    machine_id    TEXT,
    expires_at    TEXT NOT NULL,
    created_at    TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS plovane_session_user ON plovane_session(user_id);
"""


# Input: path (str), ttl (timedelta), clock (Callable)
# Output: IdentityStore
# Processing: User + session store on sqlite3; defaults to in-memory.
# Changed by: Nirmal
# Date: 2026-10-31
class IdentityStore:
    def __init__(self, path: str | None = None,
                 ttl: timedelta = timedelta(hours=24),
                 clock: Callable[[], datetime] | None = None) -> None:
        self.path = path or os.environ.get("PLOVANE_IDENTITY_DB", ":memory:")
        self.ttl = ttl
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self._lock = threading.Lock()
        self._conn: sqlite3.Connection | None = None

    # Input: none
    # Output: sqlite3.Connection
    # Processing: Lazily open the DB and apply the schema.
    # Changed by: Nirmal
    # Date: 2026-10-31
    def _db(self) -> sqlite3.Connection:
        with self._lock:
            if self._conn is None:
                self._conn = sqlite3.connect(self.path, check_same_thread=False,
                                             isolation_level=None)
                self._conn.row_factory = sqlite3.Row
                self._conn.execute("PRAGMA foreign_keys = ON")
                self._conn.executescript(SCHEMA_SQL)
            return self._conn

    # Input: email (str), password (str), machine_id (str | None)
    # Output: dict
    # Processing: Register a new user; return the §8.3 registration response.
    # Changed by: Nirmal
    # Date: 2026-10-31
    def register(self, email: str, password: str,
                 machine_id: str | None = None) -> dict[str, Any]:
        user_id = "usr_" + uuid.uuid4().hex
        try:
            self._db().execute(
                "INSERT INTO plovane_user "
                "(user_id, email, password_hash, machine_id, created_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (user_id, email.lower(), hash_password(password), machine_id, _now_iso()),
            )
        except sqlite3.IntegrityError:
            raise ValueError("email is already registered")
        return {"user_id": user_id}

    # Input: email (str), password (str), machine_id (str | None)
    # Output: dict
    # Processing: Verify the password and issue a session (§8.3 login response).
    # Changed by: Nirmal
    # Date: 2026-10-31
    def login(self, email: str, password: str,
              machine_id: str | None = None) -> dict[str, Any]:
        row = self._db().execute(
            "SELECT user_id, password_hash FROM plovane_user WHERE email = ?",
            (email.lower(),),
        ).fetchone()
        if row is None or not check_password(password, row["password_hash"]):
            raise ValueError("invalid email or password")
        token = secrets.token_urlsafe(32)
        expires_at = _future_iso(self.ttl)
        self._db().execute(
            "INSERT INTO plovane_session "
            "(session_token, user_id, machine_id, expires_at, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (token, row["user_id"], machine_id, expires_at, _now_iso()),
        )
        return {"user_id": row["user_id"], "session_token": token, "expires_at": expires_at}

    # Input: token (str)
    # Output: str | None
    # Processing: Return the user_id for a token, or None if it is bad/expired.
    # Changed by: Nirmal
    # Date: 2026-10-31
    def resolve(self, token: str) -> str | None:
        if not isinstance(token, str) or not token:
            return None
        row = self._db().execute(
            "SELECT user_id, expires_at FROM plovane_session WHERE session_token = ?",
            (token,),
        ).fetchone()
        if row is None:
            return None
        if row["expires_at"] <= _now_iso():
            return None
        return row["user_id"]

    # Input: token (str)
    # Output: None
    # Processing: Revoke one session (log out).
    # Changed by: Nirmal
    # Date: 2026-10-31
    def revoke(self, token: str) -> None:
        self._db().execute(
            "DELETE FROM plovane_session WHERE session_token = ?", (token,))
