"""B05's HTTPS client for the B06 recommender (handbook §4.2, gap G2).

Sourish's ``ml/serving/service/contract.py`` is the accepted G2 mapping, so
B05 forwards the §8.1 body unchanged to ``POST /v1/recommend`` and returns
B06's §8.2 body as-is. Only the standard library is used.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any, Protocol


# Input: code (str), message (str), status (int)
# Output: UpstreamError
# Processing: A failed B06 call with its §16.2 code and HTTP status.
# Changed by: Nirmal
# Date: 2026-10-31
class UpstreamError(RuntimeError):
    """The recommender call failed. ``code`` is a §16.2 code."""

    # Input: code (str), message (str), status (int)
    # Output: None
    # Processing: Carry the §16.2 code and the HTTP status alongside the message.
    # Changed by: Nirmal
    # Date: 2026-10-31
    def __init__(self, code: str, message: str, status: int = 502) -> None:
        super().__init__(message)
        self.code = code
        self.status = status


# Input: body (dict[str, Any])
# Output: dict[str, Any]
# Processing: The B06 call B05 makes; injected in tests.
# Changed by: Nirmal
# Date: 2026-10-31
class Recommender(Protocol):
    # Input: body (dict[str, Any])
    # Output: dict[str, Any]
    # Processing: Protocol stub: implementations POST to B06 and return its JSON.
    # Changed by: Nirmal
    # Date: 2026-10-31
    def recommend(self, body: dict[str, Any]) -> dict[str, Any]:
        ...


# Input: base_url (str), timeout (float)
# Output: HttpRecommender
# Processing: Build a client for one B06 service.
# Changed by: Nirmal
# Date: 2026-10-31
class HttpRecommender:
    # Input: base_url (str), timeout (float)
    # Output: None
    # Processing: Normalise the base URL and remember the HTTP timeout.
    # Changed by: Nirmal
    # Date: 2026-10-31
    def __init__(self, base_url: str, timeout: float = 60.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    # Input: body (dict[str, Any])
    # Output: dict[str, Any]
    # Processing: POST the §8.1 body and return the §8.2 JSON.
    # Changed by: Nirmal
    # Date: 2026-10-31
    def recommend(self, body: dict[str, Any]) -> dict[str, Any]:
        request = urllib.request.Request(
            self.base_url + "/v1/recommend",
            data=json.dumps(body).encode("utf-8"),
            headers={"Content-Type": "application/json", "Accept": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as reply:
                return _json_body(reply.read())
        except urllib.error.HTTPError as exc:
            payload = _json_body(exc.read())
            code, message = _error_fields(payload, exc.code)
            raise UpstreamError(code, message, exc.code) from None
        except urllib.error.URLError as exc:
            raise UpstreamError("INTERNAL_ERROR", f"cannot reach the recommender: {exc.reason}", 503) from None
        except TimeoutError:
            raise UpstreamError("INTERNAL_ERROR", "the recommender timed out", 504) from None


# Input: raw (bytes)
# Output: dict[str, Any]
# Processing: Parse a JSON object, or fail as a protocol error.
# Changed by: Nirmal
# Date: 2026-10-31
def _json_body(raw: bytes) -> dict[str, Any]:
    try:
        parsed = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise UpstreamError("INTERNAL_ERROR", "the recommender returned invalid JSON", 502) from None
    if not isinstance(parsed, dict):
        raise UpstreamError("INTERNAL_ERROR", "the recommender returned a non-object body", 502)
    return parsed


# Input: payload (dict[str, Any]), status (int)
# Output: tuple[str, str]
# Processing: Read B06's §16.2 error object, falling back to the status.
# Changed by: Nirmal
# Date: 2026-10-31
def _error_fields(payload: dict[str, Any], status: int) -> tuple[str, str]:
    found = payload.get("error")
    if isinstance(found, dict):
        code = found.get("code")
        message = found.get("message")
        if isinstance(code, str) and isinstance(message, str):
            return code, message
    return _code_for_status(status), f"the recommender returned HTTP {status}"


# Input: status (int)
# Output: str
# Processing: The §16.2 code that best matches an HTTP status.
# Changed by: Nirmal
# Date: 2026-10-31
def _code_for_status(status: int) -> str:
    return {
        400: "INVALID_REQUEST",
        401: "UNAUTHENTICATED",
        403: "FORBIDDEN",
        404: "NOT_FOUND",
        409: "CONFLICT",
        413: "INVALID_REQUEST",
        422: "INVALID_REQUEST",
        429: "RATE_LIMITED",
    }.get(status, "INTERNAL_ERROR")
