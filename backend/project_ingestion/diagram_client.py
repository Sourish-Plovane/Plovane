"""B10 client with a built-in stub (handbook §9.1 in, §5.4 out).

Sourish owns B10. Until a real HTTP route exists on ``ml/serving``, this
module ships a stub that honours the §5.4 shape: one ``file`` node per source
file, no edges. B09 feeds the stub's result into B11 so the pipeline stays
exercised end to end.

The real client follows the same ``generate(body)`` method so the swap is one
line in the ingestion wiring.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from typing import Any, Protocol


# Input: body (dict)
# Output: dict
# Processing: The B10 call B09 makes; injected in tests.
# Changed by: Nirmal
# Date: 2026-10-31
class DiagramClient(Protocol):
    # Input: body (dict)
    # Output: dict
    # Processing: Protocol stub: implementations return a §5.4 diagram.
    # Changed by: Nirmal
    # Date: 2026-10-31
    def generate(self, body: dict[str, Any]) -> dict[str, Any]:
        ...


# Input: body (dict)
# Output: dict
# Processing: One §5.4 file-node per §9.1 file; no edges.
# Changed by: Nirmal
# Date: 2026-10-31
class StubDiagramClient:
    # Input: body (dict)
    # Output: dict
    # Processing: One §5.4 file-node per §9.1 file; no edges.
    # Changed by: Nirmal
    # Date: 2026-10-31
    def generate(self, body: dict[str, Any]) -> dict[str, Any]:
        project_id = body.get("project_id") or ""
        nodes = []
        for index, file in enumerate(body.get("files", []) or []):
            path = file.get("path") or f"file_{index}"
            nodes.append({
                "node_id": f"node_{index:03d}",
                "label": os.path.basename(path) or path,
                "node_type": "file",
                "source_path": path,
            })
        return {"project_id": project_id, "diagram": {"nodes": nodes, "edges": []}}


# Input: base_url (str), timeout (float)
# Output: HttpDiagramClient
# Processing: HTTPS client for a real B10 service once Sourish exposes it.
# Changed by: Nirmal
# Date: 2026-10-31
class HttpDiagramClient:
    # Input: base_url (str), timeout (float)
    # Output: None
    # Processing: Normalise the base URL and remember the HTTP timeout.
    # Changed by: Nirmal
    # Date: 2026-10-31
    def __init__(self, base_url: str, timeout: float = 60.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    # Input: body (dict)
    # Output: dict
    # Processing: POST §9.1 to ``/v1/diagram/generate`` and return §5.4.
    # Changed by: Nirmal
    # Date: 2026-10-31
    def generate(self, body: dict[str, Any]) -> dict[str, Any]:
        request = urllib.request.Request(
            self.base_url + "/v1/diagram/generate",
            data=json.dumps(body).encode("utf-8"),
            headers={"Content-Type": "application/json", "Accept": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as reply:
                return json.loads(reply.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            raise RuntimeError(f"B10 returned HTTP {exc.code}") from None
        except urllib.error.URLError as exc:
            raise RuntimeError(f"cannot reach B10: {exc.reason}") from None
