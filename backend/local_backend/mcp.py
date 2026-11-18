"""B03 §7 router: ``{operation, input}`` -> §4.1 envelope.

Supported operations today: ``recommend_code`` (§5.1 / §5.2), ``submit_project``
(§5.5) and ``get_diagram`` (§5.3 / §5.4). The transport is left to §7's
"out of scope" clause; this module only serialises the payloads.
"""

from __future__ import annotations

from typing import Any, Callable

from backend.local_backend.contracts import (
    ContractError,
    cloud_request,
    diagram_data,
    error,
    new_request_id,
    ok,
    project_data,
    recommendation_data,
    status_for_code,
    validate_diagram_request,
    validate_mcp_request,
    validate_project_submission,
    validate_recommendation_request,
)


# Input: cloud (object), caller_id (Callable[[], str]), raw (Any)
# Output: tuple[dict[str, Any], int]
# Processing: Run the §7 request and return (envelope, http_status).
# Changed by: Nirmal
# Date: 2026-10-31
def handle(cloud: Any, caller_id: Callable[[], str], raw: Any) -> tuple[dict[str, Any], int]:
    try:
        operation, body = validate_mcp_request(raw)
    except ContractError as exc:
        return error(exc.code, str(exc)), status_for_code(exc.code)

    try:
        if operation == "recommend_code":
            local = validate_recommendation_request(body)
            reply = cloud.post_recommendation(
                cloud_request(local, new_request_id(), caller_id()))
            return ok(recommendation_data(reply)), 200
        if operation == "submit_project":
            payload = validate_project_submission(body)
            reply = cloud.post_project(payload)
            return ok(project_data(reply)), 200
        if operation == "get_diagram":
            project_id = validate_diagram_request(body)
            reply = cloud.get_diagram(project_id)
            return ok(diagram_data(reply)), 200
    except ContractError as exc:
        return error(exc.code, str(exc)), status_for_code(exc.code)
    except Exception as exc:
        code = getattr(exc, "code", "INTERNAL_ERROR")
        status = getattr(exc, "status", None)
        return error(code, str(exc)), status if isinstance(status, int) else status_for_code(code)

    return error("INVALID_REQUEST", f"unknown operation: {operation}"), 400
