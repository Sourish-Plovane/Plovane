"""Credential storage for the plovane CLI.

Two separate files, both chmod 0600 so another local user cannot read them:

    ~/.plovane/session.json      {user_id, email, session_token, expires_at}
    ~/.plovane/supabase.json     {url, anon_key, service_role_key}

Session is written by ``plovane login`` and cleared by ``plovane logout``.
Supabase credentials are set with ``plovane settings set supabase.url ...``
and read by the ``supabase ping`` command. Keeping them separate from
``config.json`` means a user can share their endpoint config without
leaking secrets.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from cli.config import CONFIG_DIR

SESSION_PATH = CONFIG_DIR / "session.json"
SUPABASE_PATH = CONFIG_DIR / "supabase.json"


# Input: path (Path)
# Output: dict[str, Any]
# Processing: Load a 0600 JSON file, returning an empty dict when missing.
# Changed by: Sourish
# Date: 2026-10-31
def _read(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


# Input: path (Path), payload (dict[str, Any])
# Output: Path
# Processing: Write JSON then chmod 0600 so only the owner can read it.
# Changed by: Sourish
# Date: 2026-10-31
def _write(path: Path, payload: dict[str, Any]) -> Path:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, sort_keys=True)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    return path


# Input: none
# Output: dict[str, Any]
# Processing: Return the stored session or {}.
# Changed by: Sourish
# Date: 2026-10-31
def load_session() -> dict[str, Any]:
    return _read(SESSION_PATH)


# Input: session (dict[str, Any])
# Output: Path
# Processing: Persist a session returned by B03's /api/v1/auth/login.
# Changed by: Sourish
# Date: 2026-10-31
def save_session(session: dict[str, Any]) -> Path:
    return _write(SESSION_PATH, session)


# Input: none
# Output: bool
# Processing: Delete the stored session; return True if a file was removed.
# Changed by: Sourish
# Date: 2026-10-31
def clear_session() -> bool:
    if SESSION_PATH.exists():
        SESSION_PATH.unlink()
        return True
    return False


# Input: none
# Output: dict[str, Any]
# Processing: Return the stored Supabase credentials, falling back to env.
# Changed by: Sourish
# Date: 2026-10-31
def load_supabase() -> dict[str, Any]:
    stored = _read(SUPABASE_PATH)
    for key, env in (("url", "SUPABASE_URL"),
                     ("anon_key", "SUPABASE_ANON_KEY"),
                     ("service_role_key", "SUPABASE_SERVICE_ROLE_KEY")):
        if not stored.get(key):
            value = os.environ.get(env)
            if value:
                stored[key] = value
    return stored


# Input: creds (dict[str, Any])
# Output: Path
# Processing: Persist Supabase credentials to disk at mode 0600.
# Changed by: Sourish
# Date: 2026-10-31
def save_supabase(creds: dict[str, Any]) -> Path:
    return _write(SUPABASE_PATH, creds)
