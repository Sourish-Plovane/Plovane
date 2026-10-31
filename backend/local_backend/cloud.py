"""B03's HTTPS JSON client for the cloud server (handbook §4.2).

Only the standard library is used, so the local backend keeps the small
dependency footprint its README asks for. The client returns the parsed JSON
body for 2xx answers and raises :class:`CloudError` otherwise. Mapping that
error onto the §4.1 envelope is the app's job (gap G9).
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any

from backend.local_backend.contracts import code_for_status


# Input: code (str), message (str), status (int)
# Output: CloudError
# Processing: A failed cloud call with its §16.2 code and HTTP status.
# Changed by: Nirmal
# Date: 2026-10-31
class CloudError(RuntimeError):
    """The cloud call failed. ``code`` is a §16.2 code."""

    # Input: code (str), message (str), status (int)
    # Output: None
    # Processing: Carry the §16.2 code and the HTTP status alongside the message.
    # Changed by: Nirmal
    # Date: 2026-10-31
    def __init__(self, code: str, message: str, status: int = 502) -> None:
        super().__init__(message)
        self.code = code
        self.status = status


# Input: base_url (str), timeout (float)
# Output: HttpCloudClient
# Processing: Build a client for one cloud server.
# Changed by: Nirmal
# Date: 2026-10-31
class HttpCloudClient:
    # Input: base_url (str), timeout (float)
    # Output: None
    # Processing: Normalise the base URL and remember the HTTP timeout.
    # Changed by: Nirmal
    # Date: 2026-10-31
    def __init__(self, base_url: str, timeout: float = 30.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.session_token: str | None = None

    # Input: token (str | None)
    # Output: None
    # Processing: Remember the §8.3 bearer token for later cloud calls.
    # Changed by: Nirmal
    # Date: 2026-10-31
    def set_session(self, token: str | None) -> None:
        self.session_token = token or None

    # Input: path (str), body (dict[str, Any] | None), method (str)
    # Output: dict[str, Any]
    # Processing: Send JSON to the cloud; return the parsed response.
    # Changed by: Nirmal
    # Date: 2026-10-31
    def _call(self, path: str, body: dict[str, Any] | None,
              method: str = "POST") -> dict[str, Any]:
        headers = {"Accept": "application/json"}
        data: bytes | None = None
        if body is not None:
            data = json.dumps(body).encode("utf-8")
            headers["Content-Type"] = "application/json"
        if self.session_token:
            headers["Authorization"] = f"Bearer {self.session_token}"
        request = urllib.request.Request(self.base_url + path, data=data,
                                         headers=headers, method=method)
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as reply:
                return _json_body(reply.read())
        except urllib.error.HTTPError as exc:
            payload = _json_body(exc.read()) if exc.fp is not None else {}
            code, message = _error_fields(payload, exc.code)
            raise CloudError(code, message, exc.code) from None
        except urllib.error.URLError as exc:
            raise CloudError("INTERNAL_ERROR",
                             f"cannot reach the cloud server: {exc.reason}", 503) from None
        except TimeoutError:
            raise CloudError("INTERNAL_ERROR", "the cloud server timed out", 504) from None

    # Input: body (dict[str, Any])
    # Output: dict[str, Any]
    # Processing: POST the §8.1 body and return the §8.2 JSON.
    # Changed by: Nirmal
    # Date: 2026-10-31
    def post_recommendation(self, body: dict[str, Any]) -> dict[str, Any]:
        return self._call("/v1/recommend", body)

    # Input: body (dict[str, Any])
    # Output: dict[str, Any]
    # Processing: POST the §5.5 project submission.
    # Changed by: Nirmal
    # Date: 2026-10-31
    def post_project(self, body: dict[str, Any]) -> dict[str, Any]:
        return self._call("/v1/project", body)

    # Input: project_id (str)
    # Output: dict[str, Any]
    # Processing: GET the stored §5.4 diagram for a project.
    # Changed by: Nirmal
    # Date: 2026-10-31
    def get_diagram(self, project_id: str) -> dict[str, Any]:
        return self._call(f"/v1/project/{project_id}/diagram", None, method="GET")

    # Input: body (dict[str, Any])
    # Output: dict[str, Any]
    # Processing: POST §8.3 login; the response carries session_token.
    # Changed by: Nirmal
    # Date: 2026-10-31
    def login(self, body: dict[str, Any]) -> dict[str, Any]:
        return self._call("/v1/auth/login", body)

    # Input: body (dict[str, Any])
    # Output: dict[str, Any]
    # Processing: POST §8.3 registration; returns the new user_id.
    # Changed by: Nirmal
    # Date: 2026-10-31
    def register(self, body: dict[str, Any]) -> dict[str, Any]:
        return self._call("/v1/auth/register", body)


# Input: raw (bytes)
# Output: dict[str, Any]
# Processing: Parse a JSON object, or fail as a protocol error.
# Changed by: Nirmal
# Date: 2026-10-31
def _json_body(raw: bytes) -> dict[str, Any]:
    try:
        parsed = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise CloudError("INTERNAL_ERROR", "the cloud server returned invalid JSON", 502) from None
    if not isinstance(parsed, dict):
        raise CloudError("INTERNAL_ERROR", "the cloud server returned a non-object body", 502)
    return parsed


# Input: payload (dict[str, Any]), status (int)
# Output: tuple[str, str]
# Processing: Read the cloud's §16.2 error object, falling back to the status.
# Changed by: Nirmal
# Date: 2026-10-31
def _error_fields(payload: dict[str, Any], status: int) -> tuple[str, str]:
    found = payload.get("error")
    if isinstance(found, dict):
        code = found.get("code")
        message = found.get("message")
        if isinstance(code, str) and isinstance(message, str):
            return code, message
    return code_for_status(status), f"the cloud server returned HTTP {status}"
