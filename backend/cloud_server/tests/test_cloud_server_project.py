"""B05 project + credits route tests."""

from __future__ import annotations

import pytest

pytest.importorskip("flask")

from backend.credits.engine import Engine as CreditsEngine
from backend.credits.repo import Repo as CreditsRepo
from backend.cloud_server.app import create_app
from backend.cloud_server.identity import IdentityStore
from backend.project_ingestion.service import IngestionService


# Input: body (dict | None)
# Output: FakeUpstream
# Processing: Minimal recommender stand-in for app construction.
# Changed by: Nirmal
# Date: 2026-10-31
class FakeUpstream:
    def __init__(self, body=None):
        self.body = body or {"request_id": "req_1", "recommendations": []}

    def recommend(self, body):
        return self.body


# Input: none
# Output: None
# Processing: /v1/project ingests and /v1/project/<id>/diagram returns §5.4.
# Changed by: Nirmal
# Date: 2026-10-31
def test_ingest_then_fetch_diagram():
    client = create_app(upstream=FakeUpstream(), identity=IdentityStore(),
                        ingestion=IngestionService()).test_client()
    reply = client.post("/v1/project",
                        json={"project_id": "proj_1",
                              "files": [{"path": "src/x.py", "content": "pass"}]})
    assert reply.status_code == 200
    assert reply.get_json()["status"] == "accepted"

    reply = client.get("/v1/project/proj_1/diagram")
    assert reply.status_code == 200
    body = reply.get_json()
    assert body["project_id"] == "proj_1"
    assert body["diagram"]["nodes"][0]["source_path"] == "src/x.py"


# Input: none
# Output: None
# Processing: Unknown project returns 404 with NOT_FOUND.
# Changed by: Nirmal
# Date: 2026-10-31
def test_unknown_project_is_404():
    client = create_app(upstream=FakeUpstream(), identity=IdentityStore(),
                        ingestion=IngestionService()).test_client()
    reply = client.get("/v1/project/ghost/diagram")
    assert reply.status_code == 404
    assert reply.get_json()["error"]["code"] == "NOT_FOUND"


# Input: none
# Output: None
# Processing: /v1/contributions records a §15.1 and returns the §15.2 outcome.
# Changed by: Nirmal
# Date: 2026-10-31
def test_contributions_round_trip():
    credits = CreditsRepo(CreditsEngine())
    client = create_app(upstream=FakeUpstream(), identity=IdentityStore(),
                        credits=credits).test_client()
    reply = client.post("/v1/contributions", json={
        "contribution_id": "contrib_1",
        "user_id": "usr_1",
        "source": "manual_submission",
        "code_blocks": [{"block_id": "blk_1"}],
    })
    assert reply.status_code == 200
    assert reply.get_json()["credit_outcome"]["status"] == "pending"
    credits.mark_awarded("contrib_1")

    reply = client.get("/v1/credits/usr_1")
    assert reply.status_code == 200
    assert reply.get_json()["balances"] == [{"credit_type": "compute", "amount": 1}]
