"""Liveness probes for B03, B05, and B06.

Each service exposes a GET endpoint that returns the §4.1 envelope. The
probe here only cares whether the request succeeds within a short
timeout; the body is returned so callers can show the version.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any

HEALTH_PATHS: dict[str, str] = {
    "local": "/api/v1/health",
    "cloud": "/healthz",
    "serving": "/healthz",
}


@dataclass
class HealthResult:
    name: str
    url: str
    ok: bool
    status: int | None
    detail: str


# Input: endpoints (dict[str, str]), timeout (float)
# Output: list[HealthResult]
# Processing: Probe every endpoint sequentially and collect the result.
# Changed by: Sourish
# Date: 2026-10-31
def probe_all(endpoints: dict[str, str], timeout: float = 1.5) -> list[HealthResult]:
    results: list[HealthResult] = []
    for name, base in endpoints.items():
        path = HEALTH_PATHS.get(name, "/health")
        results.append(_probe_one(name, base.rstrip("/") + path, timeout))
    return results


# Input: name (str), url (str), timeout (float)
# Output: HealthResult
# Processing: GET the URL and classify the outcome without raising.
# Changed by: Sourish
# Date: 2026-10-31
def _probe_one(name: str, url: str, timeout: float) -> HealthResult:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as reply:
            body = reply.read().decode("utf-8", errors="replace")
            detail = _summarise(body)
            return HealthResult(name=name, url=url, ok=True, status=reply.status, detail=detail)
    except urllib.error.HTTPError as error:
        return HealthResult(name=name, url=url, ok=False, status=error.code, detail=error.reason)
    except urllib.error.URLError as error:
        return HealthResult(name=name, url=url, ok=False, status=None, detail=str(error.reason))
    except TimeoutError:
        return HealthResult(name=name, url=url, ok=False, status=None, detail="timeout")
    except OSError as error:
        return HealthResult(name=name, url=url, ok=False, status=None, detail=str(error))


# Input: body (str)
# Output: str
# Processing: Pull a short detail string from a JSON envelope when present.
# Changed by: Sourish
# Date: 2026-10-31
def _summarise(body: str) -> str:
    try:
        payload: Any = json.loads(body)
    except ValueError:
        return body[:60]
    if isinstance(payload, dict):
        data = payload.get("data")
        if isinstance(data, dict):
            parts = [f"{k}={v}" for k, v in list(data.items())[:3]]
            return ", ".join(parts) or "ok"
        if payload.get("ok") is True:
            return "ok"
    return str(payload)[:60]
