"""B05, the cloud Flask server (handbook §4.2).

    python -m backend.cloud_server.app        # 127.0.0.1:8500

The server is the public HTTPS edge. It rate-limits per authenticated user
(falling back to the peer address until §8.3 is frozen), applies the auth
guard, and forwards operation-specific bodies to the right in-process
service: B06 for recommendations, B09 for project ingestion, B16 for credits.

Routes:

    POST /v1/recommend              §8.1 in, §8.2 out, upstream = B06
    POST /v1/project                §5.5 submission, upstream = B09
    GET  /v1/project/<id>/diagram   §5.4
    POST /v1/contributions          §15.1 in, §15.2 out (B16)
    GET  /v1/credits/<user_id>      B16 balances
    POST /v1/auth/register          §8.3 registration
    POST /v1/auth/login             §8.3 login
    GET  /healthz                   liveness

Gap G9: the handbook has no cloud envelope, so errors use the §16.2 object
inside ``{"request_id", "error": {"code", "message"}}``.
"""

from __future__ import annotations

import argparse
import os
from typing import Any

from flask import Flask, g, jsonify, request

from backend.cloud_server.auth import Guard, is_public
from backend.cloud_server.identity import IdentityStore
from backend.cloud_server.rate_limit import RateLimiter
from backend.cloud_server.upstream import HttpRecommender, Recommender, UpstreamError
from backend.credits.engine import default_engine as default_credits_engine
from backend.credits.repo import ALLOWED_SOURCES, Repo as CreditsRepo
from backend.project_ingestion.service import IngestionError, IngestionService


# Input: request_id (Any), code (str), message (str)
# Output: dict[str, Any]
# Processing: The cloud error body used until §4.2 defines one (gap G9).
# Changed by: Nirmal
# Date: 2026-10-31
def error_body(request_id: Any, code: str, message: str) -> dict[str, Any]:
    return {"request_id": request_id if isinstance(request_id, str) else None,
            "error": {"code": code, "message": message}}


# Input: upstream, limiter, identity, ingestion, credits, require_auth (all optional)
# Output: Flask
# Processing: Build the cloud app with its collaborators injected for tests.
# Changed by: Nirmal
# Date: 2026-10-31
def create_app(upstream: Recommender | None = None,
               limiter: RateLimiter | None = None,
               identity: IdentityStore | None = None,
               ingestion: IngestionService | None = None,
               credits: CreditsRepo | None = None,
               require_auth: bool | None = None) -> Flask:
    app = Flask("plovane-cloud-server")
    upstream_of = upstream or HttpRecommender(
        os.environ.get("PLOVANE_MODEL_URL", "http://127.0.0.1:8600")
    )
    limiter_of = limiter or RateLimiter()
    identity_of = identity or IdentityStore()
    ingestion_of = ingestion or IngestionService()
    credits_of = credits or CreditsRepo(default_credits_engine())
    guard = Guard(identity_of, require=require_auth)

    # Input: raw (dict[str, Any] | None), user_id (str | None)
    # Output: str
    # Processing: Pick the rate-limit key: authenticated user first.
    # Changed by: Nirmal
    # Date: 2026-10-31
    def caller_key(raw: dict[str, Any] | None, user_id: str | None) -> str:
        if user_id:
            return user_id
        if isinstance(raw, dict):
            maybe = raw.get("user_id")
            if isinstance(maybe, str) and maybe:
                return maybe
        return request.remote_addr or "anonymous"

    # Input: none
    # Output: Response | None
    # Processing: Enforce the auth guard before any protected view runs.
    # Changed by: Nirmal
    # Date: 2026-10-31
    @app.before_request
    def _authenticate():
        user_id, authenticated = guard.check(request)
        g.user_id = user_id
        if not authenticated:
            raw = request.get_json(silent=True)
            request_id = raw.get("request_id") if isinstance(raw, dict) else None
            return jsonify(error_body(request_id, "UNAUTHENTICATED",
                                      "a valid session token is required")), 401
        return None

    # Input: none
    # Output: Response
    # Processing: Liveness; it does not call the recommender.
    # Changed by: Nirmal
    # Date: 2026-10-31
    @app.get("/healthz")
    def healthz():
        return jsonify({"ok": True})

    # Input: none
    # Output: Response
    # Processing: Rate-limit, then forward §8.1 and return §8.2 unchanged.
    # Changed by: Nirmal
    # Date: 2026-10-31
    @app.post("/v1/recommend")
    def recommend():
        raw = request.get_json(silent=True)
        if not isinstance(raw, dict):
            return jsonify(error_body(None, "INVALID_REQUEST",
                                      "send a JSON object with Content-Type: application/json")), 400
        if not limiter_of.allow(caller_key(raw, getattr(g, "user_id", None))):
            return jsonify(error_body(raw.get("request_id"), "RATE_LIMITED",
                                      "too many requests; slow down")), 429
        try:
            reply = upstream_of.recommend(raw)
        except UpstreamError as exc:
            status = exc.status if exc.status in (400, 401, 403, 404, 409, 429, 503, 504) else 502
            return jsonify(error_body(raw.get("request_id"), exc.code, str(exc))), status
        return jsonify(reply)

    # Input: none
    # Output: Response
    # Processing: Ingest a §5.5 submission; return the success body.
    # Changed by: Nirmal
    # Date: 2026-10-31
    @app.post("/v1/project")
    def ingest_project():
        raw = request.get_json(silent=True)
        if not limiter_of.allow(caller_key(raw if isinstance(raw, dict) else None,
                                          getattr(g, "user_id", None))):
            request_id = raw.get("request_id") if isinstance(raw, dict) else None
            return jsonify(error_body(request_id, "RATE_LIMITED",
                                      "too many requests; slow down")), 429
        try:
            reply = ingestion_of.ingest(raw)
        except IngestionError as exc:
            request_id = raw.get("request_id") if isinstance(raw, dict) else None
            return jsonify(error_body(request_id, exc.code, str(exc))), 400
        return jsonify(reply)

    # Input: project_id (str)
    # Output: Response
    # Processing: Return the stored §5.4 diagram.
    # Changed by: Nirmal
    # Date: 2026-10-31
    @app.get("/v1/project/<project_id>/diagram")
    def project_diagram(project_id: str):
        diagram = ingestion_of.diagram(project_id)
        if diagram is None:
            return jsonify(error_body(None, "NOT_FOUND",
                                      f"no project with id {project_id}")), 404
        return jsonify(diagram)

    # Input: none
    # Output: Response
    # Processing: Record a §15.1 contribution; return the §15.2 outcome.
    # Changed by: Nirmal
    # Date: 2026-10-31
    @app.post("/v1/contributions")
    def contributions():
        raw = request.get_json(silent=True)
        if not isinstance(raw, dict):
            return jsonify(error_body(None, "INVALID_REQUEST",
                                      "send a JSON object")), 400
        for field in ("contribution_id", "user_id", "source", "code_blocks"):
            if field not in raw:
                return jsonify(error_body(raw.get("request_id"), "INVALID_REQUEST",
                                          f"{field} is required")), 400
        if raw["source"] not in ALLOWED_SOURCES:
            return jsonify(error_body(raw.get("request_id"), "INVALID_REQUEST",
                                      f"source must be one of {ALLOWED_SOURCES}")), 400
        outcome = credits_of.record(raw)
        return jsonify(outcome)

    # Input: user_id (str)
    # Output: Response
    # Processing: Read a user's credit balances.
    # Changed by: Nirmal
    # Date: 2026-10-31
    @app.get("/v1/credits/<user_id>")
    def credits_for(user_id: str):
        return jsonify(credits_of.balances(user_id))

    # Input: none
    # Output: Response
    # Processing: §8.3 registration: email + password + machine_id in.
    # Changed by: Nirmal
    # Date: 2026-10-31
    @app.post("/v1/auth/register")
    def register():
        raw = request.get_json(silent=True)
        if not isinstance(raw, dict):
            return jsonify(error_body(None, "INVALID_REQUEST",
                                      "send a JSON object")), 400
        for field in ("email", "password"):
            if not isinstance(raw.get(field), str) or not raw[field]:
                return jsonify(error_body(None, "INVALID_REQUEST",
                                          f"{field} is required")), 400
        try:
            reply = identity_of.register(raw["email"], raw["password"], raw.get("machine_id"))
        except ValueError as exc:
            return jsonify(error_body(None, "CONFLICT", str(exc))), 409
        return jsonify(reply), 201

    # Input: none
    # Output: Response
    # Processing: §8.3 login: issue a session token.
    # Changed by: Nirmal
    # Date: 2026-10-31
    @app.post("/v1/auth/login")
    def login():
        raw = request.get_json(silent=True)
        if not isinstance(raw, dict):
            return jsonify(error_body(None, "INVALID_REQUEST",
                                      "send a JSON object")), 400
        for field in ("email", "password"):
            if not isinstance(raw.get(field), str) or not raw[field]:
                return jsonify(error_body(None, "INVALID_REQUEST",
                                          f"{field} is required")), 400
        try:
            reply = identity_of.login(raw["email"], raw["password"], raw.get("machine_id"))
        except ValueError as exc:
            return jsonify(error_body(None, "UNAUTHENTICATED", str(exc))), 401
        return jsonify(reply)

    return app


# Input: argv (list[str] | None)
# Output: None
# Processing: Run the development server.
# Changed by: Nirmal
# Date: 2026-10-31
def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Plovane cloud server (development server)")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8500)
    parser.add_argument("--model-url", default=os.environ.get("PLOVANE_MODEL_URL", "http://127.0.0.1:8600"),
                        help="base URL of the B06 recommender")
    args = parser.parse_args(argv)
    create_app(HttpRecommender(args.model_url)).run(host=args.host, port=args.port)


if __name__ == "__main__":
    main()
