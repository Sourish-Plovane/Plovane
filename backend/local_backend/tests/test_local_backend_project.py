"""B03 project + diagram + §7 MCP + auth route tests."""

from __future__ import annotations

import pytest

pytest.importorskip("flask")

from backend.local_backend.app import create_app
from backend.local_backend.cloud import CloudError


# Input: none
# Output: FakeCloud
# Processing: Cloud double that scripts replies per method.
# Changed by: Nirmal
# Date: 2026-10-31
class FakeCloud:
    def __init__(self):
        self.session_token = None
        self.replies: dict[str, object] = {}
        self.seen: list[tuple[str, object]] = []

    def set_session(self, token):
        self.session_token = token

    def post_recommendation(self, body):
        self.seen.append(("recommend", body))
        return self.replies.get("recommend", {"request_id": "req_x", "recommendations": []})

    def post_project(self, body):
        self.seen.append(("project", body))
        return self.replies.get("project",
                                {"submission_id": "sub_x", "project_id": body.get("project_id"),
                                 "status": "accepted"})

    def get_diagram(self, project_id):
        self.seen.append(("diagram", project_id))
        return self.replies.get("diagram",
                                {"project_id": project_id, "diagram": {"nodes": [], "edges": []}})

    def login(self, body):
        self.seen.append(("login", body))
        return self.replies.get("login",
                                {"user_id": "usr_x",
                                 "session_token": "token_x",
                                 "expires_at": "2026-11-01T00:00:00Z"})

    def register(self, body):
        self.seen.append(("register", body))
        return self.replies.get("register", {"user_id": "usr_x"})


# Input: none
# Output: dict
# Processing: A valid §5.5 project submission.
# Changed by: Nirmal
# Date: 2026-10-31
def submission():
    return {"project_id": "proj_1",
            "files": [{"path": "src/main.py", "content": "def m():\n    pass"}]}


# Input: none
# Output: None
# Processing: /api/v1/projects wraps the cloud reply in the §4.1 envelope.
# Changed by: Nirmal
# Date: 2026-10-31
def test_project_submission_wraps_envelope():
    cloud = FakeCloud()
    client = create_app(cloud=cloud).test_client()
    reply = client.post("/api/v1/projects", json=submission())
    assert reply.status_code == 200
    body = reply.get_json()
    assert body["ok"] is True
    assert body["data"]["status"] == "accepted"
    assert cloud.seen[0][0] == "project"


# Input: none
# Output: None
# Processing: /api/v1/projects/<id>/diagram reads a §5.4 body.
# Changed by: Nirmal
# Date: 2026-10-31
def test_diagram_read_wraps_envelope():
    cloud = FakeCloud()
    cloud.replies["diagram"] = {"project_id": "proj_1",
                                "diagram": {"nodes": [{"node_id": "n1", "label": "x",
                                                       "node_type": "file",
                                                       "source_path": "x.py"}],
                                            "edges": []}}
    client = create_app(cloud=cloud).test_client()
    reply = client.get("/api/v1/projects/proj_1/diagram")
    assert reply.status_code == 200
    body = reply.get_json()
    assert body["ok"] is True
    assert body["data"]["diagram"]["nodes"][0]["node_id"] == "n1"


# Input: none
# Output: None
# Processing: Login stores the token on the cloud client for later calls.
# Changed by: Nirmal
# Date: 2026-10-31
def test_login_stores_session_token():
    cloud = FakeCloud()
    client = create_app(cloud=cloud).test_client()
    reply = client.post("/api/v1/auth/login",
                        json={"email": "a@b.c", "password": "pw",
                              "machine_id": "m1"})
    assert reply.status_code == 200
    assert cloud.session_token == "token_x"


# Input: none
# Output: None
# Processing: §7 MCP envelope dispatches recommend_code through the cloud.
# Changed by: Nirmal
# Date: 2026-10-31
def test_mcp_router_dispatches_recommend_code():
    cloud = FakeCloud()
    cloud.replies["recommend"] = {"request_id": "req_x",
                                  "recommendations": [{"block_id": "blk_1"}]}
    client = create_app(cloud=cloud).test_client()
    reply = client.post("/api/v1/mcp", json={
        "operation": "recommend_code",
        "input": {
            "project_id": "proj_1",
            "description": "add csv parsing",
            "language": "python",
            "tech_stack": ["python"],
            "context_code": None,
            "requested_count": 1,
        },
    })
    assert reply.status_code == 200
    body = reply.get_json()
    assert body["ok"] is True
    assert body["data"]["recommendations"][0]["block_id"] == "blk_1"


# Input: none
# Output: None
# Processing: Unknown §7 operation is an INVALID_REQUEST 400 envelope.
# Changed by: Nirmal
# Date: 2026-10-31
def test_mcp_router_rejects_unknown_operation():
    client = create_app(cloud=FakeCloud()).test_client()
    reply = client.post("/api/v1/mcp", json={"operation": "nope", "input": {}})
    assert reply.status_code == 400
    body = reply.get_json()
    assert body["ok"] is False and body["error"]["code"] == "INVALID_REQUEST"


# Input: none
# Output: None
# Processing: A cloud 404 bubbles up through the diagram route.
# Changed by: Nirmal
# Date: 2026-10-31
def test_diagram_cloud_404_is_mapped():
    class Broken(FakeCloud):
        def get_diagram(self, project_id):
            raise CloudError("NOT_FOUND", "nope", 404)
    client = create_app(cloud=Broken()).test_client()
    reply = client.get("/api/v1/projects/ghost/diagram")
    assert reply.status_code == 404
    assert reply.get_json()["error"]["code"] == "NOT_FOUND"
