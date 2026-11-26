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

It also watches the project. When files change outside the whiteboard (Claude
Code or a person editing in their editor), :class:`ProjectWatcher` waits for
the edits to settle and refreshes the board from the code (``/project refresh``),
with no AI: the board follows the code as normal. Changes the whiteboard itself
wrote are ignored. The AI steps in only when a new kind of signal appears
(the first failing API call, log errors, a trace...): it decides whether an
overlay is needed to show it and, if so, switches on a premade one or writes
one as if/else rules (:mod:`backend.whiteboard.overlay_rules`).

    PLOVANE_BOARD_AI_PROVIDER   anthropic | deepseek | openai (default: the first with a key)
    PLOVANE_BOARD_AI_MODEL      model id (default: claude-haiku-5-5 for Claude)
    PLOVANE_BOARD_AI_WATCH      0 to stop watching the project
"""

from __future__ import annotations

import difflib
import json
import os
import threading
import time
from pathlib import Path
from typing import Any, Callable

from backend.whiteboard import codegen, llm_agent
from backend.whiteboard.engine import BOARD_AI, USER
from backend.whiteboard.store import SKIP_DIRS

DEFAULT_MODELS = {"anthropic": "claude-haiku-5-5", "deepseek": "deepseek-chat", "openai": "gpt-5-mini"}
MAX_ASK_STEPS = 20
MAX_BUILD_STEPS = 30
WATCHER = "project watcher"
MAX_OVERLAY_STEPS = 6
SIGNALS = {
    "api_traffic": "requests are reaching the app's API routes (live status per route)",
    "api_failures": "some API requests are failing",
    "security_events": "attack-like requests or security events were recorded",
    "log_errors": "a log scan found errors pointing at project files",
    "trace": "a test trace recorded which lines ran and which never did",
    "outside_apis": "the code calls outside APIs",
}
WATCH_SECONDS = 1.0
QUIET_SECONDS = 1.5
"""Wait this long after the last edit before redrawing, so a burst of saves is one update."""
MAX_TEXT = 200_000
DIFF_LIMIT = 6000

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
Overlays put extra information on top of the board without changing it. You write how the board
is viewed; Plovane then applies it to whatever anyone changes or runs, with no AI. In order:
1. a premade overlay (see /overlay list): switch it on for the person with /overlay on <id> --person;
2. a template filled in for this project: /overlay from <template> --id <id> --set '{"blank": value}'
   (templates: {templates});
3. your own if/else rules, which Plovane checks on every draw:
  /overlay rule failing_calls --data '{"label": "Failing API calls", "rules": [
    {"on": "arrow", "if": {"type": "api", "live.ok": false}, "show": {"color": "red", "message": "failed ({live.status})"}},
    {"on": "arrow", "if": {"type": "api", "live.ok": true}, "show": {"color": "green"}}]}'
For each block or arrow the first rule whose "if" holds decides what shows; "if": {} is the
final else. A condition maps facts to a value or to one operator ({">": 0}): {operators}.
Combine with "all", "any", "not". Colors: {colors}. Messages may use {{fact}}.
  block facts: {block_facts}
  arrow facts: {arrow_facts}
Blocks whose meta has "pinned" were arranged by the person: never move, rename, resize, restyle
or remove those fields (the commands are refused). Work around them.
Call get_board before changing anything, and list_commands before guessing syntax. Keep the
board tidy: reuse blocks that exist, do not duplicate them, name things by what they do in
plain words. A destructive command answers needs_confirmation with a preview: only repeat it
with confirm=true when the change is what was asked. If a command fails, read the error and fix
it; do not repeat it unchanged. When done, reply with a short plain-English summary of what
you changed on the board."""

from backend.whiteboard.overlay_rules import ARROW_FACTS, BLOCK_FACTS, COLORS, OPERATORS  # noqa: E402

from backend.whiteboard.overlay_templates import TEMPLATES  # noqa: E402

SYSTEM = (SYSTEM.replace("{templates}", ", ".join(f"{k} ({', '.join(v['params']) or 'no blanks'})" for k, v in TEMPLATES.items()))
          .replace("{operators}", " ".join(OPERATORS)).replace("{colors}", " ".join(COLORS))
          .replace("{block_facts}", ", ".join(BLOCK_FACTS)).replace("{arrow_facts}", ", ".join(ARROW_FACTS))
          .replace("{{fact}}", "{fact}"))

ORGANISE = """The project's folders, files, import arrows and API routes were just mapped onto
the board from the code. Organise it so a newcomer understands the project at a glance.

What the project's own docs say (README, architecture):
{docs}

1. Read the board and the docs. Add a service block (with /sketch) for each major part of the
   product the docs describe (or, without docs, each major part of the code), named by what it
   does, and databases, queues or outside APIs the code clearly talks to.
2. Join them with arrows labelled in a few plain words ("stores orders", "charges cards").
3. Do not remove or rename what the import made.
4. Make what matters visible: switch on premade overlays that have something to show (for the
   person, with --person); mark each part the docs name with /overlay from area (its folder as
   the path); fill in other templates that fit this project. Use at most 20 commands.
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
    who = {USER: "the person", WATCHER: "the project watcher (files changed outside the whiteboard)",
           BOARD_AI: "Plovane"}.get(requester, f"the AI agent {requester!r}")
    result = llm_agent.run(f"Request from {who}:\n{text}", provider=name, client=client, model=chosen_model,
                           max_steps=max_steps, on_step=on_step, stop=stop, system=SYSTEM)
    return {**result, "requester": requester}


DOC_NAMES = ("README", "ARCHITECTURE", "DESIGN", "OVERVIEW")
MAX_DOC = 4000
MAX_DOCS = 10_000


# Input: root (Path)
# Output: str
# Processing: The project's README and architecture docs (top level and docs/), trimmed for the prompt; "(none)" when there are none.
# Changed by: Claude Code
# Date: 2026-11-25
def project_docs(root: Path) -> str:
    found: list[Path] = []
    for folder in (root, root / "docs", root / "doc"):
        if not folder.is_dir():
            continue
        for path in sorted(folder.iterdir()):
            stem = path.stem.upper()
            if path.is_file() and path.suffix.lower() in (".md", ".markdown", ".rst", ".txt") and (
                    any(stem.startswith(n) for n in DOC_NAMES) or (folder != root and len(found) < 6)):
                found.append(path)
    parts, total = [], 0
    for path in found:
        try:
            text = path.read_text(encoding="utf-8", errors="replace").strip()
        except OSError:
            continue
        chunk = f"--- {path.relative_to(root).as_posix()} ---\n{text[:MAX_DOC]}"
        if total + len(chunk) > MAX_DOCS:
            break
        parts.append(chunk)
        total += len(chunk)
    return "\n\n".join(parts) or "(none)"


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
        out["organise"] = ask(ws, ORGANISE.replace("{docs}", project_docs(ws.root)), requester=BOARD_AI, lock=lock,
                              provider=provider, model=model, on_step=on_step, max_steps=MAX_BUILD_STEPS)
    except llm_agent.AgentError as exc:  # the import still stands; say why organising did not happen
        out["note"] = f"organising failed: {exc}"
    return out


OVERLAY_TASK = """New kinds of information appeared in the project:
{kinds}
Overlays on the board now: {overlays}

Decide whether the person needs a new overlay to see this. If a premade overlay already shows it,
switch that on for the person (/overlay on <id> --person) when it is off. Otherwise fill in a
template (/overlay from), or write your own with /overlay rule. If nothing needs to change, reply
"No change needed." Use at most 4 commands."""


# Input: ws (Workspace)
# Output: set[str]
# Processing: Which kinds of signal the project has right now (keys of SIGNALS), so the watcher can spot new ones.
# Changed by: Claude Code
# Date: 2026-11-25
def signal_kinds(ws: Any) -> set[str]:
    from backend.whiteboard.ops import live
    from backend.whiteboard.overlay_rules import saved

    state = live.snapshot(ws)
    api = (state.get("api") or {}).values()
    kinds = set()
    if any(entry.get("count") for entry in api):
        kinds.add("api_traffic")
    if any(entry.get("fails") or entry.get("ok") is False for entry in api):
        kinds.add("api_failures")
    if state.get("security"):
        kinds.add("security_events")
    if saved(ws, "log_errors.json").get("files"):
        kinds.add("log_errors")
    if saved(ws, "trace.json").get("files"):
        kinds.add("trace")
    if any(b.kind == "external" for b in ws.board.blocks.values()):
        kinds.add("outside_apis")
    return kinds


# Input: root (Path)
# Output: dict[str, tuple[int, int]]
# Processing: Every source file in the project with its modification time and size.
# Changed by: Claude Code
# Date: 2026-11-25
def source_stamps(root: Path) -> dict[str, tuple[int, int]]:
    out: dict[str, tuple[int, int]] = {}
    for folder, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS and not d.startswith(".")]
        for name in files:
            full = Path(folder) / name
            rel = full.relative_to(root).as_posix()
            if not codegen.language_of(rel):
                continue
            try:
                stat = full.stat()
            except OSError:
                continue
            out[rel] = (stat.st_mtime_ns, stat.st_size)
    return out


# Input: root (Path), rel (str)
# Output: str | None
# Processing: A source file's text for diffs, or None when it is gone or too big.
# Changed by: Claude Code
# Date: 2026-11-25
def read_text(root: Path, rel: str) -> str | None:
    try:
        path = root / rel
        return path.read_text(encoding="utf-8", errors="replace") if path.stat().st_size <= MAX_TEXT else None
    except OSError:
        return None


class ProjectWatcher:
    """Notices files changed outside the whiteboard and brings the board up to date from the code."""

    # Input: ws (Workspace), lock (threading.RLock), publish (Callable | None), quiet (float)
    # Output: None
    # Processing: Take a first picture of the project's source files and listen for the board's own file writes.
    # Changed by: Claude Code
    # Date: 2026-11-25
    def __init__(self, ws: Any, lock: Any, *, publish: Callable[[dict[str, Any]], None] | None = None,
                 quiet: float = QUIET_SECONDS) -> None:
        self.ws, self.lock, self.quiet = ws, lock, quiet
        self.publish = publish or (lambda _event: None)
        self.stamps = source_stamps(ws.root)
        self.texts = {rel: read_text(ws.root, rel) for rel in self.stamps}
        self.pending: set[str] = set()
        self.last_change = 0.0
        self.own: dict[str, tuple[int, int] | None] = {}
        self.kinds = signal_kinds(ws)
        self.stopped = threading.Event()
        ws.listeners.append(self.on_event)

    # Input: event (dict[str, Any])
    # Output: None
    # Processing: Remember files a whiteboard operation just wrote, so the watcher does not treat them as outside edits.
    # Changed by: Claude Code
    # Date: 2026-11-25
    def on_event(self, event: dict[str, Any]) -> None:
        if event.get("kind") != "change":
            return
        for rel in (event.get("changes") or {}).get("files") or []:
            self.own[rel] = self.stamp(rel)

    # Input: rel (str)
    # Output: tuple[int, int] | None
    # Processing: One file's modification time and size, or None when it is gone.
    # Changed by: Claude Code
    # Date: 2026-11-25
    def stamp(self, rel: str) -> tuple[int, int] | None:
        try:
            stat = (self.ws.root / rel).stat()
        except OSError:
            return None
        return (stat.st_mtime_ns, stat.st_size)

    # Input: rel (str), now_stamp (tuple | None)
    # Output: bool
    # Processing: Whether a file's current state is one the whiteboard wrote itself; forgets the note once matched.
    # Changed by: Claude Code
    # Date: 2026-11-25
    def is_own(self, rel: str, now_stamp: tuple[int, int] | None) -> bool:
        if rel in self.own and self.own[rel] == now_stamp:
            self.own.pop(rel)
            return True
        return False

    # Input: now (float | None)
    # Output: dict[str, Any] | None
    # Processing: Look for changed source files (update the board once edits are quiet) and new kinds of signal (ask the whiteboard AI about overlays); return what happened.
    # Changed by: Claude Code
    # Date: 2026-11-25
    def poll(self, now: float | None = None) -> dict[str, Any] | None:
        now = time.monotonic() if now is None else now
        current = source_stamps(self.ws.root)
        changed = {rel for rel in set(current) | set(self.stamps) if current.get(rel) != self.stamps.get(rel)}
        for rel in changed:
            if self.is_own(rel, current.get(rel)):
                self.texts[rel] = read_text(self.ws.root, rel)
            else:
                self.pending.add(rel)
                self.last_change = now
        self.stamps = current
        out: dict[str, Any] | None = None
        if self.pending and now - self.last_change >= self.quiet:
            paths, self.pending = sorted(self.pending), set()
            out = self.update(paths)
        kinds = signal_kinds(self.ws)
        fresh, self.kinds = sorted(kinds - self.kinds), kinds
        if fresh:
            out = {**(out or {}), "overlays": self.consider(fresh)}
        return out

    # Input: fresh (list[str])
    # Output: dict[str, Any]
    # Processing: Ask the whiteboard AI whether new kinds of signal need an overlay to be visible; it adds one or does nothing.
    # Changed by: Claude Code
    # Date: 2026-11-25
    def consider(self, fresh: list[str]) -> dict[str, Any]:
        self.publish({"kind": "agent", "actor": BOARD_AI, "type": "signals", "new": fresh})
        name, _ = provider_and_model()
        if not llm_agent.api_key(name):
            return {"new": fresh, "note": f"overlay check skipped: no API key for {name}"}
        from backend.whiteboard.overlays import active

        on = set(active(self.ws.board, USER))
        overlays = ", ".join(f"{oid} ({spec.get('label', oid)}, {'on' if oid in on else 'off'})"
                             for oid, spec in self.ws.board.overlays.items() if spec.get("source") != "group")
        message = OVERLAY_TASK.format(kinds="\n".join(f"- {SIGNALS[k]}" for k in fresh), overlays=overlays)
        try:
            result = ask(self.ws, message, requester=WATCHER, lock=self.lock, max_steps=MAX_OVERLAY_STEPS,
                         on_step=lambda e: self.publish({"kind": "agent", "actor": BOARD_AI, "requester": WATCHER, **e}))
        except llm_agent.AgentError as exc:
            return {"new": fresh, "note": f"overlay check failed: {exc}"}
        return {"new": fresh, **result}

    # Input: paths (list[str])
    # Output: dict[str, Any]
    # Processing: Refresh the board from the code for these files (no AI) and report what changed, with a short diff.
    # Changed by: Claude Code
    # Date: 2026-11-25
    def update(self, paths: list[str]) -> dict[str, Any]:
        root = self.ws.root
        added = [p for p in paths if self.texts.get(p) is None and (root / p).is_file()]
        deleted = [p for p in paths if not (root / p).is_file()]
        edited = [p for p in paths if p not in added and p not in deleted]
        diff = self.diff(paths)
        with self.lock:
            ensure_board_ai(self.ws)
            reply = self.ws.execute("project.refresh", {"paths": paths}, actor=BOARD_AI)
        for rel in paths:
            self.texts[rel] = read_text(root, rel)
        changes = (reply.get("data") or {}).get("changes") or {}
        structural = bool(added or deleted) or any(
            (changes.get(key) or {}).get("added") or (changes.get(key) or {}).get("removed")
            for key in ("blocks", "arrows", "endpoints"))
        self.publish({"kind": "agent", "actor": BOARD_AI, "type": "watch", "files": paths, "ok": reply["ok"]})
        return {"added": added, "edited": edited, "deleted": deleted, "structural": structural,
                "diff": diff, "refresh": reply}

    # Input: paths (list[str])
    # Output: str
    # Processing: A short unified diff of the changed files against what the watcher last saw, capped for the prompt.
    # Changed by: Claude Code
    # Date: 2026-11-25
    def diff(self, paths: list[str]) -> str:
        parts: list[str] = []
        for rel in paths:
            old = (self.texts.get(rel) or "").splitlines()
            new = (read_text(self.ws.root, rel) or "").splitlines()
            parts.extend(difflib.unified_diff(old, new, f"a/{rel}", f"b/{rel}", n=1, lineterm=""))
        text = "\n".join(parts)
        return text if len(text) <= DIFF_LIMIT else text[:DIFF_LIMIT] + "\n... (diff cut short)"

    # Input: interval (float)
    # Output: threading.Thread
    # Processing: Poll in the background until stopped; a failed update is reported live and watching goes on.
    # Changed by: Claude Code
    # Date: 2026-11-25
    def start(self, interval: float = WATCH_SECONDS) -> threading.Thread:
        # Input: none
        # Output: None
        # Processing: The polling loop.
        # Changed by: Claude Code
        # Date: 2026-11-25
        def loop() -> None:
            while not self.stopped.wait(interval):
                try:
                    self.poll()
                except Exception as exc:  # noqa: BLE001  (keep watching; tell the person)
                    self.publish({"kind": "agent", "actor": BOARD_AI, "type": "watch_error", "error": str(exc)})

        thread = threading.Thread(target=loop, name="plovane-project-watcher", daemon=True)
        thread.start()
        return thread


# Input: ws (Workspace), lock (threading.RLock), publish (Callable | None)
# Output: ProjectWatcher | None
# Processing: Start watching the project for outside edits, unless PLOVANE_BOARD_AI_WATCH is 0.
# Changed by: Claude Code
# Date: 2026-11-25
def start_project_watcher(ws: Any, lock: Any, publish: Callable[[dict[str, Any]], None] | None = None) -> ProjectWatcher | None:
    if os.environ.get("PLOVANE_BOARD_AI_WATCH", "1").strip().lower() in ("0", "off", "false", "no"):
        return None
    watcher = ProjectWatcher(ws, lock, publish=publish)
    watcher.start()
    return watcher
