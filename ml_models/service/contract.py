"""The cloud recommendation contract (handbook §8.1 / §8.2) mapped onto B06.

This is the proposal for gap G2, the rate limiter (B05) to B06 boundary that
the handbook leaves undefined. B05 forwards the §8.1 body unchanged; this
module turns it into the :class:`ml.schemas.Request` the pipeline takes, and
turns the pipeline's answer back into the §8.2 body.

Field mapping, §8.1 to B06:

=================  ==========================================================
``description``    ``query`` (required)
``language``       ``project_context.languages`` (hard filter)
``tech_stack``     ``project_context.dependencies``, minus language names
``context_code``   ``project_context.file_summary``: the functions it defines
``requested_count`` ``k`` (1 to 20, default 3)
``user_id``        ``requester_id``
``request_id``     echoed back; ``project_id`` is logged only
=================  ==========================================================

§8.2 is extended with ``query_id`` (needed to send feedback),
``cutoff_enforced`` and ``reason``. Errors use the §4.1 error object, since
§4.2 defines none for the cloud (gap G9).
"""

from __future__ import annotations

from typing import Any

from ml.ingest.extract import extract
from ml.schemas import ProjectContext, Request, Response

MAX_COUNT = 20
LANGUAGE_EXTENSION = {"python": "x.py", "javascript": "x.js", "typescript": "x.ts", "cpp": "x.cpp"}
LANGUAGE_NAMES = {"python", "javascript", "typescript", "cpp", "c++", "js", "ts"}


class ContractError(ValueError):
    """A request that breaks the contract. ``code`` is the §16.2-style code."""

    # Input: message (str), code (str)
    # Output: None
    # Processing: Keep the machine-readable code next to the message.
    # Changed by: Claude Code
    # Date: 2026-10-22
    def __init__(self, message: str, code: str = "INVALID_REQUEST") -> None:
        super().__init__(message)
        self.code = code


# Input: code (str | None), language (str | None)
# Output: str | None
# Processing: One line naming the functions the caller's code defines.
# Changed by: Claude Code
# Date: 2026-10-22
def summarise_context(code: str | None, language: str | None) -> str | None:
    """What the code the user is editing defines, for the query's context.

    Names rather than the raw code: the query side of the cross-encoder has a
    token budget, and a pasted file would push the request text out of it.
    """
    if not code or not code.strip():
        return None
    filename = LANGUAGE_EXTENSION.get((language or "").lower())
    names = [u.name for u in extract(code, filename)] if filename else []
    if names:
        return "defines " + ", ".join(names[:12])
    first = code.strip().splitlines()[0]
    return first[:120]


# Input: raw (Any)
# Output: Request
# Processing: Validate a §8.1 body and build the B06 request from it.
# Changed by: Claude Code
# Date: 2026-10-22
def request_from_handbook(raw: Any) -> Request:
    if not isinstance(raw, dict):
        raise ContractError("the body must be a JSON object")
    description = raw.get("description")
    if not isinstance(description, str) or not description.strip():
        raise ContractError("description is required and must be a non-empty string")
    language = raw.get("language")
    if language is not None and not isinstance(language, str):
        raise ContractError("language must be a string")
    tech_stack = raw.get("tech_stack") or []
    if not isinstance(tech_stack, list) or not all(isinstance(t, str) for t in tech_stack):
        raise ContractError("tech_stack must be a list of strings")
    count = raw.get("requested_count", 3)
    if not isinstance(count, int) or isinstance(count, bool) or not 1 <= count <= MAX_COUNT:
        raise ContractError(f"requested_count must be an integer from 1 to {MAX_COUNT}")
    context_code = raw.get("context_code")
    if context_code is not None and not isinstance(context_code, str):
        raise ContractError("context_code must be a string")

    return Request(
        query=description.strip(),
        project_context=ProjectContext(
            languages=(language.lower(),) if language else (),
            dependencies=tuple(t for t in tech_stack if t.lower() not in LANGUAGE_NAMES),
            file_summary=summarise_context(context_code, language),
        ),
        k=count,
        requester_id=raw.get("user_id"),
        drop_in_only=bool(raw.get("drop_in_only", False)),
    )


# Input: request_id (str | None), response (Response)
# Output: dict[str, Any]
# Processing: Build the §8.2 body from the pipeline's response.
# Changed by: Claude Code
# Date: 2026-10-22
def response_to_handbook(request_id: str | None, response: Response) -> dict[str, Any]:
    data = response.to_handbook_response()["data"]
    return {"request_id": request_id, **data}


# Input: request_id (str | None), code (str), message (str)
# Output: dict[str, Any]
# Processing: The error body, using the §4.1 error object.
# Changed by: Claude Code
# Date: 2026-10-22
def error_body(request_id: str | None, code: str, message: str) -> dict[str, Any]:
    return {"request_id": request_id, "error": {"code": code, "message": message}}
