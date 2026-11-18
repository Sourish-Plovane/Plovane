"""Thin HTTP client for B03's §4.1 API.

The CLI only hits three routes:

    POST /api/v1/auth/register
    POST /api/v1/auth/login
    POST /api/v1/recommendations

Every call returns the §4.1 envelope ``{"ok", "data", "error"}``; this
module unwraps it, raising ``LocalBackendError`` on an envelope ``ok=False``
or on transport failure. The session token from a successful login is
sent as the ``X-Plovane-Session`` header on later requests.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any


class LocalBackendError(RuntimeError):
    """A B03 call failed in transport or returned ``ok=False``."""

    # Input: message (str), code (str | None)
    # Output: None
    # Processing: Carry the §16.2 error code alongside the message.
    # Changed by: Sourish
    # Date: 2026-10-31
    def __init__(self, message: str, code: str | None = None) -> None:
        super().__init__(message)
        self.code = code


# Input: base_url (str), path (str), body (dict | None), user (str),
#        session_token (str | None), timeout (float)
# Output: dict[str, Any]
# Processing: GET or POST a B03 endpoint and return the §4.1 ``data`` field.
# Changed by: Sourish
# Date: 2026-10-31
def call(
    base_url: str,
    path: str,
    body: dict[str, Any] | None = None,
    user: str = "local",
    session_token: str | None = None,
    timeout: float = 15.0,
) -> dict[str, Any]:
    headers = {"X-Plovane-User": user}
    data: bytes | None = None
    method = "GET"
    if body is not None:
        method = "POST"
        headers["Content-Type"] = "application/json"
        data = json.dumps(body).encode("utf-8")
    if session_token:
        headers["X-Plovane-Session"] = session_token
    request = urllib.request.Request(
        base_url.rstrip("/") + path,
        data=data,
        headers=headers,
        method=method,
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as reply:
            payload = json.loads(reply.read().decode("utf-8"))
    except urllib.error.HTTPError as http_error:
        detail = http_error.read().decode("utf-8", errors="replace")
        try:
            envelope = json.loads(detail)
            err = envelope.get("error") or {}
            raise LocalBackendError(err.get("message") or http_error.reason,
                                    err.get("code")) from http_error
        except json.JSONDecodeError:
            raise LocalBackendError(f"HTTP {http_error.code}: {detail[:120]}") from http_error
    except urllib.error.URLError as net_error:
        raise LocalBackendError(f"cannot reach {base_url}: {net_error.reason}") from net_error
    if not isinstance(payload, dict) or payload.get("ok") is not True:
        err = (payload or {}).get("error") or {}
        raise LocalBackendError(err.get("message") or "unknown error", err.get("code"))
    return payload.get("data") or {}


# Input: base_url (str), email (str), password (str), machine_id (str | None), user (str)
# Output: dict[str, Any]
# Processing: POST /api/v1/auth/register with a §8.3 body.
# Changed by: Sourish
# Date: 2026-10-31
def register(base_url: str, email: str, password: str, machine_id: str | None, user: str) -> dict[str, Any]:
    return call(base_url, "/api/v1/auth/register",
                {"email": email, "password": password, "machine_id": machine_id},
                user=user)


# Input: base_url (str), email (str), password (str), machine_id (str | None), user (str)
# Output: dict[str, Any]
# Processing: POST /api/v1/auth/login and return {user_id, session_token, expires_at}.
# Changed by: Sourish
# Date: 2026-10-31
def login(base_url: str, email: str, password: str, machine_id: str | None, user: str) -> dict[str, Any]:
    return call(base_url, "/api/v1/auth/login",
                {"email": email, "password": password, "machine_id": machine_id},
                user=user)


# Input: base_url (str), body (dict), user (str), session_token (str | None)
# Output: dict[str, Any]
# Processing: POST /api/v1/recommendations with a §5.1 body and return §5.2 data.
# Changed by: Sourish
# Date: 2026-10-31
def recommend(base_url: str, body: dict[str, Any], user: str, session_token: str | None) -> dict[str, Any]:
    return call(base_url, "/api/v1/recommendations", body,
                user=user, session_token=session_token, timeout=30.0)
