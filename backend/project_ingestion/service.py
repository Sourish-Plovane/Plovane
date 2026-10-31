"""B09 service: ingest a §5.5 submission, build a §5.4 diagram.

Pipeline, in order:
    §5.5 submission -> add §9.1 ``language`` -> B10 diagram -> B11 validator
                     -> project store -> §5.4 on request

B10 is a stub by default (DiagramClient's Stub implementation) so the pipeline
is testable before Sourish ships B10. B11 is already built; we import it.
"""

from __future__ import annotations

import os
import uuid
from typing import Any

from backend.project_ingestion.diagram_client import (
    DiagramClient,
    HttpDiagramClient,
    StubDiagramClient,
)
from backend.project_ingestion.language import detect_language
from backend.project_ingestion.store import InMemoryStore, ProjectStore
from backend.validator.diagram import validate_diagram


# Input: none
# Output: DiagramClient
# Processing: HttpDiagramClient when PLOVANE_B10_URL is set, else the stub.
# Changed by: Sourish
# Date: 2026-10-31
def default_diagram_client() -> DiagramClient:
    url = os.environ.get("PLOVANE_B10_URL")
    return HttpDiagramClient(url) if url else StubDiagramClient()


# Input: message (str), code (str)
# Output: IngestionError
# Processing: A §5.5 payload that cannot be ingested; code is §16.2.
# Changed by: Nirmal
# Date: 2026-10-31
class IngestionError(ValueError):
    """A §5.5 payload that breaks the ingestion contract."""

    # Input: message (str), code (str)
    # Output: None
    # Processing: Carry the §16.2 code alongside the human message.
    # Changed by: Nirmal
    # Date: 2026-10-31
    def __init__(self, message: str, code: str = "INVALID_REQUEST") -> None:
        super().__init__(message)
        self.code = code


# Input: raw (Any)
# Output: dict
# Processing: Validate a §5.5 submission.
# Changed by: Nirmal
# Date: 2026-10-31
def validate_submission(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise IngestionError("the body must be a JSON object")
    project_id = raw.get("project_id")
    if not isinstance(project_id, str) or not project_id:
        raise IngestionError("project_id is required and must be a non-empty string")
    files = raw.get("files")
    if not isinstance(files, list) or not files:
        raise IngestionError("files is required and must be a non-empty array")
    cleaned: list[dict[str, Any]] = []
    for index, file in enumerate(files):
        if not isinstance(file, dict):
            raise IngestionError(f"files[{index}] must be an object")
        path = file.get("path")
        content = file.get("content")
        if not isinstance(path, str) or not path:
            raise IngestionError(f"files[{index}].path must be a non-empty string")
        if not isinstance(content, str):
            raise IngestionError(f"files[{index}].content must be a string")
        cleaned.append({"path": path, "content": content})
    return {"project_id": project_id, "files": cleaned}


# Input: submission (dict)
# Output: dict
# Processing: Add §9.1 ``language`` to each file.
# Changed by: Nirmal
# Date: 2026-10-31
def to_ingestion_input(submission: dict[str, Any]) -> dict[str, Any]:
    return {
        "project_id": submission["project_id"],
        "files": [
            {"path": file["path"],
             "language": detect_language(file["path"]),
             "content": file["content"]}
            for file in submission["files"]
        ],
    }


# Input: none
# Output: str
# Processing: Opaque submission id for §5.5 responses.
# Changed by: Nirmal
# Date: 2026-10-31
def new_submission_id() -> str:
    return "sub_" + uuid.uuid4().hex


# Input: diagram_client (DiagramClient | None), store (ProjectStore | None)
# Output: IngestionService
# Processing: Wire the ingest pipeline from §5.5 to §5.4 on request.
# Changed by: Nirmal
# Date: 2026-10-31
class IngestionService:
    # Input: diagram_client (DiagramClient | None), store (ProjectStore | None)
    # Output: None
    # Processing: Default the diagram client to the stub and the store to in-memory.
    # Changed by: Nirmal
    # Date: 2026-10-31
    def __init__(self, diagram_client: DiagramClient | None = None,
                 store: ProjectStore | None = None) -> None:
        self.diagram_client = diagram_client or default_diagram_client()
        self.store = store or InMemoryStore()

    # Input: raw (Any)
    # Output: dict
    # Processing: Full §5.5 pipeline; returns the §5.5 success response.
    # Changed by: Nirmal
    # Date: 2026-10-31
    def ingest(self, raw: Any) -> dict[str, Any]:
        submission = validate_submission(raw)
        ingestion = to_ingestion_input(submission)
        diagram_body = self.diagram_client.generate(ingestion)
        diagram = diagram_body.get("diagram") or {"nodes": [], "edges": []}
        canonical = {"project_id": submission["project_id"], "diagram": diagram}
        validation = validate_diagram(canonical)
        submission_id = new_submission_id()
        self.store.put(submission["project_id"], {
            "submission_id": submission_id,
            "project_id": submission["project_id"],
            "diagram": diagram,
            "validation": validation,
        })
        return {
            "submission_id": submission_id,
            "project_id": submission["project_id"],
            "status": "accepted",
        }

    # Input: project_id (str)
    # Output: dict | None
    # Processing: Return the §5.4 diagram, or None if no project is stored.
    # Changed by: Nirmal
    # Date: 2026-10-31
    def diagram(self, project_id: str) -> dict[str, Any] | None:
        row = self.store.get(project_id)
        if row is None:
            return None
        return {"project_id": project_id, "diagram": row.get("diagram") or {"nodes": [], "edges": []}}

    # Input: project_id (str)
    # Output: dict | None
    # Processing: Return the stored §10.2 validation result, or None.
    # Changed by: Nirmal
    # Date: 2026-10-31
    def validation(self, project_id: str) -> dict[str, Any] | None:
        row = self.store.get(project_id)
        if row is None:
            return None
        return row.get("validation")
