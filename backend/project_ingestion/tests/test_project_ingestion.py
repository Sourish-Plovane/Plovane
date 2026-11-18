"""B09 service tests, with the B10 stub."""

from __future__ import annotations

import pytest

from backend.project_ingestion.language import detect_language
from backend.project_ingestion.service import (
    IngestionError,
    IngestionService,
    to_ingestion_input,
    validate_submission,
)


# Input: none
# Output: dict
# Processing: A §5.5 submission with one Python file.
# Changed by: Nirmal
# Date: 2026-10-31
def submission():
    return {
        "project_id": "proj_1",
        "files": [{"path": "src/main.py", "content": "def main():\n    pass"}],
    }


# Input: none
# Output: None
# Processing: detect_language maps common extensions.
# Changed by: Nirmal
# Date: 2026-10-31
def test_detect_language_maps_common_extensions():
    assert detect_language("src/x.py") == "python"
    assert detect_language("app/index.ts") == "typescript"
    assert detect_language("README") == "text"


# Input: none
# Output: None
# Processing: validate_submission accepts a canonical body.
# Changed by: Nirmal
# Date: 2026-10-31
def test_validate_submission_accepts_a_canonical_body():
    assert validate_submission(submission())["project_id"] == "proj_1"


# Input: none
# Output: None
# Processing: validate_submission rejects an empty files array.
# Changed by: Nirmal
# Date: 2026-10-31
def test_validate_submission_rejects_empty_files():
    with pytest.raises(IngestionError):
        validate_submission({"project_id": "proj_1", "files": []})


# Input: none
# Output: None
# Processing: ingest returns an accepted submission with a §5.4 diagram.
# Changed by: Nirmal
# Date: 2026-10-31
def test_ingest_returns_accepted_and_stores_diagram():
    service = IngestionService()
    reply = service.ingest(submission())
    assert reply["status"] == "accepted"
    assert reply["project_id"] == "proj_1"
    diagram = service.diagram("proj_1")
    assert diagram["project_id"] == "proj_1"
    assert diagram["diagram"]["nodes"][0]["source_path"] == "src/main.py"
    assert service.validation("proj_1")["valid"] is True


# Input: none
# Output: None
# Processing: to_ingestion_input adds the §9.1 language field per file.
# Changed by: Nirmal
# Date: 2026-10-31
def test_to_ingestion_input_adds_language():
    enriched = to_ingestion_input(validate_submission(submission()))
    assert enriched["files"][0]["language"] == "python"
