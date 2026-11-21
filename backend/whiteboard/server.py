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
    POST /api/v1/whiteboard/traffic     requests the app handled ({"method","path","status",...} or
                                        {"requests": [...]}): arrows go green/red, attacks are flagged
    GET  /api/v1/whiteboard/log         ?actor=NAME&last=N

board, board/file, command, op, undo, redo and prompt/* act on one whiteboard: the
.plo file named by an X-Plovane-Board header or ?board= (default whiteboard.plo).

    GET  /api/v1/whiteboard/board/file   person only: the board as a .plo file of commands (#72)
    GET  /api/v1/whiteboard/boards       every whiteboard (.plo file) in the project, for the tabs
    GET  /api/v1/whiteboard/events      live changes as Server-Sent Events (?token= for EventSource)
    GET  /api/v1/whiteboard/events/recent  ?after=SEQ, the same events as JSON
    POST /api/v1/whiteboard/agents/<name>/token   person only: make an agent and its API token
    DELETE /api/v1/whiteboard/agents/<name>/token person only: revoke it
    GET  /api/v1/whiteboard/providers   Claude / DeepSeek / OpenAI: configured or not
    POST /api/v1/whiteboard/providers/<name>/key  person only: save an API key
    POST /api/v1/whiteboard/agent/run   person only: let Claude or DeepSeek work on the board
    GET  /api/v1/whiteboard/agent/runs[/<id>]     run status;  POST .../<id>/stop stops one
    GET  /api/v1/whiteboard/agents/<name>/chat     ?since=N, the chat with one agent
    POST /api/v1/whiteboard/agents/<name>/chat     {"message": "..."}, see agent_chat.py
    DELETE /api/v1/whiteboard/agents/<name>/chat   forget that chat

Every /api/v1/ request carries ``Authorization: Bearer <token>`` (see
auth.py): the person's token from .plovane/server.json or the served page,
or an agent's token, which holds it to that agent's grants. Without one the
server answers 401. The server binds to 127.0.0.1 only: it can change files
on this machine.
"""

from __future__ import annotations

import argparse
import atexit
import json
import re
import threading
import time
import uuid
from pathlib import Path
from typing import Any

from flask import Flask, Response, g, jsonify, request, send_from_directory, stream_with_context

from backend.whiteboard import llm_agent
from backend.whiteboard.ops.agents import instructions_for
from backend.whiteboard.agent_chat import AgentChat
from backend.whiteboard.auth import Auth, new_token, remove_server_file, write_server_file
from backend.whiteboard.engine import USER, Workspace, board_name
from backend.whiteboard.registry import OpError
from backend.whiteboard.store import MAIN_BOARD
from backend.whiteboard.events import EventBus
from backend.whiteboard.store import now

STATUS = {"INVALID_ARGS": 400, "INVALID_COMMAND": 400, "UNKNOWN_COMMAND": 400, "UNKNOWN_OPERATION": 400,
          "INVALID_STYLE": 400, "INVALID_PATH": 400, "FORBIDDEN": 403, "NOT_FOUND": 404, "CONFLICT": 409,
          "AMBIGUOUS": 409, "TESTS_FAILED": 409, "INTERNAL": 500,
          "UNAUTHENTICATED": 401, "INVALID_PLAN": 422, "LLM_UNAVAILABLE": 503, "MODEL_UNAVAILABLE": 503}
LOCAL_HOSTS = {"127.0.0.1", "localhost", "[::1]"}
"""Host headers the API answers to; anything else is a DNS-rebinding attempt from a web page."""


# Input: text (str)
# Output: Any
# Processing: Parse a model's JSON reply, tolerating a ```json fence around it.
# Changed by: Claude Code
# Date: 2026-11-09
def parse_model_json(text: str) -> Any:
    fenced = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    return json.loads(fenced.group(1) if fenced else text)


# Input: text (str)
# Output: str
# Processing: Drop a ```json ... ``` fence some models put around a JSON-only reply.
# Changed by: Claude
# Date: 2026-11-10
def strip_fence(text: str) -> str:
    body = text.strip()
    if body.startswith("```"):
        body = body.split("\n", 1)[1] if "\n" in body else ""
        body = body.rsplit("```", 1)[0]
    return body.strip()


# Input: none
# Output: list[dict[str, str]]
# Processing: Every slash command with one example, for the planner's prompt.
# Changed by: Claude
# Date: 2026-11-10
def command_reference() -> list[dict[str, str]]:
    from backend.whiteboard.commands import COMMANDS
    from backend.whiteboard.registry import OPERATIONS

    return [{"command": "/" + c.phrase, "example": c.example, "does": OPERATIONS[c.op].summary}
            for c in COMMANDS]


# Input: text (str), recommendations (list[dict])
# Output: tuple[str, list[str], str]
# Processing: Parse a planner reply into (summary, commands, problem); problem is "" when it is valid.
# Changed by: Claude
# Date: 2026-11-10
def check_plan(text: str, recommendations: list[dict[str, Any]]) -> tuple[str, list[str], str]:
    from backend.whiteboard.commands import parse
    from backend.whiteboard.registry import OPERATIONS, OpError

    try:
        plan = parse_model_json(text)
    except json.JSONDecodeError:
        return "", [], "the reply was not JSON"
    if not isinstance(plan, dict):
        return "", [], "the reply was not a JSON object"
    commands = plan.get("commands")
    summary = str(plan.get("summary", ""))
    if not isinstance(commands, list) or not commands or any(not isinstance(c, str) for c in commands):
        return summary, [], "commands must be a non-empty array of strings"
    if len(commands) > 40:
        return summary, [], "the plan has more than 40 commands"
    bad = []
    for command in commands:
        try:
            op, args = parse(command)
        except OpError as exc:
            bad.append(f"{command!r}: {exc.message}")
            continue
        if op not in OPERATIONS:
            bad.append(f"{command!r}: unknown operation")
        elif op == "library.insert" and args.get("entry") not in {r.get("block_id") for r in recommendations}:
            bad.append(f"{command!r}: library inserts must use a listed library candidate")
    if bad:
        return summary, [], "invalid commands: " + "; ".join(bad[:8])
    return summary, commands, ""


# Input: workspace (Workspace), chat (AgentChat | None), person_token (str | None), lock (threading.RLock | None)
# Output: Flask
# Processing: Build the Flask app over one project's workspace; with a person token every API call needs a token; tests inject chat.
# Changed by: Claude Code
# Date: 2026-11-14
def create_app(workspace: Workspace, chat: AgentChat | None = None, *, person_token: str | None = None,
               lock: threading.RLock | None = None) -> Flask:
    app = Flask("plovane-whiteboard")
    lock = lock or threading.RLock()  # one operation at a time: the board is shared state
    frontend = Path(__file__).resolve().parents[2] / "frontend" / "whiteboard"
    cli_frontend = frontend.parent / "cli"
    prompt_plans: dict[str, dict[str, Any]] = {}
    chat = chat or AgentChat(workspace, lock)
    chat.lock = lock  # agent turns and HTTP requests must take the same board lock
    auth = Auth(workspace.store.dir, person_token) if person_token else None
    bus = EventBus()
    workspace.listeners.append(bus.publish)
    boards: dict[str, Workspace] = {MAIN_BOARD: workspace}
    """Every open whiteboard (.plo file), by name; each tab in the page shows one."""
    app.config["plovane_boards"] = boards
    runs: dict[str, dict[str, Any]] = {}
    app.config["plovane_bus"] = bus
    app.config["plovane_lock"] = lock

    # Input: page (Path)
    # Output: Response
    # Processing: An HTML page with the person token in a meta tag, so the page can call the API.
    # Changed by: Claude Code
    # Date: 2026-11-09
    def page_with_token(page: Path) -> Response:
        html = page.read_text(encoding="utf-8")
        if auth:
            meta = f'<meta name="plovane-token" content="{auth.person_token}">'
            html = html.replace("<head>", "<head>\n  " + meta, 1)
        response = Response(html, mimetype="text/html")
        response.headers["Cache-Control"] = "no-store"
        return response

    # Input: none
    # Output: Response | None
    # Processing: Refuse API calls from a non-local Host or without a valid token; record who is calling.
    # Changed by: Claude Code
    # Date: 2026-11-09
    @app.before_request
    def authenticate():
        g.actor = None
        if not request.path.startswith("/api/"):
            return None
        host = (request.host or "").rsplit(":", 1)[0]
        if host not in LOCAL_HOSTS:
            return send({"ok": False, "data": None, "error": {"code": "FORBIDDEN",
                         "message": f"the whiteboard API only answers on 127.0.0.1, not {host!r}"}})
        if auth is None:
            return None
        header = request.headers.get("Authorization", "")
        token = header[7:].strip() if header.lower().startswith("bearer ") else request.args.get("token")
        kind, name = auth.identify(token)
        if kind == "none":
            return send({"ok": False, "data": None, "error": {"code": "UNAUTHENTICATED", "message":
                         "send Authorization: Bearer <token>: the person's token is in .plovane/server.json; "
                         "an agent's comes from 'plovane agent token <name>'"}})
        g.actor = USER if kind == "person" else name
        return None

    # Input: none
    # Output: HTML response
    # Processing: Serve the standalone whiteboard from the existing local API origin.
    # Changed by: Codex
    # Date: 2026-11-07
    @app.get("/whiteboard")
    def whiteboard_page():
        return page_with_token(frontend / "index.html")

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
        return page_with_token(cli_frontend / "index.html")

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
        if auth is not None:
            return g.actor
        return request.headers.get("X-Plovane-Agent") or USER

    # Input: none
    # Output: tuple | None
    # Processing: A FORBIDDEN reply unless the person is calling.
    # Changed by: Claude Code
    # Date: 2026-11-09
    def person_only():
        if actor() != USER:
            return send({"ok": False, "data": None, "error": {"code": "FORBIDDEN",
                         "message": "only the person can do this, not an agent"}})
        return None

    # Input: code (str), message (str)
    # Output: tuple
    # Processing: An error envelope with its HTTP status.
    # Changed by: Claude Code
    # Date: 2026-11-09
    def fail(code: str, message: str):
        return send({"ok": False, "data": None, "error": {"code": code, "message": message}})

    # Input: exc (OpError)
    # Output: tuple
    # Processing: An operation error raised outside an operation (e.g. an unknown whiteboard) as an envelope.
    # Changed by: Claude Code
    # Date: 2026-11-15
    @app.errorhandler(OpError)
    def op_error(exc: OpError):
        return fail(exc.code, exc.message)

    # Input: none
    # Output: Workspace
    # Processing: The whiteboard this request is for (X-Plovane-Board header or ?board=), opening it on first use.
    # Changed by: Claude Code
    # Date: 2026-11-15
    def current() -> Workspace:
        name = board_name(workspace.root, request.headers.get("X-Plovane-Board") or request.args.get("board")
                          or (body().get("board") if request.is_json else None) or MAIN_BOARD)
        with lock:
            if name not in boards:
                if not (workspace.root / name).is_file():
                    raise OpError("NOT_FOUND", f"no whiteboard {name}; make one with /board new")
                opened = Workspace(workspace.root, library=workspace._library, llm=workspace._llm, board=name,
                                   main=workspace)
                opened.listeners.append(bus.publish)
                boards[name] = opened
            return boards[name]

    # Input: target (Workspace)
    # Output: dict[str, Any]
    # Processing: One whiteboard's board envelope, after picking up changes other processes made.
    # Changed by: Claude Code
    # Date: 2026-11-09
    def board_reply(target: Workspace) -> dict[str, Any]:
        from backend.whiteboard.ops import live
        from backend.whiteboard.ops.arrows import arrow_record

        with lock:
            target.sync()
            current = target.board
            data = {
                "project": str(target.root),
                "file": target.board_file,  # which whiteboard (.plo) this is
                "file_error": target.plo_error,
                "board": current.to_dict(),
                "block_styles": {b.id: current.resolved_style(b) for b in current.blocks.values()},
                "absolute": {b.id: current.absolute(b) for b in current.blocks.values()},
                "arrows": [arrow_record(a) for a in current.arrows.values()],
                "access": target.access_map(),  # which agents reach each block, for the chips
                "live": live.snapshot(target),  # agents working, API pass/fail, attacks (ops/live.py)
                "about": target.main.board.view.get("about", ""),  # what the whole project is
                "seq": bus.seq,
            }
        return {"ok": True, "data": data, "error": None}

    # Input: none
    # Output: dict[str, Any]
    # Processing: Every slash command as an envelope.
    # Changed by: Claude Code
    # Date: 2026-11-09
    def commands_reply() -> dict[str, Any]:
        from backend.whiteboard.commands import COMMANDS
        from backend.whiteboard.registry import OPERATIONS

        listing = [{"command": "/" + c.phrase, "op": c.op, "example": c.example,
                    "summary": OPERATIONS[c.op].summary, "confirms": OPERATIONS[c.op].confirm is True,
                    "people_only": not OPERATIONS[c.op].agents} for c in COMMANDS]
        return {"ok": True, "data": {"commands": listing}, "error": None}

    # Input: none
    # Output: tuple
    # Processing: The board plus each block's and arrow's resolved style.
    # Changed by: Sourish
    # Date: 2026-10-22
    @app.get("/api/v1/whiteboard/board")
    def board():
        return send(board_reply(current()))

    # Input: none
    # Output: tuple
    # Processing: Every whiteboard (.plo file) in the project, the main one first, for the tabs.
    # Changed by: Claude Code
    # Date: 2026-11-15
    @app.get("/api/v1/whiteboard/boards")
    def board_list():
        from backend.whiteboard.ops.boardfile import whiteboards

        return send({"ok": True, "data": {"main": MAIN_BOARD, "boards": whiteboards(workspace.root)}, "error": None})

    # Input: none
    # Output: tuple
    # Processing: The board as a .plo whiteboard file (JSON full of commands) for the browser to download; person only.
    # Changed by: Claude Code
    # Date: 2026-11-15
    @app.get("/api/v1/whiteboard/board/file")
    def board_file_download():
        from backend.whiteboard.ops.boardfile import board_file

        refused = person_only()
        if refused:
            return refused
        target = current()
        with lock:
            target.sync()
            content = board_file(target.board, target.root.name, agents=target.is_main)
        stem = target.root.name if target.is_main else Path(target.board_file).stem
        name = "".join(c if c.isalnum() or c in "-_" else "-" for c in stem) or "whiteboard"
        return send({"ok": True, "data": {"name": f"{name}.plo", "file": content}, "error": None})

    # Input: none
    # Output: tuple
    # Processing: The command reference for autocomplete and help.
    # Changed by: Sourish
    # Date: 2026-10-22
    @app.get("/api/v1/whiteboard/commands")
    def commands():
        return send(commands_reply())

    # Input: none
    # Output: tuple
    # Processing: Search verified code, ask Claude, DeepSeek or OpenAI for slash commands (one retry), and preview them.
    # Changed by: Claude Code
    # Date: 2026-11-14
    @app.post("/api/v1/whiteboard/prompt/plan")
    def prompt_plan():
        from backend.whiteboard.registry import OpError

        raw = body()
        prompt = raw.get("prompt")
        if not isinstance(prompt, str) or not prompt.strip():
            return send({"ok": False, "data": None, "error": {"code": "INVALID_ARGS", "message": "prompt is required"}})
        try:
            provider = llm_agent.canonical(raw.get("provider") or __import__("os").environ.get(
                "PLOVANE_LLM_PROVIDER", "anthropic"))
        except llm_agent.AgentError as exc:
            return fail("INVALID_ARGS", str(exc))
        target = current()
        try:
            with lock:
                ranked = target.execute("library.rank", {"query": prompt, "k": 5}, actor=actor())
            # The library is a bonus: when the model is not installed, plan without it.
            ranking = ranked["data"]["result"] if ranked["ok"] else {}
            recommendations = ranking.get("recommendations", []) if ranking.get("scores_calibrated") else []
            with lock:
                target.sync()
                board = target.board.to_dict()
            context = {
                "request": prompt.strip(),
                "project_root": str(workspace.root),
                "existing_blocks": [
                    {"id": b["id"], "kind": b["kind"], "name": b["name"],
                     "layer": b["layer"], "path": b.get("path")}
                    for b in board.get("blocks", {}).values()
                ],
                "library_candidates": recommendations,
                # The only commands that exist. Without this list models invent ones like /tree.
                "command_reference": command_reference(),
            }
            system = ("You are Plovane's whiteboard planner. The verified library was searched first. "
                      "Only use a listed library candidate when it fits; insert it with /insert <id> --into <path>. "
                      "Otherwise create new code using whiteboard slash commands. Every command must start with "
                      "one of the command names in command_reference and follow its example's syntax; never "
                      "invent a command. Refer to existing blocks by their id. For drawing services, databases, "
                      "queues, external APIs and the arrows between them, use ONE /sketch command with short "
                      "lines separated by ';' (e.g. '/sketch service orders; database postgres; orders -> "
                      "postgres'); it lays everything out itself, so never give coordinates. Return JSON only: {\"summary\": string, \"commands\": "
                      "[slash-command strings]}. Never claim a write happened; this is a preview.")
            usage: dict[str, Any] = {}
            problem = ""
            for _ in range(2):  # one retry, told exactly what was wrong
                if problem:
                    context["previous_plan_was_rejected"] = problem
                llm_reply = target.llm_result(json.dumps(context, ensure_ascii=False), system=system,
                                                 provider=provider)
                for key, value in (llm_reply.get("usage") or {}).items():
                    if isinstance(value, (int, float)):
                        usage[key] = usage.get(key, 0) + value
                summary, commands, problem = check_plan(llm_reply["text"], recommendations)
                if problem:
                    continue
                with lock:
                    batch_preview = target.execute("batch", {"commands": commands}, actor=actor(), dry_run=True)
                if not batch_preview["ok"]:
                    problem = f"the plan failed its dry run: {batch_preview['error']['message']}"
                    continue
                plan_id = uuid.uuid4().hex
                prompt_plans[plan_id] = {
                    "prompt": prompt.strip(), "commands": commands, "tokens": usage,
                    "actor": actor(), "ranked": recommendations, "board": target.board_file,
                }
                return send({"ok": True, "error": None, "data": {
                    "plan_id": plan_id, "summary": summary, "commands": commands, "provider": provider,
                    "preview": batch_preview["data"], "library_candidates": recommendations,
                    "tokens": usage,
                }})
            return send({"ok": False, "data": None, "error": {
                "code": "INVALID_PLAN",
                "message": f"The planner could not produce a valid plan ({problem}). Try rephrasing, "
                           "or use a / command directly."}})
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
        target = boards.get(plan.get("board", MAIN_BOARD), workspace)  # the whiteboard it was planned on
        with lock:
            target.prompt_context = {"prompt": plan["prompt"], "tokens": plan["tokens"]}
            try:
                reply = target.execute("batch", {"commands": plan["commands"]}, actor=actor(),
                                       confirm=bool(raw.get("confirm")))
            finally:
                target.prompt_context = None
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
        target = current()
        with lock:
            target.current_command = raw["command"]
            try:
                reply = target.command(raw["command"], actor=actor(), confirm=bool(raw.get("confirm")),
                                       dry_run=bool(raw.get("dry_run")))
            finally:
                target.current_command = None
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
        target = current()
        with lock:
            reply = target.execute(raw["op"], raw.get("args") or {}, actor=actor(),
                                   confirm=bool(raw.get("confirm")), dry_run=bool(raw.get("dry_run")))
        return send(reply)

    # Input: none
    # Output: tuple
    # Processing: Take requests the app handled (one, or {"requests": [...]}) for live API status and attack checks.
    # Changed by: Claude Code
    # Date: 2026-11-18
    @app.post("/api/v1/whiteboard/traffic")
    def traffic():
        raw = body()
        batch = raw.get("requests") if isinstance(raw.get("requests"), list) else [raw]
        with lock:
            reply = workspace.execute("traffic.record", {"requests": batch}, actor=actor())
        return send(reply)

    # Input: none
    # Output: tuple
    # Processing: Undo the last change.
    # Changed by: Sourish
    # Date: 2026-10-22
    @app.post("/api/v1/whiteboard/undo")
    def undo():
        target = current()
        with lock:
            return send(target.execute("history.undo", actor=actor()))

    # Input: none
    # Output: tuple
    # Processing: Redo the last undone change.
    # Changed by: Sourish
    # Date: 2026-10-22
    @app.post("/api/v1/whiteboard/redo")
    def redo():
        target = current()
        with lock:
            return send(target.execute("history.redo", actor=actor()))

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
    # Output: Response
    # Processing: Stream every change as Server-Sent Events, resuming after Last-Event-ID or ?after.
    # Changed by: Claude Code
    # Date: 2026-11-09
    @app.get("/api/v1/whiteboard/events")
    def events():
        start = request.headers.get("Last-Event-ID") or request.args.get("after")
        after = int(start) if start and str(start).isdigit() else bus.seq
        response = Response(stream_with_context(bus.stream(after)), mimetype="text/event-stream")
        response.headers["Cache-Control"] = "no-cache"
        response.headers["X-Accel-Buffering"] = "no"
        return response

    # Input: none
    # Output: tuple
    # Processing: Events after ?after=SEQ as JSON, for clients that cannot hold a stream open.
    # Changed by: Claude Code
    # Date: 2026-11-09
    @app.get("/api/v1/whiteboard/events/recent")
    def events_recent():
        after = request.args.get("after", default=0, type=int)
        return send({"ok": True, "data": {"seq": bus.seq, "events": bus.since(after)}, "error": None})

    # Input: name (str)
    # Output: tuple
    # Processing: Person only: add the agent if new, optionally grant it access, and issue its API token.
    # Changed by: Claude Code
    # Date: 2026-11-09
    @app.post("/api/v1/whiteboard/agents/<name>/token")
    def agent_token(name: str):
        denied = person_only()
        if denied:
            return denied
        if auth is None:
            return fail("CONFLICT", "this server runs without tokens (test mode); start it with "
                                    "python -m backend.whiteboard.server")
        raw = body()
        with lock:
            prepared = ensure_agent(name, raw.get("access"), raw.get("path") or ".")
            if prepared is not None:
                return send(prepared)
            token = auth.issue(name)
        bus.publish({"at": now(), "kind": "agent", "type": "token", "actor": USER, "agent": name})
        return send({"ok": True, "error": None, "data": {
            "agent": name, "token": token, "grants": workspace.board.agents[name].grants,
            "use": "send it as 'Authorization: Bearer <token>'; it is shown once"}})

    # Input: name (str)
    # Output: tuple
    # Processing: Person only: revoke every token an agent has.
    # Changed by: Claude Code
    # Date: 2026-11-09
    @app.delete("/api/v1/whiteboard/agents/<name>/token")
    def agent_token_revoke(name: str):
        denied = person_only()
        if denied:
            return denied
        if auth is None or not auth.revoke(name):
            return fail("NOT_FOUND", f"agent {name!r} has no token")
        return send({"ok": True, "data": {"agent": name, "revoked": True}, "error": None})

    # Input: name (str), access (str | None), path (str)
    # Output: dict[str, Any] | None
    # Processing: Add an agent if missing and grant it access when asked; an error envelope on failure.
    # Changed by: Claude Code
    # Date: 2026-11-09
    def ensure_agent(name: str, access: str | None, path: str) -> dict[str, Any] | None:
        if name not in workspace.board.agents:
            added = workspace.execute("agent.add", {"name": name}, actor=USER)
            if not added["ok"]:
                return added
        if access and access != "existing":
            granted = workspace.execute("agent.grant", {"agent": name, "path": path, "mode": access},
                                        actor=USER, confirm=True)
            if not granted["ok"]:
                return granted
        return None

    # Input: none
    # Output: tuple
    # Processing: Which AI providers are set up, with their models; never the keys.
    # Changed by: Claude Code
    # Date: 2026-11-09
    @app.get("/api/v1/whiteboard/providers")
    def providers():
        return send({"ok": True, "data": {"providers": llm_agent.providers()}, "error": None})

    # Input: name (str)
    # Output: tuple
    # Processing: Person only: save a provider's API key to ~/.plovane/keys.json and use it now.
    # Changed by: Claude Code
    # Date: 2026-11-09
    @app.post("/api/v1/whiteboard/providers/<name>/key")
    def provider_key(name: str):
        denied = person_only()
        if denied:
            return denied
        key = body().get("key")
        if not isinstance(key, str) or len(key.strip()) < 8:
            return fail("INVALID_ARGS", "key is required")
        try:
            provider = llm_agent.save_key(name, key.strip())
        except llm_agent.AgentError as exc:
            return fail("INVALID_ARGS", str(exc))
        return send({"ok": True, "data": {"provider": provider, "configured": True}, "error": None})

    # Input: none
    # Output: tuple
    # Processing: Person only: start Claude, DeepSeek or OpenAI working on the board as an agent added in the CLI.
    # Changed by: Claude Code
    # Date: 2026-11-09
    @app.post("/api/v1/whiteboard/agent/run")
    def agent_run():
        denied = person_only()
        if denied:
            return denied
        raw = body()
        task = raw.get("prompt")
        if not isinstance(task, str) or not task.strip():
            return fail("INVALID_ARGS", "prompt is required")
        try:
            provider = llm_agent.canonical(raw.get("provider") or "anthropic")
        except llm_agent.AgentError as exc:
            return fail("INVALID_ARGS", str(exc))
        if not llm_agent.api_key(provider):
            return fail("LLM_UNAVAILABLE", f"no API key for {provider}: add one in Settings or run "
                                           f"'plovane connect {'claude' if provider == 'anthropic' else provider}'")
        name = str(raw.get("agent") or ("claude" if provider == "anthropic" else provider))
        if name not in workspace.board.agents:
            # Agents are added in the Plovane CLI, not from the whiteboard.
            return fail("NOT_FOUND", f"no agent {name!r}: add it in the Plovane CLI first "
                                     f"(plovane agent add {name} --access read-write)")
        if any(r["agent"] == name and r["status"] == "running" for r in runs.values()):
            return fail("CONFLICT", f"agent {name!r} is already running; stop it first")
        access = raw.get("access", "read-write")
        with lock:
            prepared = ensure_agent(name, access, raw.get("path") or ".")
        if prepared is not None:
            return send(prepared)
        run_id = uuid.uuid4().hex[:12]
        stop = threading.Event()
        record = {"id": run_id, "agent": name, "provider": provider, "model": llm_agent.model_for(provider, raw.get("model")),
                  "prompt": task.strip(), "status": "running", "started": now(), "steps": [], "result": None,
                  "error": None, "_stop": stop}
        runs[run_id] = record

        # Input: event (dict[str, Any])
        # Output: None
        # Processing: Keep a run's step and publish it as a live event.
        # Changed by: Claude Code
        # Date: 2026-11-09
        def on_step(event: dict[str, Any]) -> None:
            record["steps"].append(event)
            bus.publish({"at": now(), "kind": "agent", "actor": name, "run": run_id, **event})

        # Input: none
        # Output: None
        # Processing: Run the agent loop to the end and record how it finished.
        # Changed by: Claude Code
        # Date: 2026-11-09
        def work() -> None:
            client = llm_agent.LocalClient(workspace, lock, name, board_reply, commands_reply)
            try:
                profile = workspace.board.agents.get(name)
                result = llm_agent.run(task.strip(), provider=provider, client=client,
                                       model=raw.get("model") or (profile.model if profile else None) or None,
                                       max_steps=int(raw.get("max_steps") or 30), on_step=on_step, stop=stop,
                                       instructions=instructions_for(workspace.board, name))
                record.update(status=result["status"], result=result)
            except Exception as exc:  # noqa: BLE001  (report any failure to the person)
                record.update(status="error", error=str(exc))
            record["finished"] = now()
            bus.publish({"at": now(), "kind": "agent", "actor": name, "run": run_id, "type": "finished",
                         "status": record["status"], "error": record["error"],
                         "summary": (record["result"] or {}).get("summary")})

        bus.publish({"at": now(), "kind": "agent", "actor": name, "run": run_id, "type": "started",
                     "provider": provider, "model": record["model"], "prompt": record["prompt"]})
        threading.Thread(target=work, name=f"plovane-agent-{run_id}", daemon=True).start()
        return send({"ok": True, "data": public_run(record), "error": None})

    # Input: record (dict[str, Any])
    # Output: dict[str, Any]
    # Processing: A run without its private stop flag.
    # Changed by: Claude Code
    # Date: 2026-11-09
    def public_run(record: dict[str, Any]) -> dict[str, Any]:
        return {k: v for k, v in record.items() if not k.startswith("_")}

    # Input: none
    # Output: tuple
    # Processing: Every agent run this server started, newest first.
    # Changed by: Claude Code
    # Date: 2026-11-09
    @app.get("/api/v1/whiteboard/agent/runs")
    def agent_runs():
        listing = sorted((public_run(r) for r in runs.values()), key=lambda r: r["started"], reverse=True)
        return send({"ok": True, "data": {"runs": listing}, "error": None})

    # Input: run_id (str)
    # Output: tuple
    # Processing: One run's status, steps and result.
    # Changed by: Claude Code
    # Date: 2026-11-09
    @app.get("/api/v1/whiteboard/agent/runs/<run_id>")
    def agent_run_status(run_id: str):
        record = runs.get(run_id)
        return send({"ok": True, "data": public_run(record), "error": None}) if record else fail(
            "NOT_FOUND", f"no run {run_id!r}")

    # Input: run_id (str)
    # Output: tuple
    # Processing: Person only: ask a running agent to stop after its current step.
    # Changed by: Claude Code
    # Date: 2026-11-09
    @app.post("/api/v1/whiteboard/agent/runs/<run_id>/stop")
    def agent_run_stop(run_id: str):
        denied = person_only()
        if denied:
            return denied
        record = runs.get(run_id)
        if not record:
            return fail("NOT_FOUND", f"no run {run_id!r}")
        record["_stop"].set()
        return send({"ok": True, "data": {"id": run_id, "stopping": record["status"] == "running"}, "error": None})

    # Input: name (str)
    # Output: tuple
    # Processing: The chat with one agent: grants, running flag and events from ?since=N.
    # Changed by: Claude
    # Date: 2026-11-09
    @app.get("/api/v1/whiteboard/agents/<name>/chat")
    def agent_chat_read(name: str):
        since = max(request.args.get("since", default=0, type=int), 0)
        return send({"ok": True, "data": chat.snapshot(name, since), "error": None})

    # Input: name (str)
    # Output: tuple
    # Processing: Send the agent a message; its reply arrives through the GET above.
    # Changed by: Claude
    # Date: 2026-11-09
    @app.post("/api/v1/whiteboard/agents/<name>/chat")
    def agent_chat_send(name: str):
        message = body().get("message")
        if not isinstance(message, str) or not message.strip():
            return send({"ok": False, "data": None, "error": {"code": "INVALID_ARGS", "message": "message is required"}})
        try:
            return send({"ok": True, "data": chat.send(name, message.strip()), "error": None})
        except LookupError as exc:
            return send({"ok": False, "data": None, "error": {"code": "NOT_FOUND", "message": str(exc)}})
        except RuntimeError as exc:
            return send({"ok": False, "data": None, "error": {"code": "CONFLICT", "message": str(exc)}})

    # Input: name (str)
    # Output: tuple
    # Processing: Forget the chat with one agent.
    # Changed by: Claude
    # Date: 2026-11-09
    @app.delete("/api/v1/whiteboard/agents/<name>/chat")
    def agent_chat_reset(name: str):
        try:
            chat.reset(name)
        except RuntimeError as exc:
            return send({"ok": False, "data": None, "error": {"code": "CONFLICT", "message": str(exc)}})
        return send({"ok": True, "data": {"agent": name, "cleared": True}, "error": None})

    # Input: none
    # Output: tuple
    # Processing: Liveness.
    # Changed by: Sourish
    # Date: 2026-10-22
    @app.get("/healthz")
    def healthz():
        return send({"ok": True, "data": {"project": str(workspace.root), "auth": auth is not None},
                     "error": None})

    return app


# Input: workspace (Workspace), lock (threading.RLock), interval (float), boards (dict[str, Workspace] | None)
# Output: threading.Thread
# Processing: Watch every open whiteboard's cache and .plo file, and reload one (announcing it) when something else writes it.
# Changed by: Claude Code
# Date: 2026-11-15
def start_watcher(workspace: Workspace, lock: threading.RLock, interval: float = 0.5,
                  boards: dict[str, Workspace] | None = None) -> threading.Thread:
    # Input: none
    # Output: None
    # Processing: Poll the stamps forever; git pull, a teammate's push or a hand edit shows up within a second.
    # Changed by: Claude Code
    # Date: 2026-11-15
    def watch() -> None:
        while True:
            time.sleep(interval)
            for board in list((boards or {workspace.board_file: workspace}).values()):
                if board.store.board_changed():
                    with lock:
                        board.sync()

    thread = threading.Thread(target=watch, name="plovane-board-watcher", daemon=True)
    thread.start()
    return thread


# Input: pid (int), state_dir (Path)
# Output: threading.Thread
# Processing: Exit the server when the process that started it (the desktop app) dies, so no orphan holds the port.
# Changed by: Claude Code
# Date: 2026-11-09
def start_parent_watch(pid: int, state_dir: Path) -> threading.Thread:
    import os

    # Input: none
    # Output: None
    # Processing: Check every second that the parent is alive; remove server.json and exit when not.
    # Changed by: Claude Code
    # Date: 2026-11-09
    def watch() -> None:
        while True:
            time.sleep(1.0)
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                remove_server_file(state_dir)
                os._exit(0)
            except PermissionError:
                pass  # alive, owned by someone else

    thread = threading.Thread(target=watch, name="plovane-parent-watch", daemon=True)
    thread.start()
    return thread


# Input: none
# Output: threading.Thread | None
# Processing: Load the library model in the background, outside the board lock, so the first search does not freeze the board.
# Changed by: Claude Code
# Date: 2026-11-09
def start_library_warmup() -> threading.Thread | None:
    import os

    entries = Path(__file__).resolve().parents[2] / "ml" / "data" / "entries.jsonl"
    if os.environ.get("PLOVANE_NO_WARMUP") or not entries.exists():
        return None

    # Input: none
    # Output: None
    # Processing: Build the shared engine once; a failure only means the first search loads it instead.
    # Changed by: Claude Code
    # Date: 2026-11-09
    def warm() -> None:
        try:
            from ml.serving.engine import get_engine

            get_engine()
        except Exception:  # noqa: BLE001  (missing torch or weights: library ops report it themselves)
            pass

    thread = threading.Thread(target=warm, name="plovane-library-warmup", daemon=True)
    thread.start()
    return thread


# Input: argv (list[str] | None)
# Output: None
# Processing: Run the local server for one project, with a fresh person token recorded in server.json.
# Changed by: Claude Code
# Date: 2026-11-09
def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Plovane whiteboard local API")
    parser.add_argument("--project", default=".", help="the project folder the whiteboard edits")
    parser.add_argument("--port", type=int, default=8700)
    parser.add_argument("--exit-with", type=int, default=None, metavar="PID",
                        help="stop when this process (the desktop app) is gone")
    args = parser.parse_args(argv)
    llm_agent.load_saved_keys()
    workspace = Workspace(args.project)
    token = new_token()
    lock = threading.RLock()
    app = create_app(workspace, person_token=token, lock=lock)
    workspace.store.ensure()
    write_server_file(workspace.store.dir, args.port, workspace.root, token)
    atexit.register(remove_server_file, workspace.store.dir)
    start_watcher(workspace, lock, boards=app.config["plovane_boards"])
    start_library_warmup()
    if args.exit_with:
        start_parent_watch(args.exit_with, workspace.store.dir)
    print(f"Plovane whiteboard for {workspace.root} on http://127.0.0.1:{args.port}/whiteboard", flush=True)
    app.run(host="127.0.0.1", port=args.port, threaded=True)


if __name__ == "__main__":
    main()
