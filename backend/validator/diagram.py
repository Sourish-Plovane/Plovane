"""B11, the deterministic diagram validator (handbook §10).

§10 defines the result shape, not the checking logic, so the rules here are
B11's own and deliberately structural: required fields, the permitted node
types, unique node ids, and edges that point at nodes in the same diagram.
The relationship enumeration is still open (OD-2), so any non-empty string is
accepted. No transport is assumed: the handbook leaves it unfinalized.

    from backend.validator.diagram import validate_diagram
    result = validate_diagram({"project_id": "proj_123", "diagram": {...}})
"""

from __future__ import annotations

from typing import Any

NODE_TYPES = ("function", "file", "folder")

# Input: none
# Output: dict[str, str]
# Processing: The issue codes consumers may rely on; keep them stable.
# Changed by: Nirmal
# Date: 2026-10-31
ISSUE_CODES = {
    "INVALID_DIAGRAM": "the payload or its diagram is missing or not an object",
    "MISSING_FIELD": "a required field is missing, empty, or of the wrong type",
    "INVALID_NODE_TYPE": "node_type is not one of the permitted values",
    "DUPLICATE_NODE_ID": "two nodes share a node_id",
    "INVALID_EDGE_REFERENCE": "an edge names a node that is not in this diagram",
}


# Input: code (str), message (str), node_id (str | None)
# Output: dict[str, Any]
# Processing: One §10.2 issue object.
# Changed by: Nirmal
# Date: 2026-10-31
def issue(code: str, message: str, node_id: str | None = None) -> dict[str, Any]:
    return {"code": code, "message": message, "node_id": node_id}


# Input: node (Any), index (int), seen (set[str])
# Output: tuple[list[dict[str, Any]], str | None]
# Processing: Check one node and return its issues plus its usable id.
# Changed by: Nirmal
# Date: 2026-10-31
def _check_node(node: Any, index: int, seen: set[str]) -> tuple[list[dict[str, Any]], str | None]:
    found: list[dict[str, Any]] = []
    if not isinstance(node, dict):
        found.append(issue("MISSING_FIELD", f"nodes[{index}] must be an object"))
        return found, None

    node_id = node.get("node_id")
    usable_id: str | None = None
    if not isinstance(node_id, str) or not node_id:
        found.append(issue("MISSING_FIELD", f"nodes[{index}].node_id must be a non-empty string"))
    elif node_id in seen:
        found.append(issue("DUPLICATE_NODE_ID", f"node_id '{node_id}' is used more than once", node_id))
    else:
        seen.add(node_id)
        usable_id = node_id

    label = node.get("label")
    if not isinstance(label, str) or not label:
        found.append(issue("MISSING_FIELD", f"nodes[{index}].label must be a non-empty string", usable_id))

    node_type = node.get("node_type")
    if node_type not in NODE_TYPES:
        found.append(issue("INVALID_NODE_TYPE",
                           f"nodes[{index}].node_type must be one of {', '.join(NODE_TYPES)}", usable_id))

    source_path = node.get("source_path")
    if not isinstance(source_path, str) or not source_path:
        found.append(issue("MISSING_FIELD",
                           f"nodes[{index}].source_path must be a non-empty string", usable_id))

    return found, usable_id


# Input: edge (Any), index (int), node_ids (set[str])
# Output: list[dict[str, Any]]
# Processing: Check one edge's endpoint references and relationship.
# Changed by: Nirmal
# Date: 2026-10-31
def _check_edge(edge: Any, index: int, node_ids: set[str]) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    if not isinstance(edge, dict):
        found.append(issue("MISSING_FIELD", f"edges[{index}] must be an object"))
        return found

    for field in ("source_node_id", "target_node_id"):
        endpoint = edge.get(field)
        if not isinstance(endpoint, str) or not endpoint:
            found.append(issue("MISSING_FIELD", f"edges[{index}].{field} must be a non-empty string"))
        elif endpoint not in node_ids:
            found.append(issue("INVALID_EDGE_REFERENCE",
                               f"edges[{index}].{field} '{endpoint}' is not a node in this diagram",
                               endpoint))

    relationship = edge.get("relationship")
    if not isinstance(relationship, str) or not relationship:
        found.append(issue("MISSING_FIELD",
                           f"edges[{index}].relationship must be a non-empty string"))

    return found


# Input: payload (Any)
# Output: dict[str, Any]
# Processing: Validate a canonical diagram (§5.4) and return the §10.2 result.
# Changed by: Nirmal
# Date: 2026-10-31
def validate_diagram(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return {"project_id": "", "valid": False,
                "issues": [issue("INVALID_DIAGRAM", "the body must be a JSON object")]}

    project_id = payload.get("project_id")
    issues: list[dict[str, Any]] = []
    if not isinstance(project_id, str) or not project_id:
        issues.append(issue("MISSING_FIELD", "project_id is required and must be a non-empty string"))
        project_id = project_id if isinstance(project_id, str) else ""

    diagram = payload.get("diagram")
    if not isinstance(diagram, dict):
        issues.append(issue("INVALID_DIAGRAM", "diagram is required and must be an object"))
        return {"project_id": project_id, "valid": False, "issues": issues}

    nodes = diagram.get("nodes")
    if not isinstance(nodes, list):
        issues.append(issue("INVALID_DIAGRAM", "diagram.nodes is required and must be an array"))
        nodes = []

    edges = diagram.get("edges")
    if not isinstance(edges, list):
        issues.append(issue("INVALID_DIAGRAM", "diagram.edges is required and must be an array"))
        edges = []

    node_ids: set[str] = set()
    for index, node in enumerate(nodes):
        node_issues, _ = _check_node(node, index, node_ids)
        issues.extend(node_issues)

    for index, edge in enumerate(edges):
        issues.extend(_check_edge(edge, index, node_ids))

    return {"project_id": project_id, "valid": not issues, "issues": issues}
