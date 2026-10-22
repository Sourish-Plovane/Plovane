"""The whiteboard's local HTTP API, for the desktop app and the CLI.

    python -m whiteboard.server --project /path/to/project     # 127.0.0.1:8700

Handbook §4.1: HTTP on 127.0.0.1, JSON, prefix ``/api/v1/``, and every
response is the ``{"ok", "data", "error"}`` envelope.

    GET  /api/v1/whiteboard/board       the board to draw, with resolved styles
    GET  /api/v1/whiteboard/commands    every slash command, for autocomplete and help
    POST /api/v1/whiteboard/command     {"command": "/block add service auth", "confirm"?, "dry_run"?}
    POST /api/v1/whiteboard/op          {"op": "block.add", "args": {...}, "confirm"?, "dry_run"?}
    POST /api/v1/whiteboard/undo        undo the last change
    POST /api/v1/whiteboard/redo        redo it
    GET  /api/v1/whiteboard/log         ?actor=NAME&last=N

An AI agent acting through this API identifies itself with the
``X-Plovane-Agent`` header and is held to its grants. The server binds to
127.0.0.1 only: it can change files on this machine.
"""

from __future__ import annotations

import argparse
import threading
from typing import Any

from flask import Flask, jsonify, request

from whiteboard.engine import USER, Workspace

STATUS = {"INVALID_ARGS": 400, "INVALID_COMMAND": 400, "UNKNOWN_COMMAND": 400, "UNKNOWN_OPERATION": 400,
          "INVALID_STYLE": 400, "INVALID_PATH": 400, "FORBIDDEN": 403, "NOT_FOUND": 404, "CONFLICT": 409,
          "AMBIGUOUS": 409, "TESTS_FAILED": 409, "INTERNAL": 500}


# Input: workspace (Workspace)
# Output: Flask
# Processing: Build the Flask app over one project's workspace.
# Changed by: Sourish
# Date: 2026-10-22
def create_app(workspace: Workspace) -> Flask:
    app = Flask("plovane-whiteboard")
    lock = threading.Lock()  # one operation at a time: the board is shared state

    # Input: reply (dict[str, Any])
    # Output: tuple
    # Processing: Send an envelope with the HTTP status its error implies.
    # Changed by: Sourish
    # Date: 2026-10-22
    def send(reply: dict[str, Any]):
        if reply["ok"]:
            return jsonify(reply), 200
        return jsonify(reply), STATUS.get(reply["error"]["code"], 400)

    # Input: none
    # Output: dict[str, Any]
    # Processing: The request's JSON body, or an empty dict.
    # Changed by: Sourish
    # Date: 2026-10-22
    def body() -> dict[str, Any]:
        raw = request.get_json(silent=True)
        return raw if isinstance(raw, dict) else {}

    # Input: none
    # Output: str
    # Processing: Who is calling: an agent named in the header, or the person.
    # Changed by: Sourish
    # Date: 2026-10-22
    def actor() -> str:
        return request.headers.get("X-Plovane-Agent") or USER

    # Input: none
    # Output: tuple
    # Processing: The board plus each block's and arrow's resolved style.
    # Changed by: Sourish
    # Date: 2026-10-22
    @app.get("/api/v1/whiteboard/board")
    def board():
        from whiteboard.ops.arrows import arrow_record

        with lock:
            current = workspace.board
            data = {
                "project": str(workspace.root),
                "board": current.to_dict(),
                "block_styles": {b.id: current.resolved_style(b) for b in current.blocks.values()},
                "absolute": {b.id: current.absolute(b) for b in current.blocks.values()},
                "arrows": [arrow_record(a) for a in current.arrows.values()],
            }
        return send({"ok": True, "data": data, "error": None})

    # Input: none
    # Output: tuple
    # Processing: The command reference for autocomplete and help.
    # Changed by: Sourish
    # Date: 2026-10-22
    @app.get("/api/v1/whiteboard/commands")
    def commands():
        from whiteboard.commands import COMMANDS
        from whiteboard.registry import OPERATIONS

        listing = [{"command": "/" + c.phrase, "op": c.op, "example": c.example,
                    "summary": OPERATIONS[c.op].summary, "confirms": OPERATIONS[c.op].confirm is True,
                    "people_only": not OPERATIONS[c.op].agents} for c in COMMANDS]
        return send({"ok": True, "data": {"commands": listing}, "error": None})

    # Input: none
    # Output: tuple
    # Processing: Run a slash command.
    # Changed by: Sourish
    # Date: 2026-10-22
    @app.post("/api/v1/whiteboard/command")
    def command():
        raw = body()
        if not isinstance(raw.get("command"), str):
            return send({"ok": False, "data": None, "error": {"code": "INVALID_ARGS", "message": "command is required"}})
        with lock:
            reply = workspace.command(raw["command"], actor=actor(), confirm=bool(raw.get("confirm")),
                                      dry_run=bool(raw.get("dry_run")))
        return send(reply)

    # Input: none
    # Output: tuple
    # Processing: Run a structured operation.
    # Changed by: Sourish
    # Date: 2026-10-22
    @app.post("/api/v1/whiteboard/op")
    def op():
        raw = body()
        if not isinstance(raw.get("op"), str) or not isinstance(raw.get("args", {}), dict):
            return send({"ok": False, "data": None, "error": {"code": "INVALID_ARGS",
                                                             "message": "op (string) and args (object) are required"}})
        with lock:
            reply = workspace.execute(raw["op"], raw.get("args") or {}, actor=actor(),
                                      confirm=bool(raw.get("confirm")), dry_run=bool(raw.get("dry_run")))
        return send(reply)

    # Input: none
    # Output: tuple
    # Processing: Undo the last change.
    # Changed by: Sourish
    # Date: 2026-10-22
    @app.post("/api/v1/whiteboard/undo")
    def undo():
        with lock:
            return send(workspace.execute("history.undo", actor=actor()))

    # Input: none
    # Output: tuple
    # Processing: Redo the last undone change.
    # Changed by: Sourish
    # Date: 2026-10-22
    @app.post("/api/v1/whiteboard/redo")
    def redo():
        with lock:
            return send(workspace.execute("history.redo", actor=actor()))

    # Input: none
    # Output: tuple
    # Processing: The operation log, optionally for one actor.
    # Changed by: Sourish
    # Date: 2026-10-22
    @app.get("/api/v1/whiteboard/log")
    def log():
        last = request.args.get("last", type=int)
        entries = workspace.store.read_log(actor=request.args.get("actor"), last=last or 100)
        return send({"ok": True, "data": {"entries": entries}, "error": None})

    # Input: none
    # Output: tuple
    # Processing: Liveness.
    # Changed by: Sourish
    # Date: 2026-10-22
    @app.get("/healthz")
    def healthz():
        return send({"ok": True, "data": {"project": str(workspace.root)}, "error": None})

    return app


# Input: argv (list[str] | None)
# Output: None
# Processing: Run the local server for one project.
# Changed by: Sourish
# Date: 2026-10-22
def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Plovane whiteboard local API")
    parser.add_argument("--project", default=".", help="the project folder the whiteboard edits")
    parser.add_argument("--port", type=int, default=8700)
    args = parser.parse_args(argv)
    create_app(Workspace(args.project)).run(host="127.0.0.1", port=args.port, threaded=False)


if __name__ == "__main__":
    main()
