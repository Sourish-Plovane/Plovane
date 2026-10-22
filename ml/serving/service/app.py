"""The HTTP backend for Plovane's models (Flask, as the handbook's cloud server).

    python -m ml.serving.service.app                     # 127.0.0.1:8600, development
    gunicorn -w 2 -b 0.0.0.0:8600 'ml.serving.service.app:create_app()'   # production

Each worker process loads its own copy of the models (about 2 GB): the
cross-encoder flips train/eval mode while scoring, so one model must never
serve two threads at once. Run gunicorn with sync workers, not threads.

Routes:

    POST /v1/recommend      §8.1 in, §8.2 out (see contract.py)
    POST /v1/feedback       {query_id, block_id, outcome: accepted|rejected, user_id?}
    POST /v1/check-file     {filename, code} -> each function: in_library / similar_exists / new
    POST /v1/find-helpers   {filename, code} -> standalone helpers in the file
    POST /v1/rerank         {query, candidates: [{name, code, ...}], language?}
    GET  /v1/blocks/<id>    one library entry, code included
    GET  /v1/models         the model registry plus what this process has loaded
    GET  /healthz           the process is up
    GET  /readyz            the models are loaded and rank a known pair correctly
"""

from __future__ import annotations

import argparse
from collections.abc import Callable
from typing import Any

from flask import Flask, jsonify, request

from ml.serving import registry
from ml.serving.engine import Engine, get_engine
from ml.serving.service.contract import (
    ContractError,
    error_body,
    request_from_handbook,
    response_to_handbook,
)
from ml.serving.service.store import FeedbackStore

MAX_BODY_BYTES = 2 * 1024 * 1024
"""A source file or a request bigger than this is refused (413)."""


# Input: engine_factory (Callable[[], Engine] | None), store (FeedbackStore | None)
# Output: Flask
# Processing: Build the Flask app with its routes.
# Changed by: Claude Code
# Date: 2026-10-22
def create_app(
    engine_factory: Callable[[], Engine] | None = None, store: FeedbackStore | None = None
) -> Flask:
    """``engine_factory`` defaults to the shared lazily-loaded engine; tests
    pass one built on the hashing placeholder so they need no torch."""
    app = Flask("plovane-models")
    app.config["MAX_CONTENT_LENGTH"] = MAX_BODY_BYTES
    engine_of = engine_factory or get_engine
    feedback = store or FeedbackStore()

    # Input: none
    # Output: dict[str, Any]
    # Processing: The JSON body, or a ContractError when there is none.
    # Changed by: Claude Code
    # Date: 2026-10-22
    def body() -> dict[str, Any]:
        raw = request.get_json(silent=True)
        if not isinstance(raw, dict):
            raise ContractError("send a JSON object with Content-Type: application/json")
        return raw

    # Input: raw (dict[str, Any])
    # Output: tuple[str, str]
    # Processing: The filename and code from a file-carrying body.
    # Changed by: Claude Code
    # Date: 2026-10-22
    def file_fields(raw: dict[str, Any]) -> tuple[str, str]:
        filename, code = raw.get("filename"), raw.get("code")
        if not isinstance(filename, str) or not isinstance(code, str):
            raise ContractError("filename and code are required strings")
        return code, filename

    # Input: exc (ContractError)
    # Output: tuple
    # Processing: 400 with the error object.
    # Changed by: Claude Code
    # Date: 2026-10-22
    @app.errorhandler(ContractError)
    def bad_request(exc: ContractError):
        raw = request.get_json(silent=True)
        request_id = raw.get("request_id") if isinstance(raw, dict) else None
        return jsonify(error_body(request_id, exc.code, str(exc))), 400

    # Input: exc (Exception)
    # Output: tuple
    # Processing: 413 for an oversized body.
    # Changed by: Claude Code
    # Date: 2026-10-22
    @app.errorhandler(413)
    def too_large(exc: Exception):
        return jsonify(error_body(None, "PAYLOAD_TOO_LARGE", f"limit is {MAX_BODY_BYTES} bytes")), 413

    # Input: exc (RuntimeError)
    # Output: tuple
    # Processing: 503 when the models cannot be loaded.
    # Changed by: Claude Code
    # Date: 2026-10-22
    @app.errorhandler(RuntimeError)
    def unavailable(exc: RuntimeError):
        return jsonify(error_body(None, "MODEL_UNAVAILABLE", str(exc))), 503

    # Input: none
    # Output: Response
    # Processing: §8.1 -> pipeline -> §8.2, logging what was served.
    # Changed by: Claude Code
    # Date: 2026-10-22
    @app.post("/v1/recommend")
    def recommend():
        raw = body()
        req = request_from_handbook(raw)
        response, log = engine_of().recommend(req)
        feedback.record_recommendation(
            log, {"request_id": raw.get("request_id"), "project_id": raw.get("project_id")}
        )
        return jsonify(response_to_handbook(raw.get("request_id"), response))

    # Input: none
    # Output: Response
    # Processing: Record a user's accept or reject of a served block.
    # Changed by: Claude Code
    # Date: 2026-10-22
    @app.post("/v1/feedback")
    def record_feedback():
        raw = body()
        try:
            row = feedback.record_feedback(
                str(raw.get("query_id")), str(raw.get("block_id")),
                str(raw.get("outcome")), raw.get("user_id"),
            )
        except ValueError as exc:
            raise ContractError(str(exc)) from None
        except KeyError as exc:
            return jsonify(error_body(None, "NOT_FOUND", exc.args[0])), 404
        return jsonify({"recorded": row})

    # Input: none
    # Output: Response
    # Processing: Which functions in a file the library already has.
    # Changed by: Claude Code
    # Date: 2026-10-22
    @app.post("/v1/check-file")
    def check_file():
        code, filename = file_fields(body())
        return jsonify(engine_of().check_file(code, filename))

    # Input: none
    # Output: Response
    # Processing: The standalone helpers in a file; needs no model.
    # Changed by: Claude Code
    # Date: 2026-10-22
    @app.post("/v1/find-helpers")
    def find_helpers_route():
        from ml.ingest.find_helpers import find_helpers

        code, filename = file_fields(body())
        return jsonify(find_helpers(code, filename))

    # Input: none
    # Output: Response
    # Processing: Cross-encoder scores for the caller's own snippets.
    # Changed by: Claude Code
    # Date: 2026-10-22
    @app.post("/v1/rerank")
    def rerank():
        raw = body()
        query, candidates = raw.get("query"), raw.get("candidates")
        if not isinstance(query, str) or not isinstance(candidates, list) or not candidates:
            raise ContractError("query (string) and candidates (non-empty list) are required")
        if not all(isinstance(c, dict) for c in candidates):
            raise ContractError("each candidate must be an object with name and code")
        return jsonify(engine_of().rerank(query, candidates, language=raw.get("language")))

    # Input: block_id (str)
    # Output: Response
    # Processing: One library entry, or 404 with close ids.
    # Changed by: Claude Code
    # Date: 2026-10-22
    @app.get("/v1/blocks/<path:block_id>")
    def get_block(block_id: str):
        found = engine_of().get(block_id)
        return jsonify(found), 404 if "error" in found else 200

    # Input: none
    # Output: Response
    # Processing: The registry, plus what this process has loaded.
    # Changed by: Claude Code
    # Date: 2026-10-22
    @app.get("/v1/models")
    def models():
        return jsonify({"models": registry.as_json(), "loaded": engine_of().status()})

    # Input: none
    # Output: Response
    # Processing: Liveness: answers without loading anything.
    # Changed by: Claude Code
    # Date: 2026-10-22
    @app.get("/healthz")
    def healthz():
        return jsonify({"ok": True})

    # Input: none
    # Output: Response
    # Processing: Readiness: models loaded and ranking a known pair right.
    # Changed by: Claude Code
    # Date: 2026-10-22
    @app.get("/readyz")
    def readyz():
        engine = engine_of()
        ready, detail = engine.self_check()
        return jsonify({"ready": ready, "detail": detail}), 200 if ready else 503

    return app


# Input: argv (list[str] | None)
# Output: None
# Processing: Run the development server.
# Changed by: Claude Code
# Date: 2026-10-22
def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Plovane model backend (development server)")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8600)
    args = parser.parse_args(argv)
    # threaded=False: one request at a time per process (see module docstring).
    create_app().run(host=args.host, port=args.port, threaded=False)


if __name__ == "__main__":
    main()
