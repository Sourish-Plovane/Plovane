"""B11's deterministic checks against the §10.2 result shape."""

from __future__ import annotations

from backend.validator.diagram import validate_diagram


# Input: none
# Output: dict
# Processing: A small valid canonical diagram (§5.4).
# Changed by: Nirmal
# Date: 2026-10-31
def sample():
    return {
        "project_id": "proj_123",
        "diagram": {
            "nodes": [
                {"node_id": "n1", "label": "main", "node_type": "file", "source_path": "src/main.py"},
                {"node_id": "n2", "label": "load", "node_type": "function", "source_path": "src/main.py"},
            ],
            "edges": [{"source_node_id": "n1", "target_node_id": "n2", "relationship": "calls"}],
        },
    }


# Input: none
# Output: list[str]
# Processing: The issue codes in a result.
# Changed by: Nirmal
# Date: 2026-10-31
def codes(result):
    return [i["code"] for i in result["issues"]]


# Input: none
# Output: None
# Processing: Check the happy path.
# Changed by: Nirmal
# Date: 2026-10-31
def test_valid_diagram_passes():
    assert validate_diagram(sample()) == {"project_id": "proj_123", "valid": True, "issues": []}


# Input: none
# Output: None
# Processing: Check a body that is not an object.
# Changed by: Nirmal
# Date: 2026-10-31
def test_non_object_body():
    result = validate_diagram([])
    assert result["valid"] is False and codes(result) == ["INVALID_DIAGRAM"]


# Input: none
# Output: None
# Processing: Check the missing project_id and diagram cases.
# Changed by: Nirmal
# Date: 2026-10-31
def test_missing_fields():
    assert "MISSING_FIELD" in codes(validate_diagram({"diagram": sample()["diagram"]}))
    assert "INVALID_DIAGRAM" in codes(validate_diagram({"project_id": "proj_123"}))


# Input: none
# Output: None
# Processing: Check node-level rules.
# Changed by: Nirmal
# Date: 2026-10-31
def test_node_rules():
    body = sample()
    body["diagram"]["nodes"][0]["node_type"] = "module"
    body["diagram"]["nodes"][1]["node_id"] = "n1"
    result = codes(validate_diagram(body))
    assert "INVALID_NODE_TYPE" in result
    assert "DUPLICATE_NODE_ID" in result


# Input: none
# Output: None
# Processing: Check that an edge to an unknown node is refused.
# Changed by: Nirmal
# Date: 2026-10-31
def test_edge_reference_must_exist():
    body = sample()
    body["diagram"]["edges"][0]["target_node_id"] = "n404"
    result = validate_diagram(body)
    assert result["valid"] is False
    assert "INVALID_EDGE_REFERENCE" in codes(result)


# Input: none
# Output: None
# Processing: Check that an unknown relationship passes while OD-2 is open.
# Changed by: Nirmal
# Date: 2026-10-31
def test_relationship_enum_is_open():
    body = sample()
    body["diagram"]["edges"][0]["relationship"] = "imports"
    assert validate_diagram(body)["valid"] is True
