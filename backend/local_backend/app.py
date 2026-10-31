"""B03, the local Python backend the desktop app and CLI talk to.

    python -m backend.local_backend.app     # 127.0.0.1:8701

Handbook §4.1: HTTP on 127.0.0.1, JSON, prefix ``/api/v1/``, every response
is the ``{"ok", "data", "error"}`` envelope.

    GET  /api/v1/health                            liveness
    POST /api/v1/recommendations                   §5.1 in, §5.2 out
    POST /api/v1/projects                          §5.5 submission
    GET  /api/v1/projects/<project_id>/diagram     §5.4 read
    POST /api/v1/auth/register                     §8.3 registration (OD-4)
    POST /api/v1/auth/login                        §8.3 login (OD-4); stores token
    POST /api/v1/mcp                               §7 operation + input router

Caller identity: ``X-Plovane-User`` -> ``PLOVANE_USER`` env -> ``"local"``.
The session token from a successful login is kept on the cloud client so
the next request to §8 goes out authenticated.
"""

from __future__ import annotations

import argparse
import os
from typing import Any, Protocol

from flask import Flask, jsonify, request

from backend.local_backend import mcp
from backend.local_backend.cloud import CloudError, HttpCloudClient
from backend.local_backend.contracts import (
    ContractError,
    auth_login_data,
    auth_register_data,
    cloud_request,
    diagram_data,
    error,
    new_request_id,
    ok,
    project_data,
    recommendation_data,
    status_for_code,
    validate_auth_body,
    validate_project_submission,
    validate_recommendation_request,
)


# Input: body (dict[str, Any])
# Output: dict[str, Any]
# Processing: The cloud calls B03 makes; injected in tests.
# Changed by: Nirmal
# Date: 2026-10-31
class CloudClient(Protocol):
    session_token: str | None

    def set_session(self, token: str | None) -> None: ...
    def post_recommendation(self, body: dict[str, Any]) -> dict[str, Any]: ...
    def post_project(self, body: dict[str, Any]) -> dict[str, Any]: ...
    def get_diagram(self, project_id: str) -> dict[str, Any]: ...
    def login(self, body: dict[str, Any]) -> dict[str, Any]: ...
    def register(self, body: dict[str, Any]) -> dict[str, Any]: ...


# Input: cloud (CloudClient | None), user_id (str | None)
# Output: Flask
# Processing: Build the local API, defaulting to the real cloud client.
# Changed by: Nirmal
# Date: 2026-10-31
def create_app(cloud: CloudClient | None = None, user_id: str | None = None) -> Flask:
    app = Flask("plovane-local-backend")
    cloud_of: CloudClient = cloud or HttpCloudClient(
        os.environ.get("PLOVANE_CLOUD_URL", "http://127.0.0.1:8500")
    )

    # Input: none
    # Output: str
    # Processing: Who the local backend acts for: header, config, then "local".
    # Changed by: Nirmal
    # Date: 2026-10-31
    def caller_id() -> str:
        return (request.headers.get("X-Plovane-User") or user_id
                or os.environ.get("PLOVANE_USER") or "local")

    # Input: exc (CloudError), request_id (str | None)
    # Output: tuple[dict, int]
    # Processing: Map a cloud failure onto a §4.1 envelope and HTTP status.
    # Changed by: Nirmal
    # Date: 2026-10-31
    def cloud_failure(exc: CloudError) -> tuple[dict[str, Any], int]:
        status = exc.status if exc.status in (400, 401, 403, 404, 409, 429, 503, 504) else 502
        return error(exc.code, str(exc)), status

    # Input: none
    # Output: Response
    # Processing: Liveness; it does not call the cloud.
    # Changed by: Nirmal
    # Date: 2026-10-31
    @app.get("/api/v1/health")
    def health():
        return jsonify(ok({"status": "ok"}))

    # Input: none
    # Output: Response
    # Processing: §5.1 -> §8.1 -> cloud -> §5.2, all inside the §4.1 envelope.
    # Changed by: Nirmal
    # Date: 2026-10-31
    @app.post("/api/v1/recommendations")
    def recommendations():
        try:
            local = validate_recommendation_request(request.get_json(silent=True))
            body = cloud_request(local, new_request_id(), caller_id())
            data = recommendation_data(cloud_of.post_recommendation(body))
        except ContractError as exc:
            return jsonify(error(exc.code, str(exc))), status_for_code(exc.code)
        except CloudError as exc:
            body, status = cloud_failure(exc)
            return jsonify(body), status
        return jsonify(ok(data))

    # Input: none
    # Output: Response
    # Processing: §5.5 submission; forwards to the cloud and echoes the ack.
    # Changed by: Nirmal
    # Date: 2026-10-31
    @app.post("/api/v1/projects")
    def submit_project():
        try:
            payload = validate_project_submission(request.get_json(silent=True))
            data = project_data(cloud_of.post_project(payload))
        except ContractError as exc:
            return jsonify(error(exc.code, str(exc))), status_for_code(exc.code)
        except CloudError as exc:
            body, status = cloud_failure(exc)
            return jsonify(body), status
        return jsonify(ok(data))

    # Input: project_id (str)
    # Output: Response
    # Processing: §5.3 in, §5.4 out; reads from the cloud's stored diagram.
    # Changed by: Nirmal
    # Date: 2026-10-31
    @app.get("/api/v1/projects/<project_id>/diagram")
    def diagram(project_id: str):
        try:
            data = diagram_data(cloud_of.get_diagram(project_id))
        except ContractError as exc:
            return jsonify(error(exc.code, str(exc))), status_for_code(exc.code)
        except CloudError as exc:
            body, status = cloud_failure(exc)
            return jsonify(body), status
        return jsonify(ok(data))

    # Input: none
    # Output: Response
    # Processing: §8.3 registration (OD-4): just forwards to the cloud.
    # Changed by: Nirmal
    # Date: 2026-10-31
    @app.post("/api/v1/auth/register")
    def register():
        try:
            payload = validate_auth_body(request.get_json(silent=True))
            data = auth_register_data(cloud_of.register(payload))
        except ContractError as exc:
            return jsonify(error(exc.code, str(exc))), status_for_code(exc.code)
        except CloudError as exc:
            body, status = cloud_failure(exc)
            return jsonify(body), status
        return jsonify(ok(data))

    # Input: none
    # Output: Response
    # Processing: §8.3 login (OD-4); remembers the session token.
    # Changed by: Nirmal
    # Date: 2026-10-31
    @app.post("/api/v1/auth/login")
    def login():
        try:
            payload = validate_auth_body(request.get_json(silent=True))
            data = auth_login_data(cloud_of.login(payload))
            cloud_of.set_session(data["session_token"])
        except ContractError as exc:
            return jsonify(error(exc.code, str(exc))), status_for_code(exc.code)
        except CloudError as exc:
            body, status = cloud_failure(exc)
            return jsonify(body), status
        return jsonify(ok(data))

    # Input: none
    # Output: Response
    # Processing: §7 MCP router; dispatches operation+input to the right path.
    # Changed by: Nirmal
    # Date: 2026-10-31
    @app.post("/api/v1/mcp")
    def mcp_router():
        envelope, status = mcp.handle(cloud_of, caller_id, request.get_json(silent=True))
        return jsonify(envelope), status

    return app


# Input: argv (list[str] | None)
# Output: None
# Processing: Run the development server on the loopback interface only.
# Changed by: Nirmal
# Date: 2026-10-31
def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Plovane local backend (development server)")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8701)
    parser.add_argument("--cloud", default=os.environ.get("PLOVANE_CLOUD_URL", "http://127.0.0.1:8500"),
                        help="base URL of the cloud server (B05)")
    args = parser.parse_args(argv)
    create_app(HttpCloudClient(args.cloud)).run(host=args.host, port=args.port)


if __name__ == "__main__":
    main()
