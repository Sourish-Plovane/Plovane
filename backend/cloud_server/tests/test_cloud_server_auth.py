"""B05 auth + §8.3 route tests."""

from __future__ import annotations

import pytest

pytest.importorskip("flask")

from backend.cloud_server.app import create_app
from backend.cloud_server.identity import IdentityStore
from backend.cloud_server.upstream import UpstreamError


# Input: body (dict | None), fail (UpstreamError | None)
# Output: FakeUpstream
# Processing: Recommender double so routes work in tests.
# Changed by: Nirmal
# Date: 2026-10-31
class FakeUpstream:
    def __init__(self, body=None, fail=None):
        self.body = body or {"request_id": "req_1", "recommendations": []}
        self.fail = fail
        self.seen = []

    # Input: body (dict)
    # Output: dict
    # Processing: Record and return.
    # Changed by: Nirmal
    # Date: 2026-10-31
    def recommend(self, body):
        self.seen.append(body)
        if self.fail:
            raise self.fail
        return self.body


# Input: none
# Output: None
# Processing: register + login happy path returns a session token.
# Changed by: Nirmal
# Date: 2026-10-31
def test_register_then_login_round_trip():
    client = create_app(upstream=FakeUpstream(), identity=IdentityStore()).test_client()
    reply = client.post("/v1/auth/register", json={"email": "a@b.c", "password": "pw"})
    assert reply.status_code == 201
    assert reply.get_json()["user_id"].startswith("usr_")

    reply = client.post("/v1/auth/login", json={"email": "a@b.c", "password": "pw"})
    assert reply.status_code == 200
    assert reply.get_json()["session_token"]


# Input: none
# Output: None
# Processing: Duplicate email is rejected with CONFLICT.
# Changed by: Nirmal
# Date: 2026-10-31
def test_duplicate_register_is_conflict():
    client = create_app(upstream=FakeUpstream(), identity=IdentityStore()).test_client()
    client.post("/v1/auth/register", json={"email": "a@b.c", "password": "pw"})
    reply = client.post("/v1/auth/register", json={"email": "a@b.c", "password": "pw"})
    assert reply.status_code == 409


# Input: none
# Output: None
# Processing: Wrong password is UNAUTHENTICATED.
# Changed by: Nirmal
# Date: 2026-10-31
def test_wrong_password_is_unauthenticated():
    identity = IdentityStore()
    identity.register("a@b.c", "pw")
    client = create_app(upstream=FakeUpstream(), identity=identity).test_client()
    reply = client.post("/v1/auth/login", json={"email": "a@b.c", "password": "wrong"})
    assert reply.status_code == 401


# Input: none
# Output: None
# Processing: With require_auth, /v1/recommend without a token is 401.
# Changed by: Nirmal
# Date: 2026-10-31
def test_require_auth_blocks_unauthenticated_recommend():
    client = create_app(upstream=FakeUpstream(),
                        identity=IdentityStore(), require_auth=True).test_client()
    reply = client.post("/v1/recommend", json={"request_id": "req_x"})
    assert reply.status_code == 401
    assert reply.get_json()["error"]["code"] == "UNAUTHENTICATED"


# Input: none
# Output: None
# Processing: With a valid bearer token, /v1/recommend reaches the upstream.
# Changed by: Nirmal
# Date: 2026-10-31
def test_require_auth_allows_with_bearer_token():
    identity = IdentityStore()
    identity.register("a@b.c", "pw")
    session = identity.login("a@b.c", "pw")
    upstream = FakeUpstream({"request_id": "req_z", "recommendations": [{"block_id": "b"}]})
    client = create_app(upstream=upstream, identity=identity, require_auth=True).test_client()
    reply = client.post("/v1/recommend",
                        json={"request_id": "req_z"},
                        headers={"Authorization": "Bearer " + session["session_token"]})
    assert reply.status_code == 200
    assert reply.get_json()["recommendations"][0]["block_id"] == "b"
