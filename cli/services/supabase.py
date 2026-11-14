"""Supabase connectivity probe for the plovane CLI.

The project's cloud (B05) is where Supabase is ultimately wired in. This
module only verifies, from the CLI side, that the project URL + anon key
on disk reach a live gotrue instance:

    GET <url>/auth/v1/health      -- gotrue liveness
    GET <url>/rest/v1/            -- PostgREST liveness, with apikey

Neither call stores anything. Any failure returns a ``SupabaseResult`` with
``ok=False`` so the ``supabase ping`` command can print a diagnostic.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass


@dataclass
class SupabaseResult:
    url: str
    ok: bool
    status: int | None
    detail: str


# Input: url (str), anon_key (str | None), timeout (float)
# Output: list[SupabaseResult]
# Processing: Probe gotrue and PostgREST on the given Supabase project URL.
# Changed by: Sourish
# Date: 2026-10-31
def ping(url: str, anon_key: str | None = None, timeout: float = 2.0) -> list[SupabaseResult]:
    results: list[SupabaseResult] = []
    results.append(_get(url.rstrip("/") + "/auth/v1/health", {}, timeout))
    headers: dict[str, str] = {}
    if anon_key:
        headers = {"apikey": anon_key, "Authorization": f"Bearer {anon_key}"}
    results.append(_get(url.rstrip("/") + "/rest/v1/", headers, timeout))
    return results


# Input: url (str), headers (dict[str, str]), timeout (float)
# Output: SupabaseResult
# Processing: Issue a GET and classify the outcome without raising.
# Changed by: Sourish
# Date: 2026-10-31
def _get(url: str, headers: dict[str, str], timeout: float) -> SupabaseResult:
    request = urllib.request.Request(url, headers=headers, method="GET")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as reply:
            body = reply.read().decode("utf-8", errors="replace")
            return SupabaseResult(url=url, ok=True, status=reply.status,
                                  detail=_summary(body))
    except urllib.error.HTTPError as http_error:
        ok = 200 <= http_error.code < 500
        return SupabaseResult(url=url, ok=ok, status=http_error.code,
                              detail=http_error.reason)
    except urllib.error.URLError as net_error:
        return SupabaseResult(url=url, ok=False, status=None, detail=str(net_error.reason))
    except TimeoutError:
        return SupabaseResult(url=url, ok=False, status=None, detail="timeout")
    except OSError as error:
        return SupabaseResult(url=url, ok=False, status=None, detail=str(error))


# Input: body (str)
# Output: str
# Processing: Short detail string. Prefers ``description`` from gotrue bodies.
# Changed by: Sourish
# Date: 2026-10-31
def _summary(body: str) -> str:
    try:
        payload = json.loads(body)
    except ValueError:
        return body[:60] if body else "ok"
    if isinstance(payload, dict):
        for key in ("description", "name", "status"):
            value = payload.get(key)
            if isinstance(value, str):
                return value
    return str(payload)[:60]
