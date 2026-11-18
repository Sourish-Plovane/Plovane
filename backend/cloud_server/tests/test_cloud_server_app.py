"""B05's HTTP surface, exercised with a fake recommender."""

from __future__ import annotations

import pytest

pytest.importorskip("flask")

from backend.cloud_server.app import create_app
from backend.cloud_server.rate_limit import RateLimiter
from backend.cloud_server.upstream import UpstreamError


# Input: reply (dict), failure (UpstreamError | None)
# Output: FakeUpstream
# Processing: A B06 double that records what it was sent.
# Changed by: Nirmal
# Date: 2026-10-31
class FakeUpstream:
    def __init__(self, reply=None, failure=None):
        self.reply = reply or {"request_id": "req_1", "recommendations": []}
        self.failure = failure
        self.seen = []

    # Input: body (dict)
    # Output: dict
    # Processing: Return the canned answer, or raise the canned failure.
    # Changed by: Nirmal
    # Date: 2026-10-31
    def recommend(self, body):
        self.seen.append(body)
        if self.failure:
            raise self.failure
        return self.reply


# Input: user_id (str)
# Output: dict
# Processing: A minimal §8.1 body.
# Changed by: Nirmal
# Date: 2026-10-31
def body(user_id="usr_1"):
    return {"request_id": "req_1", "user_id": user_id, "description": "x", "language": "python"}


# Input: none
# Output: None
# Processing: Check that §8.1 is forwarded unchanged and §8.2 returned.
# Changed by: Nirmal
# Date: 2026-10-31
def test_recommend_is_forwarded():
    upstream = FakeUpstream({"request_id": "req_1", "recommendations": [{"block_id": "b"}]})
    client = create_app(upstream=upstream).test_client()
    reply = client.post("/v1/recommend", json=body())
    assert reply.status_code == 200
    assert reply.get_json()["recommendations"] == [{"block_id": "b"}]
    assert upstream.seen[0] == body()


# Input: none
# Output: None
# Processing: Check that a body that is not an object is a 400.
# Changed by: Nirmal
# Date: 2026-10-31
def test_non_object_body_is_refused():
    reply = create_app(upstream=FakeUpstream()).test_client().post("/v1/recommend", data="nope",
                                                                   content_type="application/json")
    assert reply.status_code == 400
    assert reply.get_json()["error"]["code"] == "INVALID_REQUEST"


# Input: none
# Output: None
# Processing: Check that the bucket rejects the second immediate call.
# Changed by: Nirmal
# Date: 2026-10-31
def test_rate_limit_is_per_user():
    client = create_app(upstream=FakeUpstream(),
                        limiter=RateLimiter(capacity=1, refill_per_second=0.001)).test_client()
    assert client.post("/v1/recommend", json=body("usr_a")).status_code == 200
    limited = client.post("/v1/recommend", json=body("usr_a"))
    assert limited.status_code == 429
    assert limited.get_json()["error"]["code"] == "RATE_LIMITED"
    assert client.post("/v1/recommend", json=body("usr_b")).status_code == 200


# Input: none
# Output: None
# Processing: Check that a recommender error keeps its status and code.
# Changed by: Nirmal
# Date: 2026-10-31
def test_upstream_error_is_mapped():
    client = create_app(upstream=FakeUpstream(failure=UpstreamError("INVALID_REQUEST", "bad", 400))).test_client()
    reply = client.post("/v1/recommend", json=body())
    assert reply.status_code == 400
    assert reply.get_json()["error"] == {"code": "INVALID_REQUEST", "message": "bad"}
