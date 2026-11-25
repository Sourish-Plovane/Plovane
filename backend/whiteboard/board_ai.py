"""The whiteboard AI: the one AI that builds and changes a project's whiteboard.

Outside AIs (Claude Code, Cursor, any MCP client) no longer draw on the board
themselves. They read it, and when they want a change they ask this AI in
plain English (``plovane_ask`` over MCP, ``POST /api/v1/whiteboard/ask``
over HTTP). It decides how the board should change, makes the change with the
same slash commands a person types, and replies with what it did.

It acts as the agent ``plovane`` (:data:`engine.BOARD_AI`), so every change
it makes is in the undo history and shows up live. The engine refuses any
operation that would change a project file when this agent runs it: the
whiteboard AI changes the board, never code.

Building a board is hybrid. ``/project import`` lays out folders, files,
import arrows, one-line summaries and API routes from the code for free;
then one short tool loop on a lightweight model (Haiku 5.5 by default)
organises it: services, outside systems, arrow labels.

    PLOVANE_BOARD_AI_PROVIDER   anthropic | deepseek | openai (default: the first with a key)
    PLOVANE_BOARD_AI_MODEL      model id (default: claude-haiku-5-5 for Claude)
"""

from __future__ import annotations

import os
import threading
from typing import Any, Callable

from backend.whiteboard import llm_agent
from backend.whiteboard.engine import BOARD_AI, USER

DEFAULT_MODELS = {"anthropic": "claude-haiku-5-5", "deepseek": "deepseek-chat", "openai": "gpt-5-mini"}
MAX_ASK_STEPS = 20
MAX_BUILD_STEPS = 25

SYSTEM = """You are the Plovane whiteboard AI. You own this project's whiteboard: a live,
zoomable map of the code that people (many of them not programmers) read to understand it.
Blocks (services, databases, folders, files, functions) sit on zoom layers, joined by arrows.

Every change to the board goes through you. People and other AIs (Claude Code, Cursor) send you
requests; you decide how the board should change and make the change. You change the board
only, never project code: commands that would write a project file are refused, so do not try
them. If a request needs code changed, say so in your reply instead.

Work only through the tools. run_command takes one Plovane slash command exactly as a person
types it, for example:
  /sketch service payments; database postgres; payments -> postgres "stores charges"
  /block edit <id> --summary "Takes card payments"
  /overlay on log_errors
To explain a file as zoomable blocks, read it with /open <file>, then run
  /blocks generate <file> --summary "..." --blocks '[{"name": ..., "summary": ..., "lines": [start, end]}]'
summary first: one or two plain sentences on what the whole file does and why it exists. Then
blocks top to bottom, each named by what it does in 2 to 6 plain words (not the function name),
with a one-sentence summary and real line numbers; split a long part into children.
Call get_board before changing anything, and list_commands before guessing syntax. Keep the
board tidy: reuse blocks that exist, do not duplicate them, name things by what they do in
plain words. A destructive command answers needs_confirmation with a preview: only repeat it
with confirm=true when the change is what was asked. If a command fails, read the error and fix
it; do not repeat it unchanged. When done, reply with a short plain-English summary of what
you changed on the board."""

ORGANISE = """The project's folders, files, import arrows and API routes were just mapped onto
the board from the code. Organise it so a newcomer understands the project at a glance:
1. Read the board. Add a service block (with /sketch) for each major part of the project, named
   by what it does, and databases, queues or outside APIs the code clearly talks to.
2. Join them with arrows labelled in a few plain words ("stores orders", "charges cards").
3. Do not remove or rename what the import made. Use at most 15 commands.
Finish with two or three sentences on how the project is laid out."""


# Input: ws (Workspace)
# Output: None
# Processing: Put the whiteboard AI on the project with read-write access to everything, once.
# Changed by: Claude Code
# Date: 2026-11-25
def ensure_board_ai(ws: Any) -> None:
    agents = ws.main.board.agents if hasattr(ws, "main") else ws.board.agents
    if BOARD_AI not in agents:
        added = ws.execute("agent.add", {"name": BOARD_AI}, actor=USER)
        if not added["ok"]:
            raise llm_agent.AgentError(f"could not add the whiteboard AI: {added['error']['message']}")
    agent = (ws.main.board.agents if hasattr(ws, "main") else ws.board.agents)[BOARD_AI]
    if not any(g["path"].strip("/") in ("", ".") and g["mode"] == "read-write" for g in agent.grants):
        granted = ws.execute("agent.grant", {"agent": BOARD_AI, "path": ".", "mode": "read-write"},
                             actor=USER, confirm=True)
        if not granted["ok"]:
            raise llm_agent.AgentError(f"could not grant the whiteboard AI: {granted['error']['message']}")


# Input: provider (str | None), model (str | None)
# Output: tuple[str, str]
# Processing: The provider and model the whiteboard AI uses: explicit, then PLOVANE_BOARD_AI_*, then the first provider with a key on its lightweight model.
# Changed by: Claude Code
# Date: 2026-11-25
def provider_and_model(provider: str | None = None, model: str | None = None) -> tuple[str, str]:
    chosen = provider or os.environ.get("PLOVANE_BOARD_AI_PROVIDER")
    if chosen:
        name = llm_agent.canonical(chosen)
    else:
        name = next((p for p in llm_agent.PROVIDERS if llm_agent.api_key(p)), "anthropic")
    return name, model or os.environ.get("PLOVANE_BOARD_AI_MODEL") or DEFAULT_MODELS[name]


# Input: ws (Workspace)
# Output: dict[str, Any]
# Processing: The board envelope the agent loop reads with get_board.
# Changed by: Claude Code
# Date: 2026-11-25
def board_reply(ws: Any) -> dict[str, Any]:
    return {"ok": True, "data": {"board": ws.board.to_dict()}, "error": None}


# Input: none
# Output: dict[str, Any]
# Processing: The command list envelope the agent loop reads with list_commands.
# Changed by: Claude Code
# Date: 2026-11-25
def commands_reply() -> dict[str, Any]:
    from backend.whiteboard.commands import COMMANDS
    from backend.whiteboard.registry import OPERATIONS

    return {"ok": True, "error": None, "data": {"commands": [
        {"command": "/" + c.phrase, "example": c.example, "summary": OPERATIONS[c.op].summary,
         "people_only": not OPERATIONS[c.op].agents} for c in COMMANDS]}}


# Input: ws (Workspace), message (str), requester (str), lock (threading.RLock | None), provider (str | None), model (str | None), on_step (Callable | None), max_steps (int), stop (threading.Event | None)
# Output: dict[str, Any]
# Processing: Have the whiteboard AI carry out one request on the board and return its summary and the commands it ran.
# Changed by: Claude Code
# Date: 2026-11-25
def ask(ws: Any, message: str, *, requester: str = USER, lock: Any = None, provider: str | None = None,
        model: str | None = None, on_step: Callable[[dict[str, Any]], None] | None = None,
        max_steps: int = MAX_ASK_STEPS, stop: threading.Event | None = None) -> dict[str, Any]:
    text = (message or "").strip()
    if not text:
        raise llm_agent.AgentError("ask the whiteboard AI something, e.g. 'show the payment flow'")
    lock = lock or threading.RLock()
    with lock:
        ensure_board_ai(ws)
    name, chosen_model = provider_and_model(provider, model)
    client = llm_agent.LocalClient(ws, lock, BOARD_AI, lambda: board_reply(ws), commands_reply)
    who = "the person" if requester == USER else f"the AI agent {requester!r}"
    result = llm_agent.run(f"Request from {who}:\n{text}", provider=name, client=client, model=chosen_model,
                           max_steps=max_steps, on_step=on_step, stop=stop, system=SYSTEM)
    return {**result, "requester": requester}


# Input: ws (Workspace), lock (threading.RLock | None), provider (str | None), model (str | None), on_step (Callable | None), organise (bool)
# Output: dict[str, Any]
# Processing: Build the board: import the project from the code, then let the whiteboard AI organise it (skipped, with a note, when no AI key is set).
# Changed by: Claude Code
# Date: 2026-11-25
def build(ws: Any, *, lock: Any = None, provider: str | None = None, model: str | None = None,
          on_step: Callable[[dict[str, Any]], None] | None = None, organise: bool = True) -> dict[str, Any]:
    lock = lock or threading.RLock()
    with lock:
        ensure_board_ai(ws)
        imported = ws.execute("project.import", {}, actor=BOARD_AI)
    if not imported["ok"]:
        return {"status": "error", "import": imported, "organise": None}
    out: dict[str, Any] = {"status": "done", "import": imported["data"]["result"], "organise": None}
    if not organise:
        return out
    name, _ = provider_and_model(provider, model)
    if not llm_agent.api_key(name):
        out["note"] = (f"organising skipped: no API key for {name}; run "
                       f"'plovane connect {'claude' if name == 'anthropic' else name}'")
        return out
    try:
        out["organise"] = ask(ws, ORGANISE, requester=BOARD_AI, lock=lock, provider=provider, model=model,
                              on_step=on_step, max_steps=MAX_BUILD_STEPS)
    except llm_agent.AgentError as exc:  # the import still stands; say why organising did not happen
        out["note"] = f"organising failed: {exc}"
    return out
