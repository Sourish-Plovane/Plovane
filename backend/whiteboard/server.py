"""The whiteboard's local HTTP API, for the desktop app and the CLI.

    python -m backend.whiteboard.server --project /path/to/project     # 127.0.0.1:8700

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
import json
import threading
import uuid
from pathlib import Path
from typing import Any

from flask import Flask, jsonify, request, send_from_directory

from backend.whiteboard.engine import USER, Workspace
from backend.whiteboard.store import now

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
    frontend = Path(__file__).resolve().parents[2] / "frontend" / "whiteboard"
    cli_frontend = frontend.parent / "cli"
    prompt_plans: dict[str, dict[str, Any]] = {}

    # Input: none
    # Output: HTML response
    # Processing: Serve the standalone whiteboard from the existing local API origin.
    # Changed by: Codex
    # Date: 2026-11-07
    @app.get("/whiteboard")
    def whiteboard_page():
        return send_from_directory(frontend, "index.html")

    # Input: none
    # Output: JPEG response
    # Processing: Serve the Plovane logo stored at the frontend root.
    # Changed by: Codex
    # Date: 2026-11-07
    @app.get("/whiteboard/logo.jpeg")
    def whiteboard_logo():
        return send_from_directory(frontend.parent, "1770049027667.jpeg")

    # Input: asset (str)
    # Output: CSS or JavaScript response
    # Processing: Serve only files inside the standalone whiteboard directory.
    # Changed by: Codex
    # Date: 2026-11-07
    @app.get("/whiteboard/<path:asset>")
    def whiteboard_asset(asset: str):
        return send_from_directory(frontend, asset)

    # Input: none
    # Output: HTML response
    # Processing: Serve the embedded command-line frontend from the same local origin.
    # Changed by: Codex
    # Date: 2026-11-09
    @app.get("/cli/")
    def cli_page():
        return send_from_directory(cli_frontend, "index.html")

    # Input: asset (str)
    # Output: CSS or JavaScript response
    # Processing: Serve only files inside the CLI frontend directory.
    # Changed by: Codex
    # Date: 2026-11-09
    @app.get("/cli/<path:asset>")
    def cli_asset(asset: str):
        return send_from_directory(cli_frontend, asset)

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
        from backend.whiteboard.ops.arrows import arrow_record

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
        from backend.whiteboard.commands import COMMANDS
        from backend.whiteboard.registry import OPERATIONS

        listing = [{"command": "/" + c.phrase, "op": c.op, "example": c.example,
                    "summary": OPERATIONS[c.op].summary, "confirms": OPERATIONS[c.op].confirm is True,
                    "people_only": not OPERATIONS[c.op].agents} for c in COMMANDS]
        return send({"ok": True, "data": {"commands": listing}, "error": None})

    # Input: none
    # Output: tuple
    # Processing: Search verified code, ask Claude for slash commands, and preview their effects.
    # Changed by: Codex
    # Date: 2026-11-07
    @app.post("/api/v1/whiteboard/prompt/plan")
    def prompt_plan():
        from backend.whiteboard.commands import parse
        from backend.whiteboard.registry import OPERATIONS, OpError

        raw = body()
        prompt = raw.get("prompt")
        if not isinstance(prompt, str) or not prompt.strip():
            return send({"ok": False, "data": None, "error": {"code": "INVALID_ARGS", "message": "prompt is required"}})
        try:
            with lock:
                ranked = workspace.execute("library.rank", {"query": prompt, "k": 5}, actor=actor())
            if not ranked["ok"]:
                return send(ranked)
            ranking = ranked["data"]["result"]
            recommendations = ranking.get("recommendations", []) if ranking.get("scores_calibrated") else []
            with lock:
                board = workspace.board.to_dict()
            context = {
                "request": prompt.strip(),
                "project_root": str(workspace.root),
                "existing_blocks": [
                    {"id": b["id"], "kind": b["kind"], "name": b["name"],
                     "layer": b["layer"], "path": b.get("path")}
                    for b in board.get("blocks", {}).values()
                ],
                "library_candidates": recommendations,
            }
            llm_reply = workspace.llm_result(
                json.dumps(context, ensure_ascii=False),
                system=("You are Claude, Plovane's whiteboard planner. The verified library was searched first. "
                        "Only use a listed library candidate when it fits; insert it with /insert <id> --into <path>. "
                        "Otherwise create new code using supported whiteboard slash commands. Prefer templates, "
                        "presets, and relative placement. Use one /batch command per independent multi-step change. "
                        "Return JSON only: {\"summary\": string, \"commands\": [slash-command strings]}. "
                        "Commands must be valid Plovane commands. Never claim a write happened; this is a preview."),
                provider="anthropic",
            )
            try:
                plan_data = json.loads(llm_reply["text"])
                commands = plan_data.get("commands")
                summary = str(plan_data.get("summary", ""))
                if not isinstance(commands, list) or not commands or any(not isinstance(c, str) for c in commands):
                    raise ValueError("commands must be a non-empty array of strings")
                if len(commands) > 40:
                    raise ValueError("plan exceeds 40 operations")
                for command in commands:
                    op, args = parse(command)
                    if op not in OPERATIONS:
                        raise ValueError(f"unknown operation in {command}")
                    if op == "library.insert" and args.get("entry") not in {
                            item.get("block_id") for item in recommendations}:
                        raise ValueError("library inserts must use a verified candidate returned by /rank")
                batch_preview = workspace.execute("batch", {"commands": commands}, actor=actor(), dry_run=True)
                if not batch_preview["ok"]:
                    return send(batch_preview)
                plan_id = uuid.uuid4().hex
                prompt_plans[plan_id] = {
                    "prompt": prompt.strip(), "commands": commands, "tokens": llm_reply.get("usage", {}),
                    "actor": actor(), "ranked": recommendations,
                }
                return send({"ok": True, "error": None, "data": {
                    "plan_id": plan_id, "summary": summary, "commands": commands,
                    "preview": batch_preview["data"], "library_candidates": recommendations,
                    "tokens": llm_reply.get("usage", {}),
                }})
            except (ValueError, TypeError, KeyError) as exc:
                return send({"ok": False, "data": None,
                             "error": {"code": "INVALID_PLAN", "message": f"Claude returned an invalid command plan: {exc}"}})
        except OpError as exc:
            return send({"ok": False, "data": None, "error": {"code": exc.code, "message": exc.message}})

    # Input: plan_id (str), confirm (bool)
    # Output: tuple
    # Processing: Apply an approved plan through the ordinary atomic batch and confirmation paths.
    # Changed by: Codex
    # Date: 2026-11-07
    @app.post("/api/v1/whiteboard/prompt/apply")
    def prompt_apply():
        raw = body()
        plan_id = raw.get("plan_id")
        plan = prompt_plans.get(plan_id) if isinstance(plan_id, str) else None
        if not plan:
            return send({"ok": False, "data": None, "error": {"code": "NOT_FOUND", "message": "prompt plan expired or not found"}})
        if plan["actor"] != actor():
            return send({"ok": False, "data": None, "error": {"code": "FORBIDDEN", "message": "prompt plan belongs to another actor"}})
        with lock:
            workspace.prompt_context = {"prompt": plan["prompt"], "tokens": plan["tokens"]}
            try:
                reply = workspace.execute("batch", {"commands": plan["commands"]}, actor=actor(),
                                          confirm=bool(raw.get("confirm")))
            finally:
                workspace.prompt_context = None
            if reply["ok"] and reply["data"].get("status") != "needs_confirmation":
                result = reply["data"]
                files = result.get("changes", {}).get("files", [])
                workspace.store.append_log({
                    "at": now(), "actor": actor(), "op": "prompt.provenance",
                    "args": {"prompt": plan["prompt"], "token_usage": plan["tokens"]},
                    "ok": True, "detail": {"files": files, "commands": plan["commands"]},
                })
                prompt_plans.pop(plan_id, None)
        return send(reply)

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
