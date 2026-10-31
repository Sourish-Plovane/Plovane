"""B10 diagram generator tests."""

from __future__ import annotations

import pytest

pytest.importorskip("flask")

from ml.serving.service.app import create_app
from ml.serving.service.diagram import generate


# Input: none
# Output: dict
# Processing: A §9.1 project with two Python files that call each other.
# Changed by: Sourish
# Date: 2026-10-31
def project():
    return {
        "project_id": "proj_1",
        "files": [
            {"path": "src/main.py", "language": "python",
             "content": "from utils import helper\n\ndef main():\n    helper()\n"},
            {"path": "src/utils.py", "language": "python",
             "content": "def helper():\n    return 42\n"},
        ],
    }


# Input: none
# Output: None
# Processing: generate returns a §5.4 body with folders, files and functions.
# Changed by: Sourish
# Date: 2026-10-31
def test_generate_builds_canonical_diagram():
    result = generate(project())
    assert result["project_id"] == "proj_1"
    types = {n["node_type"] for n in result["diagram"]["nodes"]}
    assert types == {"folder", "file", "function"}
    labels = {n["label"] for n in result["diagram"]["nodes"] if n["node_type"] == "function"}
    assert labels == {"main", "helper"}


# Input: none
# Output: None
# Processing: A cross-file Python call becomes a 'calls' edge.
# Changed by: Sourish
# Date: 2026-10-31
def test_generate_produces_call_edges_across_files():
    result = generate(project())
    calls = [e for e in result["diagram"]["edges"] if e["relationship"] == "calls"]
    assert len(calls) == 1
    assert "main" in calls[0]["source_node_id"]
    assert "helper" in calls[0]["target_node_id"]


# Input: none
# Output: None
# Processing: Folder nodes exist and 'contains' edges mirror the hierarchy.
# Changed by: Sourish
# Date: 2026-10-31
def test_generate_adds_contains_edges():
    result = generate(project())
    contains = [e for e in result["diagram"]["edges"] if e["relationship"] == "contains"]
    assert any(e["target_node_id"].startswith("file:") for e in contains)
    assert any(e["target_node_id"].startswith("func:") for e in contains)


# Input: none
# Output: None
# Processing: Non-Python files become file-level nodes with no functions.
# Changed by: Sourish
# Date: 2026-10-31
def test_non_python_file_becomes_file_node_only():
    result = generate({"project_id": "p",
                       "files": [{"path": "README.md", "language": "markdown",
                                  "content": "# hi"}]})
    types = [n["node_type"] for n in result["diagram"]["nodes"]]
    assert "function" not in types
    assert any(n["node_type"] == "file" for n in result["diagram"]["nodes"])


# Input: none
# Output: None
# Processing: /v1/diagram/generate returns a §5.4 body end to end.
# Changed by: Sourish
# Date: 2026-10-31
def test_http_route_returns_canonical_diagram():
    client = create_app().test_client()
    reply = client.post("/v1/diagram/generate", json=project())
    assert reply.status_code == 200
    body = reply.get_json()
    assert body["project_id"] == "proj_1"
    assert any(n["node_type"] == "function" for n in body["diagram"]["nodes"])


# Input: none
# Output: None
# Processing: A bad payload is a 400 with the §16.2 error body.
# Changed by: Sourish
# Date: 2026-10-31
def test_http_route_rejects_bad_payload():
    client = create_app().test_client()
    reply = client.post("/v1/diagram/generate", json={"project_id": "p"})
    assert reply.status_code == 400
    assert reply.get_json()["error"]["code"] == "INVALID_REQUEST"


# Input: none
# Output: None
# Processing: Syntactically broken Python still yields a file node.
# Changed by: Sourish
# Date: 2026-10-31
def test_broken_python_falls_back_to_file_node():
    result = generate({"project_id": "p",
                       "files": [{"path": "x.py", "language": "python",
                                  "content": "def(:\n"}]})
    types = {n["node_type"] for n in result["diagram"]["nodes"]}
    assert "file" in types
    assert "function" not in types
