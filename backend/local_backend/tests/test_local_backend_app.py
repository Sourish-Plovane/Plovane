"""B03's HTTP surface, exercised with a fake cloud."""

from __future__ import annotations

import pytest

pytest.importorskip("flask")

from backend.local_backend.app import create_app
from backend.local_backend.cloud import CloudError


# Input: reply (dict), failure (CloudError | None)
# Output: FakeCloud
# Processing: A cloud double that records the body it was sent.
# Changed by: Nirmal
# Date: 2026-10-31
class FakeCloud:
    def __init__(self, reply=None, failure=None):
        self.reply = reply or {"request_id": "req_x", "recommendations": []}
        self.failure = failure
        self.seen = []

    # Input: body (dict)
    # Output: dict
    # Processing: Return the canned answer, or raise the canned failure.
    # Changed by: Nirmal
    # Date: 2026-10-31
    def post_recommendation(self, body):
        self.seen.append(body)
        if self.failure:
            raise self.failure
        return self.reply


# Input: none
# Output: dict
# Processing: A valid §5.1 body.
# Changed by: Nirmal
# Date: 2026-10-31
def sample():
    return {
        "project_id": "proj_123",
        "description": "Add CSV parsing",
        "language": "python",
        "tech_stack": ["python"],
        "context_code": None,
        "requested_count": 3,
    }


# Input: none
# Output: None
# Processing: Check the health envelope.
# Changed by: Nirmal
# Date: 2026-10-31
def test_health_uses_the_envelope():
    reply = create_app(cloud=FakeCloud()).test_client().get("/api/v1/health")
    assert reply.status_code == 200
    assert reply.get_json() == {"ok": True, "data": {"status": "ok"}, "error": None}


# Input: none
# Output: None
# Processing: Check §5.1 -> §8.1 -> §5.2 and the caller identity.
# Changed by: Nirmal
# Date: 2026-10-31
def test_recommendation_round_trip():
    cloud = FakeCloud({"request_id": "req_x", "recommendations": [{"block_id": "blk_1"}]})
    client = create_app(cloud=cloud, user_id="usr_test").test_client()
    reply = client.post("/api/v1/recommendations", json=sample(),
                        headers={"X-Plovane-User": "usr_header"})
    assert reply.status_code == 200
    assert reply.get_json() == {"ok": True, "data": {"recommendations": [{"block_id": "blk_1"}]},
                                "error": None}
    sent = cloud.seen[0]
    assert sent["request_id"].startswith("req_")
    assert sent["user_id"] == "usr_header"


# Input: none
# Output: None
# Processing: Check that an invalid §5.1 body is a 400 envelope.
# Changed by: Nirmal
# Date: 2026-10-31
def test_invalid_request_is_a_400_envelope():
    cloud = FakeCloud()
    reply = create_app(cloud=cloud).test_client().post("/api/v1/recommendations", json={})
    assert reply.status_code == 400
    body = reply.get_json()
    assert body["ok"] is False and body["data"] is None
    assert body["error"]["code"] == "INVALID_REQUEST"
    assert cloud.seen == []


# Input: none
# Output: None
# Processing: Check that a cloud rate limit is passed through in the envelope.
# Changed by: Nirmal
# Date: 2026-10-31
def test_cloud_error_is_mapped():
    cloud = FakeCloud(failure=CloudError("RATE_LIMITED", "slow down", 429))
    reply = create_app(cloud=cloud).test_client().post("/api/v1/recommendations", json=sample())
    assert reply.status_code == 429
    assert reply.get_json()["error"] == {"code": "RATE_LIMITED", "message": "slow down"}
