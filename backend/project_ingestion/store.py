"""Project store for B09.

Keeps the §5.4 diagram and the §10.2 validation result side by side under a
project_id. Pluggable: tests pass InMemoryStore; prod can swap in a sqlite
implementation later. The handbook §5.5 submission accepts bytes before the
diagram exists, so the store treats the diagram and the validation result as
optional until B10 and B11 run.
"""

from __future__ import annotations

import threading
from typing import Any, Protocol


# Input: project_id (str)
# Output: dict | None
# Processing: The store interface B09 depends on; injected in tests.
# Changed by: Nirmal
# Date: 2026-10-31
class ProjectStore(Protocol):
    def put(self, project_id: str, record: dict[str, Any]) -> None: ...
    def update(self, project_id: str, patch: dict[str, Any]) -> None: ...
    def get(self, project_id: str) -> dict[str, Any] | None: ...


# Input: none
# Output: InMemoryStore
# Processing: Thread-safe dict backed project store.
# Changed by: Nirmal
# Date: 2026-10-31
class InMemoryStore:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._rows: dict[str, dict[str, Any]] = {}

    # Input: project_id (str), record (dict)
    # Output: None
    # Processing: Replace the record for a project id.
    # Changed by: Nirmal
    # Date: 2026-10-31
    def put(self, project_id: str, record: dict[str, Any]) -> None:
        with self._lock:
            self._rows[project_id] = dict(record)

    # Input: project_id (str), patch (dict)
    # Output: None
    # Processing: Merge a patch into the record for a project id.
    # Changed by: Nirmal
    # Date: 2026-10-31
    def update(self, project_id: str, patch: dict[str, Any]) -> None:
        with self._lock:
            current = dict(self._rows.get(project_id, {}))
            current.update(patch)
            self._rows[project_id] = current

    # Input: project_id (str)
    # Output: dict | None
    # Processing: Return a shallow copy of the stored record.
    # Changed by: Nirmal
    # Date: 2026-10-31
    def get(self, project_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._rows.get(project_id)
            return dict(row) if row else None
