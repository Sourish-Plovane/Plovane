"""B05 auth middleware (handbook §8.3; still OD-4).

Public routes: ``/healthz``, ``/v1/auth/*``. Everything else requires an
``Authorization: Bearer <token>`` header unless the server is started with
``PLOVANE_REQUIRE_AUTH=0``, which keeps development friction low.

Mapping onto §16.2: a missing or expired token produces
``{"request_id": None, "error": {"code": "UNAUTHENTICATED", ...}}`` with HTTP
401, matching the cloud error body already used by B05.
"""

from __future__ import annotations

import os
from typing import Any, Callable

from flask import Request

from backend.cloud_server.identity import IdentityStore


PUBLIC_PREFIXES = ("/healthz", "/v1/auth/")


# Input: raw (bool | str | None)
# Output: bool
# Processing: Interpret an env flag as a boolean, defaulting to False.
# Changed by: Nirmal
# Date: 2026-10-31
def _flag(raw: Any) -> bool:
    if isinstance(raw, bool):
        return raw
    if isinstance(raw, str):
        return raw.lower() in ("1", "true", "yes", "on")
    return False


# Input: path (str)
# Output: bool
# Processing: Whether a path is public (no bearer token required).
# Changed by: Nirmal
# Date: 2026-10-31
def is_public(path: str) -> bool:
    return any(path == prefix.rstrip("/") or path.startswith(prefix)
               for prefix in PUBLIC_PREFIXES)


# Input: header (str | None)
# Output: str | None
# Processing: Extract the token from an ``Authorization: Bearer`` header.
# Changed by: Nirmal
# Date: 2026-10-31
def bearer_token(header: str | None) -> str | None:
    if not isinstance(header, str):
        return None
    parts = header.split(None, 1)
    if len(parts) != 2 or parts[0].lower() != "bearer":
        return None
    return parts[1].strip() or None


# Input: identity (IdentityStore), require (bool | None)
# Output: Guard
# Processing: Build the guard with the configured identity store.
# Changed by: Nirmal
# Date: 2026-10-31
class Guard:
    # Input: identity (IdentityStore), require (bool | None)
    # Output: None
    # Processing: Store the identity backend and decide whether auth is required.
    # Changed by: Nirmal
    # Date: 2026-10-31
    def __init__(self, identity: IdentityStore, require: bool | None = None) -> None:
        self.identity = identity
        self.require = _flag(os.environ.get("PLOVANE_REQUIRE_AUTH")) if require is None else require

    # Input: request (Request)
    # Output: str | None
    # Processing: Return the authenticated user_id, or None if unauthenticated.
    # Changed by: Nirmal
    # Date: 2026-10-31
    def resolve(self, request: Request) -> str | None:
        token = bearer_token(request.headers.get("Authorization"))
        if token is None:
            return None
        return self.identity.resolve(token)

    # Input: request (Request)
    # Output: tuple[str | None, bool]
    # Processing: (user_id, authenticated). Public paths return (None, True).
    # Changed by: Nirmal
    # Date: 2026-10-31
    def check(self, request: Request) -> tuple[str | None, bool]:
        if is_public(request.path):
            return self.resolve(request), True
        if not self.require:
            return self.resolve(request), True
        user_id = self.resolve(request)
        return user_id, user_id is not None
