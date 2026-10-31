"""B03's handbook contracts, kept free of Flask so they can be tested alone.

The local API uses the §4.1 envelope. The validators below check the shapes
and return only the fields each contract defines; the Flask app composes them
into cloud calls or MCP responses.

Errors use the §16.2 object. The cloud has no error format of its own (gap
G9), so it is mapped onto the same vocabulary here.
"""

from __future__ import annotations

import uuid
from typing import Any


# Input: none
# Output: str
# Processing: A fresh opaque request identifier for §8.1.
# Changed by: Nirmal
# Date: 2026-10-31
def new_request_id() -> str:
    return "req_" + uuid.uuid4().hex


# Input: data (Any)
# Output: dict[str, Any]
# Processing: A successful §4.1 envelope.
# Changed by: Nirmal
# Date: 2026-10-31
def ok(data: Any) -> dict[str, Any]:
    return {"ok": True, "data": data, "error": None}


# Input: code (str), message (str)
# Output: dict[str, Any]
# Processing: A failed §4.1 envelope.
# Changed by: Nirmal
# Date: 2026-10-31
def error(code: str, message: str) -> dict[str, Any]:
    return {"ok": False, "data": None, "error": {"code": code, "message": message}}


# Input: message (str), code (str)
# Output: ContractError
# Processing: A request that breaks a handbook contract, with its §16.2 code.
# Changed by: Nirmal
# Date: 2026-10-31
class ContractError(ValueError):
    """A request that breaks a contract. ``code`` is a §16.2 error code."""

    def __init__(self, message: str, code: str = "INVALID_REQUEST") -> None:
        super().__init__(message)
        self.code = code


# Input: raw (Any)
# Output: dict[str, Any]
# Processing: Validate and normalise a §5.1 recommendation request.
# Changed by: Nirmal
# Date: 2026-10-31
def validate_recommendation_request(raw: Any) -> dict[str, Any]:
    """Check §5.1 and return only the fields the contract defines."""
    if not isinstance(raw, dict):
        raise ContractError("the body must be a JSON object")

    if "project_id" not in raw:
        raise ContractError("project_id is required (a string or null)")
    project_id = raw.get("project_id")
    if project_id is not None and not isinstance(project_id, str):
        raise ContractError("project_id must be a string or null")

    description = raw.get("description")
    if not isinstance(description, str) or not description.strip():
        raise ContractError("description is required and must be a non-empty string")

    language = raw.get("language")
    if not isinstance(language, str) or not language.strip():
        raise ContractError("language is required and must be a non-empty string")

    tech_stack = raw.get("tech_stack")
    if not isinstance(tech_stack, list) or not all(isinstance(t, str) for t in tech_stack):
        raise ContractError("tech_stack is required and must be an array of strings")

    if "context_code" not in raw:
        raise ContractError("context_code is required (a string or null)")
    context_code = raw.get("context_code")
    if context_code is not None and not isinstance(context_code, str):
        raise ContractError("context_code must be a string or null")

    requested_count = raw.get("requested_count")
    if (not isinstance(requested_count, int) or isinstance(requested_count, bool)
            or requested_count <= 0):
        raise ContractError("requested_count is required and must be a positive integer")

    return {
        "project_id": project_id,
        "description": description,
        "language": language,
        "tech_stack": list(tech_stack),
        "context_code": context_code,
        "requested_count": requested_count,
    }


# Input: raw (Any)
# Output: dict[str, Any]
# Processing: Validate a §5.5 project submission.
# Changed by: Nirmal
# Date: 2026-10-31
def validate_project_submission(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise ContractError("the body must be a JSON object")
    project_id = raw.get("project_id")
    if not isinstance(project_id, str) or not project_id:
        raise ContractError("project_id is required and must be a non-empty string")
    files = raw.get("files")
    if not isinstance(files, list) or not files:
        raise ContractError("files is required and must be a non-empty array")
    cleaned: list[dict[str, Any]] = []
    for index, file in enumerate(files):
        if not isinstance(file, dict):
            raise ContractError(f"files[{index}] must be an object")
        path = file.get("path")
        content = file.get("content")
        if not isinstance(path, str) or not path:
            raise ContractError(f"files[{index}].path must be a non-empty string")
        if not isinstance(content, str):
            raise ContractError(f"files[{index}].content must be a string")
        cleaned.append({"path": path, "content": content})
    return {"project_id": project_id, "files": cleaned}


# Input: raw (Any)
# Output: str
# Processing: Validate a §5.3 diagram request and return its project_id.
# Changed by: Nirmal
# Date: 2026-10-31
def validate_diagram_request(raw: Any) -> str:
    if not isinstance(raw, dict):
        raise ContractError("the body must be a JSON object")
    project_id = raw.get("project_id")
    if not isinstance(project_id, str) or not project_id:
        raise ContractError("project_id is required and must be a non-empty string")
    return project_id


# Input: raw (Any)
# Output: dict[str, Any]
# Processing: Validate an §8.3 login or registration body.
# Changed by: Nirmal
# Date: 2026-10-31
def validate_auth_body(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise ContractError("the body must be a JSON object")
    email = raw.get("email")
    password = raw.get("password")
    machine_id = raw.get("machine_id")
    if not isinstance(email, str) or not email:
        raise ContractError("email is required and must be a non-empty string")
    if not isinstance(password, str) or not password:
        raise ContractError("password is required and must be a non-empty string")
    if machine_id is not None and not isinstance(machine_id, str):
        raise ContractError("machine_id must be a string or null")
    return {"email": email, "password": password, "machine_id": machine_id}


# Input: raw (Any)
# Output: tuple[str, dict[str, Any]]
# Processing: Validate a §7 MCP envelope; return (operation, input).
# Changed by: Nirmal
# Date: 2026-10-31
def validate_mcp_request(raw: Any) -> tuple[str, dict[str, Any]]:
    if not isinstance(raw, dict):
        raise ContractError("the body must be a JSON object")
    operation = raw.get("operation")
    if not isinstance(operation, str) or not operation:
        raise ContractError("operation is required and must be a non-empty string")
    body = raw.get("input")
    if not isinstance(body, dict):
        raise ContractError("input is required and must be a JSON object")
    return operation, body


# Input: local (dict[str, Any]), request_id (str), user_id (str)
# Output: dict[str, Any]
# Processing: Turn a §5.1 request into the §8.1 cloud request.
# Changed by: Nirmal
# Date: 2026-10-31
def cloud_request(local: dict[str, Any], request_id: str, user_id: str) -> dict[str, Any]:
    return {"request_id": request_id, "user_id": user_id, **local}


# Input: cloud (Any)
# Output: dict[str, Any]
# Processing: Trim a §8.2 cloud answer to the §5.2 data object.
# Changed by: Nirmal
# Date: 2026-10-31
def recommendation_data(cloud: Any) -> dict[str, Any]:
    if not isinstance(cloud, dict):
        raise ContractError("the cloud response must be a JSON object", "INTERNAL_ERROR")
    recommendations = cloud.get("recommendations")
    if not isinstance(recommendations, list):
        raise ContractError("the cloud response has no recommendations array", "INTERNAL_ERROR")
    return {"recommendations": recommendations}


# Input: cloud (Any)
# Output: dict[str, Any]
# Processing: Pass through the §5.5 success response (submission_id, status).
# Changed by: Nirmal
# Date: 2026-10-31
def project_data(cloud: Any) -> dict[str, Any]:
    if not isinstance(cloud, dict):
        raise ContractError("the cloud response must be a JSON object", "INTERNAL_ERROR")
    submission_id = cloud.get("submission_id")
    status = cloud.get("status")
    if not isinstance(submission_id, str) or status != "accepted":
        raise ContractError("the cloud response is missing submission_id/status",
                            "INTERNAL_ERROR")
    return {"submission_id": submission_id,
            "project_id": cloud.get("project_id"),
            "status": status}


# Input: cloud (Any)
# Output: dict[str, Any]
# Processing: Pass through the §5.4 diagram body unchanged.
# Changed by: Nirmal
# Date: 2026-10-31
def diagram_data(cloud: Any) -> dict[str, Any]:
    if not isinstance(cloud, dict):
        raise ContractError("the cloud response must be a JSON object", "INTERNAL_ERROR")
    if "diagram" not in cloud or "project_id" not in cloud:
        raise ContractError("the cloud response is not a §5.4 diagram body",
                            "INTERNAL_ERROR")
    return {"project_id": cloud["project_id"], "diagram": cloud["diagram"]}


# Input: cloud (Any)
# Output: dict[str, Any]
# Processing: Pass through the §8.3 login response.
# Changed by: Nirmal
# Date: 2026-10-31
def auth_login_data(cloud: Any) -> dict[str, Any]:
    if not isinstance(cloud, dict):
        raise ContractError("the cloud response must be a JSON object", "INTERNAL_ERROR")
    for field in ("user_id", "session_token", "expires_at"):
        if not isinstance(cloud.get(field), str):
            raise ContractError(f"the cloud login response is missing {field}",
                                "INTERNAL_ERROR")
    return {k: cloud[k] for k in ("user_id", "session_token", "expires_at")}


# Input: cloud (Any)
# Output: dict[str, Any]
# Processing: Pass through the §8.3 registration response.
# Changed by: Nirmal
# Date: 2026-10-31
def auth_register_data(cloud: Any) -> dict[str, Any]:
    if not isinstance(cloud, dict):
        raise ContractError("the cloud response must be a JSON object", "INTERNAL_ERROR")
    user_id = cloud.get("user_id")
    if not isinstance(user_id, str):
        raise ContractError("the cloud registration response is missing user_id",
                            "INTERNAL_ERROR")
    return {"user_id": user_id}


# Input: status (int)
# Output: str
# Processing: The §16.2 code that best matches an HTTP status.
# Changed by: Nirmal
# Date: 2026-10-31
def code_for_status(status: int) -> str:
    return {
        400: "INVALID_REQUEST",
        401: "UNAUTHENTICATED",
        403: "FORBIDDEN",
        404: "NOT_FOUND",
        409: "CONFLICT",
        413: "INVALID_REQUEST",
        422: "INVALID_REQUEST",
        429: "RATE_LIMITED",
    }.get(status, "INTERNAL_ERROR")


# Input: code (str)
# Output: int
# Processing: The HTTP status the local API should send for a §16.2 code.
# Changed by: Nirmal
# Date: 2026-10-31
def status_for_code(code: str) -> int:
    return {
        "INVALID_REQUEST": 400,
        "UNAUTHENTICATED": 401,
        "FORBIDDEN": 403,
        "NOT_FOUND": 404,
        "CONFLICT": 409,
        "RATE_LIMITED": 429,
        "INTERNAL_ERROR": 500,
        "UNAVAILABLE": 503,
    }.get(code, 500)
