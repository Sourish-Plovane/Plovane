"""Who is calling the whiteboard server: the person, one agent, or nobody.

Two kinds of bearer token, both sent as ``Authorization: Bearer <token>``:

    person token   made fresh each time the server starts. Written to
                   .plovane/server.json (mode 600) for the CLI, and put into
                   the whiteboard page the server serves, so only something
                   that can read this machine's files or that page acts as
                   the person.
    agent token    made by a person for one agent (POST
                   /api/v1/whiteboard/agents/<name>/token). Only its SHA-256
                   is kept, in .plovane/agent_tokens.json, so the file is
                   useless to anyone who copies it.

A request with neither gets nothing. Before this module, a missing
``X-Plovane-Agent`` header meant "the person", so any AI that left it out had
full rights, including granting itself access.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
from pathlib import Path
from typing import Any

SERVER_FILE = "server.json"
AGENT_TOKENS_FILE = "agent_tokens.json"
TOKEN_PREFIX = "plv_"


# Input: none
# Output: str
# Processing: A new random token, recognisable by its prefix.
# Changed by: Claude Code
# Date: 2026-11-09
def new_token() -> str:
    return TOKEN_PREFIX + secrets.token_urlsafe(32)


# Input: token (str)
# Output: str
# Processing: The SHA-256 hex digest a token is stored as.
# Changed by: Claude Code
# Date: 2026-11-09
def digest(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


# Input: path (Path), payload (dict[str, Any])
# Output: None
# Processing: Write JSON readable by this user only, atomically.
# Changed by: Claude Code
# Date: 2026-11-09
def write_private(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(".tmp")
    fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=1)
    os.chmod(temp, 0o600)
    temp.replace(path)


# Input: path (Path)
# Output: dict[str, Any]
# Processing: Read a JSON object, or an empty one when the file is missing or broken.
# Changed by: Claude Code
# Date: 2026-11-09
def read_json(path: Path) -> dict[str, Any]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return {}
    return raw if isinstance(raw, dict) else {}


class Auth:
    """Maps bearer tokens to the person or an agent for one project."""

    # Input: state_dir (Path), person_token (str)
    # Output: None
    # Processing: Remember the project's .plovane folder and this run's person token.
    # Changed by: Claude Code
    # Date: 2026-11-09
    def __init__(self, state_dir: Path, person_token: str) -> None:
        self.state_dir = state_dir
        self.person_token = person_token
        self.tokens_path = state_dir / AGENT_TOKENS_FILE

    # Input: token (str | None)
    # Output: tuple[str, str | None]
    # Processing: ("person", None), ("agent", name) or ("none", None) for a presented token.
    # Changed by: Claude Code
    # Date: 2026-11-09
    def identify(self, token: str | None) -> tuple[str, str | None]:
        if not token:
            return ("none", None)
        if hmac.compare_digest(token, self.person_token):
            return ("person", None)
        name = read_json(self.tokens_path).get(digest(token))
        return ("agent", name) if isinstance(name, str) else ("none", None)

    # Input: agent (str)
    # Output: str
    # Processing: Make a new token for an agent, replacing any it had, and return it once.
    # Changed by: Claude Code
    # Date: 2026-11-09
    def issue(self, agent: str) -> str:
        token = new_token()
        table = {h: n for h, n in read_json(self.tokens_path).items() if n != agent}
        table[digest(token)] = agent
        write_private(self.tokens_path, table)
        return token

    # Input: agent (str)
    # Output: bool
    # Processing: Drop every token an agent has; True when it had one.
    # Changed by: Claude Code
    # Date: 2026-11-09
    def revoke(self, agent: str) -> bool:
        table = read_json(self.tokens_path)
        kept = {h: n for h, n in table.items() if n != agent}
        if len(kept) != len(table):
            write_private(self.tokens_path, kept)
            return True
        return False


# Input: state_dir (Path), port (int), project (Path), person_token (str)
# Output: Path
# Processing: Record the running server in .plovane/server.json so the CLI can find and use it.
# Changed by: Claude Code
# Date: 2026-11-09
def write_server_file(state_dir: Path, port: int, project: Path, person_token: str) -> Path:
    path = state_dir / SERVER_FILE
    write_private(path, {"url": f"http://127.0.0.1:{port}", "port": port, "pid": os.getpid(),
                         "project": str(project), "token": person_token})
    return path


# Input: state_dir (Path)
# Output: None
# Processing: Remove server.json when it still belongs to this process.
# Changed by: Claude Code
# Date: 2026-11-09
def remove_server_file(state_dir: Path) -> None:
    path = state_dir / SERVER_FILE
    if read_json(path).get("pid") == os.getpid():
        path.unlink(missing_ok=True)
