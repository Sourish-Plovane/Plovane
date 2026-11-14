"""B03's handbook mappings, tested without Flask or the network."""

from __future__ import annotations

import pytest

from backend.local_backend.contracts import (
    ContractError,
    cloud_request,
    code_for_status,
    error,
    new_request_id,
    ok,
    recommendation_data,
    status_for_code,
    validate_recommendation_request,
)


# Input: none
# Output: dict
# Processing: A valid §5.1 body.
# Changed by: Nirmal
# Date: 2026-10-31
def sample() -> dict:
    return {
        "project_id": "proj_123",
        "description": "Add CSV parsing",
        "language": "python",
        "tech_stack": ["python", "pandas"],
        "context_code": None,
        "requested_count": 5,
    }


# Input: none
# Output: None
# Processing: Check that a valid body is normalised and kept.
# Changed by: Nirmal
# Date: 2026-10-31
def test_valid_request_is_normalised():
    assert validate_recommendation_request(sample()) == sample()


# Input: none
# Output: None
# Processing: Check that null project_id and context_code are allowed.
# Changed by: Nirmal
# Date: 2026-10-31
def test_null_context_is_allowed():
    body = sample() | {"project_id": None}
    assert validate_recommendation_request(body)["project_id"] is None


# Input: none
# Output: None
# Processing: Check that §5.1 violations raise INVALID_REQUEST.
# Changed by: Nirmal
# Date: 2026-10-31
@pytest.mark.parametrize("change", [
    {"description": ""},
    {"language": ""},
    {"tech_stack": "python"},
    {"requested_count": 0},
    {"requested_count": True},
    {"project_id": 3},
    {"context_code": 3},
])
def test_invalid_requests_raise(change):
    with pytest.raises(ContractError) as found:
        validate_recommendation_request(sample() | change)
    assert found.value.code == "INVALID_REQUEST"


# Input: none
# Output: None
# Processing: Check that missing keys are errors, not defaults.
# Changed by: Nirmal
# Date: 2026-10-31
@pytest.mark.parametrize("key", ["project_id", "context_code"])
def test_missing_nullable_keys_still_required(key):
    body = sample()
    del body[key]
    with pytest.raises(ContractError):
        validate_recommendation_request(body)


# Input: none
# Output: None
# Processing: Check that §8.1 adds request_id and user_id.
# Changed by: Nirmal
# Date: 2026-10-31
def test_cloud_request_adds_identity():
    body = cloud_request(sample(), "req_1", "usr_1")
    assert body["request_id"] == "req_1"
    assert body["user_id"] == "usr_1"
    assert body["description"] == "Add CSV parsing"


# Input: none
# Output: None
# Processing: Check that §8.2 is trimmed to §5.2 data.
# Changed by: Nirmal
# Date: 2026-10-31
def test_recommendation_data_drops_the_request_id():
    data = recommendation_data({"request_id": "req_1", "recommendations": [{"block_id": "b"}]})
    assert data == {"recommendations": [{"block_id": "b"}]}


# Input: none
# Output: None
# Processing: Check that a malformed cloud answer is an INTERNAL_ERROR.
# Changed by: Nirmal
# Date: 2026-10-31
def test_malformed_cloud_answer_is_an_error():
    with pytest.raises(ContractError) as found:
        recommendation_data({"request_id": "req_1"})
    assert found.value.code == "INTERNAL_ERROR"


# Input: none
# Output: None
# Processing: Check the envelope helpers and the status mapping.
# Changed by: Nirmal
# Date: 2026-10-31
def test_envelopes_and_statuses():
    assert ok({"a": 1}) == {"ok": True, "data": {"a": 1}, "error": None}
    assert error("NOT_FOUND", "gone") == {
        "ok": False, "data": None, "error": {"code": "NOT_FOUND", "message": "gone"}}
    assert code_for_status(429) == "RATE_LIMITED"
    assert code_for_status(418) == "INTERNAL_ERROR"
    assert status_for_code("RATE_LIMITED") == 429
    assert status_for_code("NOT_A_CODE") == 500
    assert new_request_id().startswith("req_")
