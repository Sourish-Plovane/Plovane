"""Runs whiteboard operations. One code path for people, agents, HTTP and MCP.

Every operation runs inside a transaction: the board is copied and every file
change is journalled. If the handler fails, both are rolled back, so a
half-applied command never exists. On success the change is pushed on the
undo stack and logged. The same mechanism gives the design rules their
features:

- **Preview**: ``dry_run=True`` runs the operation, reports what would change,
  then rolls it back.
- **Confirmation**: an operation marked ``confirm`` answers
  ``needs_confirmation`` with that preview until it is run with ``confirm=True``.
- **Batch**: ``/batch`` runs its commands in one transaction, so all succeed
  or none do.
- **Undo and logs**: every change is reversible and attributed to its actor.
- **Same tools for all**: an AI agent's operations are checked against the
  paths and canvas regions it was granted.
"""

from __future__ import annotations

import base64
import copy
import difflib
import inspect
import os
import subprocess
from pathlib import Path
from dataclasses import asdict
from typing import Any, Callable

from backend.whiteboard.model import DEFAULT_SIZE, LAYER_FOR_KIND, Block, Board
from backend.whiteboard.registry import OPERATIONS, OpError, OpSpec
from backend.whiteboard.store import BOARD_EXTENSION, MAIN_BOARD, SKIP_DIRS, FileJournal, Store, apply_changes, now

USER = "user"
MODES = {"none": 0, "read": 1, "read-write": 2}


# Input: before (dict[str, Any]), after (dict[str, Any]), files (dict[str, Any])
# Output: dict[str, Any]
# Processing: What an operation changed: blocks, arrows, endpoints, agents and files.
# Changed by: Claude Code
# Date: 2026-11-14
def summarise(before: dict[str, Any], after: dict[str, Any], files: dict[str, Any]) -> dict[str, Any]:
    summary: dict[str, Any] = {}
    for key in ("blocks", "arrows", "endpoints", "agents"):  # agents: grants show live and undo (#71)
        old, new = before.get(key, {}), after.get(key, {})
        added = sorted(set(new) - set(old))
        removed = sorted(set(old) - set(new))
        changed = sorted(k for k in set(old) & set(new) if old[k] != new[k])
        if added or removed or changed:
            summary[key] = {"added": added, "removed": removed, "changed": changed}
    if files["files"] or files["dirs_created"] or files["dirs_removed"]:
        summary["files"] = sorted(files["files"])
        if files["dirs_created"]:
            summary["folders_created"] = files["dirs_created"]
        if files["dirs_removed"]:
            summary["folders_removed"] = files["dirs_removed"]
    return summary


class Context:
    """What a handler sees: the board, journalled files, and its caller."""

    # Input: workspace (Workspace), fs (FileJournal), actor (str), dry_run (bool), confirmed (bool)
    # Output: None
    # Processing: Bind a handler's view of one transaction.
    # Changed by: Sourish
    # Date: 2026-10-22
    def __init__(self, workspace: Workspace, fs: FileJournal, actor: str, dry_run: bool, confirmed: bool) -> None:
        self.ws = workspace
        self.fs = fs
        self.actor = actor
        self.dry_run = dry_run
        self.confirmed = confirmed
        self.notes: list[str] = []

    # Input: none
    # Output: Board
    # Processing: The board being changed.
    # Changed by: Sourish
    # Date: 2026-10-22
    @property
    def board(self) -> Board:
        return self.ws.board

    # Input: none
    # Output: Path
    # Processing: The project root.
    # Changed by: Sourish
    # Date: 2026-10-22
    @property
    def root(self) -> Path:
        return self.ws.root

    # -- Permissions -------------------------------------------------------------

    # Input: target (Block | str | None)
    # Output: str | None
    # Processing: The project path a block or path argument stands for.
    # Changed by: Sourish
    # Date: 2026-10-22
    def path_of(self, target: Block | str | None) -> str | None:
        if target is None:
            return None
        if isinstance(target, str):
            return target.strip("/")
        if target.path:
            return target.path.strip("/")
        return target.meta.get("file")

    # Input: target (Block | str | None)
    # Output: int
    # Processing: The access level the current agent has to a target.
    # Changed by: Sourish
    # Date: 2026-10-22
    def access(self, target: Block | str | None) -> int:
        """Path grants: the most specific grant wins, so ``none`` on a subfolder
        carves it out of a wider grant. Region grants add access to blocks
        drawn inside the granted rectangle on their layer."""
        agent = self.board.agents.get(self.actor)
        if agent is None:
            return 0
        level, best = 0, -1
        path = self.path_of(target)
        for grant in agent.grants:
            prefix = grant["path"].strip("/")
            covers = prefix in ("", ".") if path is None else (
                prefix in ("", ".") or path == prefix or path.startswith(prefix + "/"))
            if covers and len(prefix) > best:
                level, best = MODES[grant["mode"]], len(prefix)
        if isinstance(target, Block):
            x, y = self.board.absolute(target)
            for region in agent.regions:
                x1, y1, x2, y2 = region["area"]
                same_layer = region.get("layer") in (None, target.layer)
                inside = x1 <= x and y1 <= y and x + target.w <= x2 and y + target.h <= y2
                if same_layer and inside:
                    level = max(level, MODES[region["mode"]])
        return level

    # Input: target (Block | str | None), mode (str)
    # Output: None
    # Processing: Refuse unless the actor may read or change the target.
    # Changed by: Sourish
    # Date: 2026-10-22
    def require(self, target: Block | str | None, mode: str = "read-write") -> None:
        """``target`` None means the whole board (layers, presets, theme)."""
        if self.actor == USER:
            return
        if self.access(target) < MODES[mode]:
            name = "the whole board" if target is None else (
                target.id if isinstance(target, Block) else target)
            raise OpError("FORBIDDEN", f"agent {self.actor!r} has no {mode} access to {name}")

    # -- Helpers shared by operations ---------------------------------------------

    # Input: value (float)
    # Output: float
    # Processing: Snap a coordinate to the grid when snapping is on.
    # Changed by: Sourish
    # Date: 2026-10-22
    def snap(self, value: float) -> float:
        grid = self.board.view.get("grid", {})
        if grid.get("snap", True) and grid.get("size", 20):
            size = grid["size"]
            return round(value / size) * size
        return value

    # Input: name (str)
    # Output: int
    # Processing: The next number from a named counter kept on the board.
    # Changed by: Sourish
    # Date: 2026-10-22
    def counter(self, name: str) -> int:
        counters = self.board.view.setdefault("counters", {})
        counters[name] = counters.get(name, 0) + 1
        return counters[name]

    # Input: block (Block)
    # Output: None
    # Processing: Put a new block to the right of its last sibling, wrapping rows.
    # Changed by: Sourish
    # Date: 2026-10-22
    def auto_place(self, block: Block) -> None:
        siblings = [b for b in self.board.blocks.values()
                    if b.id != block.id and b.layer == block.layer and b.parent == block.parent]
        if not siblings:
            block.x, block.y = (20, 40) if block.parent else (40, 40)
            return
        last = max(siblings, key=lambda b: (b.y, b.x))
        x = last.x + last.w + 40
        if x > 1600:
            block.x, block.y = 40 if not block.parent else 20, max(b.y + b.h for b in siblings) + 40
        else:
            block.x, block.y = x, last.y
        block.x, block.y = self.snap(block.x), self.snap(block.y)

    # Input: kind (str), name (str), layer (int | None), parent (str | None), path (str | None), symbol (str | None), meta (dict | None), status (str), id_hint (str | None)
    # Output: Block
    # Processing: Create a block with its kind's defaults and place it.
    # Changed by: Sourish
    # Date: 2026-10-22
    def new_block(
        self, kind: str, name: str, *, layer: int | None = None, parent: str | None = None,
        path: str | None = None, symbol: str | None = None, meta: dict | None = None,
        status: str = "built", id_hint: str | None = None,
    ) -> Block:
        from backend.whiteboard.model import BLOCK_KINDS

        if kind not in BLOCK_KINDS:
            raise OpError("INVALID_ARGS", f"unknown block kind {kind!r}; kinds: {sorted(BLOCK_KINDS)}")
        if layer is None:
            layer = self.board.blocks[parent].layer if parent and kind in ("function", "step", "slot") \
                else LAYER_FOR_KIND.get(kind, self.board.view.get("layer", 1))
        if layer not in self.board.layers:
            raise OpError("NOT_FOUND", f"no layer {layer}; /layer list shows them")
        if parent is not None and parent not in self.board.blocks:
            raise OpError("NOT_FOUND", f"no parent block {parent!r}")
        w, h = DEFAULT_SIZE.get(kind, (160, 80))
        block = Block(
            id=self.board.new_id(id_hint or name), kind=kind, name=name, layer=layer, w=w, h=h,
            parent=parent, path=path, symbol=symbol, meta=dict(meta or {}), status=status,
            preset=kind if kind in self.board.presets else None,
        )
        self.board.blocks[block.id] = block
        self.auto_place(block)
        return block

    # Input: none
    # Output: Any
    # Processing: The model engine, for library operations.
    # Changed by: Sourish
    # Date: 2026-10-22
    def library(self) -> Any:
        return self.ws.library()

    # Input: prompt (str), system (str | None)
    # Output: str
    # Processing: Text from the configured LLM, or LLM_UNAVAILABLE.
    # Changed by: Sourish
    # Date: 2026-10-22
    def llm(self, prompt: str, system: str | None = None) -> str:
        return self.ws.llm(prompt, system)

    # Input: args (list[str]), cwd (Path | None), timeout (int)
    # Output: subprocess.CompletedProcess
    # Processing: Run a command in the project without a shell.
    # Changed by: Sourish
    # Date: 2026-10-22
    def run(self, args: list[str], *, cwd: Path | None = None, timeout: int = 120) -> subprocess.CompletedProcess:
        try:
            return subprocess.run(args, cwd=cwd or self.root, capture_output=True, text=True, timeout=timeout)
        except FileNotFoundError:
            raise OpError("UNAVAILABLE", f"{args[0]} is not installed") from None
        except subprocess.TimeoutExpired:
            raise OpError("TIMEOUT", f"{' '.join(args[:3])} took longer than {timeout}s") from None

    # Input: op (str), args (dict[str, Any])
    # Output: dict[str, Any]
    # Processing: Run another operation inside this transaction (batch, templates).
    # Changed by: Sourish
    # Date: 2026-10-22
    def execute(self, op: str, args: dict[str, Any]) -> dict[str, Any]:
        spec = self.ws.spec(op)
        self.ws.check_actor(spec, self.actor)
        return call(spec, self, args)


# Input: spec (OpSpec), ctx (Context), args (dict[str, Any])
# Output: dict[str, Any]
# Processing: Call a handler, turning bad arguments into INVALID_ARGS.
# Changed by: Sourish
# Date: 2026-10-22
def call(spec: OpSpec, ctx: Context, args: dict[str, Any]) -> dict[str, Any]:
    signature = inspect.signature(spec.handler)
    clean = {k.replace("-", "_"): v for k, v in args.items()}
    try:
        signature.bind(ctx, **clean)
    except TypeError as exc:
        params = [p for p in signature.parameters if p != "ctx"]
        raise OpError("INVALID_ARGS", f"{spec.command}: {exc}; parameters: {params}") from None
    return spec.handler(ctx, **clean) or {}


# Input: root (Path), name (str)
# Output: str
# Processing: A whiteboard file name as a project-relative .plo path, refusing anything outside the project.
# Changed by: Claude Code
# Date: 2026-11-15
def board_name(root: Path, name: str | None) -> str:
    cleaned = str(name or MAIN_BOARD).strip().replace("\\", "/").lstrip("/")
    if not cleaned.endswith(BOARD_EXTENSION):
        raise OpError("INVALID_ARGS", f"a whiteboard is a {BOARD_EXTENSION} file, got {name!r}")
    target = (root / cleaned).resolve()
    if not target.is_relative_to(root):
        raise OpError("INVALID_ARGS", f"{name!r} is outside the project")
    rel = target.relative_to(root).as_posix()
    if any(part in SKIP_DIRS for part in rel.split("/")[:-1]):
        raise OpError("INVALID_ARGS", f"{name!r} is inside a folder whiteboards may not live in")
    return rel


class Workspace:
    """One project's whiteboard: its board, history and files."""

    # Input: root (Path | str), library (Any), llm (Any), board (str), main (Workspace | None)
    # Output: None
    # Processing: Open (or start) one whiteboard (.plo file) of a project folder.
    # Changed by: Claude Code
    # Date: 2026-11-15
    def __init__(self, root: Path | str, *, library: Any = None, llm: Any = None, board: str = MAIN_BOARD,
                 main: Workspace | None = None) -> None:
        """``library`` is a callable returning the model engine, and ``llm`` a
        callable ``(prompt, system) -> str``. Both default to the real ones
        and are injected by tests. ``board`` is the whiteboard file; any but
        whiteboard.plo shares the agents of ``main`` (opened when not given)."""
        import backend.whiteboard.ops  # noqa: F401  (registers every operation)

        self.root = Path(root).resolve()
        if not self.root.is_dir():
            raise OpError("NOT_FOUND", f"project folder {root} does not exist")
        self.board_file = board_name(self.root, board)
        self._main = None if self.board_file == MAIN_BOARD else main
        self._library = library
        self._llm = llm
        self.store = Store(self.root, self.board_file)
        self.plo_error: str | None = None
        """Why the .plo could not be read; until fixed the board shows its last good state and the file is left alone."""
        self.board = Board()
        self.board = self.load()
        self.prompt_context: dict[str, Any] | None = None
        self.processes: dict[str, subprocess.Popen] = {}
        self.listeners: list[Callable[[dict[str, Any]], None]] = []
        """Called with one event per change: the server turns them into live updates."""
        self.current_command: str | None = None
        """The slash command being run, as typed, so its event can show it."""
        self.live: dict[str, Any] | None = None
        """What is happening right now (agents working, API status, attacks); see ops/live.py. Main whiteboard only."""

    # Input: none
    # Output: bool
    # Processing: Whether this is the main whiteboard, which holds the project's agents.
    # Changed by: Claude Code
    # Date: 2026-11-15
    @property
    def is_main(self) -> bool:
        return self.board_file == MAIN_BOARD

    # Input: none
    # Output: Workspace
    # Processing: The main whiteboard: this one, or the one whose agents this one shares.
    # Changed by: Claude Code
    # Date: 2026-11-15
    @property
    def main(self) -> Workspace:
        if self.is_main:
            return self
        if self._main is None:
            self._main = Workspace(self.root, library=self._library, llm=self._llm)
        return self._main

    # Input: none
    # Output: None
    # Processing: On a whiteboard other than the main one, take the project's current agents from the main one.
    # Changed by: Claude Code
    # Date: 2026-11-15
    def refresh_agents(self) -> None:
        if self.is_main:
            return
        self.main.sync()
        if self.board.agents != self.main.board.agents:
            self.board.agents = copy.deepcopy(self.main.board.agents)

    # Input: none
    # Output: Board
    # Processing: The board from the .plo file (rebuilt when it differs from the cache), or the cache, or a new board.
    # Changed by: Claude Code
    # Date: 2026-11-15
    def load(self) -> Board:
        from backend.whiteboard.ops.boardfile import build_board, render

        cached = self.store.load_board()
        if not self.is_main:
            cached.agents = copy.deepcopy(self.main.board.agents)
        text = self.store.read_plo()
        self.plo_error = None
        if text is None:
            if self.is_main and self.store.board_path.exists():
                self.store.write_plo(render(cached, self.is_main))  # a project from before .plo files
            return cached
        if text == render(cached, self.is_main):
            return cached
        # The file changed under us (git pull, a teammate, a hand edit): rebuild the board from it.
        self.board = cached
        ctx = Context(self, FileJournal(self.root), USER, dry_run=False, confirmed=True)
        try:
            build_board(ctx, text, keep=cached)
        except OpError as exc:
            self.plo_error = (f"{self.board_file} could not be read, so the whiteboard shows its last good state "
                              f"and leaves the file alone until it is fixed: {exc.message}")
            return cached
        rebuilt = self.board
        self.store.save_board(rebuilt)  # the cache only: the file stays as it was written
        return rebuilt

    # Input: force (bool)
    # Output: None
    # Processing: Save the cache and rewrite the .plo file, unless the file is broken and this is not forced.
    # Changed by: Claude Code
    # Date: 2026-11-15
    def save(self, force: bool = False) -> None:
        from backend.whiteboard.ops.boardfile import render

        plo = None if self.plo_error and not force else render(self.board, self.is_main)
        self.store.save_board(self.board, plo)

    # Input: none
    # Output: bool
    # Processing: Reload when another process, Git or a person changed the cache or the .plo since this one last did.
    # Changed by: Claude Code
    # Date: 2026-11-15
    def sync(self) -> bool:
        if not self.store.board_changed():
            self.refresh_agents()
            return False
        self.board = self.load()
        self.emit({"kind": "external", "actor": None, "op": None, "command": None, "changes": {}})
        return True

    # Input: none
    # Output: dict[str, dict[str, str]]
    # Processing: For every block, the agents that may reach it and how ("read" or "read-write"), by the grant rules.
    # Changed by: Claude Code
    # Date: 2026-11-14
    def access_map(self) -> dict[str, dict[str, str]]:
        names = {level: mode for mode, level in MODES.items()}
        found: dict[str, dict[str, str]] = {}
        for agent in sorted(self.board.agents):
            ctx = Context(self, None, agent, dry_run=True, confirmed=False)  # access() reads the board only
            for block in self.board.blocks.values():
                level = ctx.access(block)
                if level:
                    found.setdefault(block.id, {})[agent] = names[level]
        return found

    # Input: event (dict[str, Any])
    # Output: None
    # Processing: Hand one change event to every listener; a failing listener never breaks the operation.
    # Changed by: Claude Code
    # Date: 2026-11-09
    def emit(self, event: dict[str, Any]) -> None:
        event = {"at": now(), "board": self.board_file, **event}
        for listener in list(self.listeners):
            try:
                listener(event)
            except Exception:  # noqa: BLE001  (a closed stream must not undo a change)
                pass

    # Input: none
    # Output: Any
    # Processing: The model engine, loaded on first use.
    # Changed by: Sourish
    # Date: 2026-10-22
    def library(self) -> Any:
        if not self.is_main:
            return self.main.library()  # one model engine per project, shared by every tab
        if self._library is None:
            from ml.serving.engine import get_engine

            self._library = get_engine
        try:
            return self._library()
        except RuntimeError as exc:
            raise OpError("MODEL_UNAVAILABLE", str(exc)) from None

    # Input: none
    # Output: list
    # Processing: The library's entries for browsing, without loading the models unless they already are.
    # Changed by: Claude Code
    # Date: 2026-11-15
    def library_entries(self) -> list:
        from ml.serving import engine as serving

        if self._library is not None and self._library is not serving.get_engine:
            return list(self.library().entries)  # an injected engine (tests, or a custom library)
        if serving._engine is not None:
            return list(serving._engine.entries)
        from ml.ingest.dataset import load_dataset

        if not serving.LIBRARY.exists():
            raise OpError("MODEL_UNAVAILABLE", "ml/data/entries.jsonl is missing. Run: python -m ml.serving.setup")
        stamp = serving.LIBRARY.stat().st_mtime_ns
        if getattr(self, "_entries_stamp", None) != stamp:
            self._entries = list(load_dataset(serving.LIBRARY).entries)
            self._entries_stamp = stamp
        return self._entries

    # Input: prompt (str), system (str | None)
    # Output: str
    # Processing: Ask the configured LLM; Claude by default.
    # Changed by: Sourish
    # Date: 2026-10-22
    def llm(self, prompt: str, system: str | None = None) -> str:
        reply = self.llm_result(prompt, system)
        if "error" in reply:
            raise OpError("LLM_UNAVAILABLE", reply["error"])
        return reply["text"]

    # Input: prompt (str), system (str | None), provider (str | None)
    # Output: dict[str, Any]
    # Processing: Ask Claude, DeepSeek or OpenAI and keep its token usage; a missing key says how to add one.
    # Changed by: Claude Code
    # Date: 2026-11-09
    def llm_result(self, prompt: str, system: str | None = None, *, provider: str | None = None) -> dict[str, Any]:
        if self._llm is not None:
            return {"text": self._llm(prompt, system), "usage": {}}
        from ml.serving import llm

        provider = provider or os.environ.get("PLOVANE_LLM_PROVIDER", "anthropic")
        reply = llm.ask(prompt, provider=provider, system=system, max_tokens=4096)
        if "error" in reply:
            if "is not set" in reply["error"]:
                name = {"anthropic": "claude"}.get(provider, provider)
                raise OpError("LLM_UNAVAILABLE", f"no API key for {name}: add one under AI providers "
                                                 f"(the A button, top right) or run 'plovane connect {name}'")
            raise OpError("LLM_UNAVAILABLE", reply["error"])
        return reply

    # Input: op (str)
    # Output: OpSpec
    # Processing: Look up an operation, or NOT_FOUND naming close ones.
    # Changed by: Sourish
    # Date: 2026-10-22
    def spec(self, op: str) -> OpSpec:
        if op not in OPERATIONS:
            close = [name for name in OPERATIONS if op.split(".")[0] in name][:8]
            raise OpError("UNKNOWN_OPERATION", f"no operation {op!r}; similar: {close}")
        return OPERATIONS[op]

    # Input: spec (OpSpec), actor (str)
    # Output: None
    # Processing: Refuse person-only operations and unknown agents.
    # Changed by: Sourish
    # Date: 2026-10-22
    def check_actor(self, spec: OpSpec, actor: str) -> None:
        if actor == USER:
            return
        if actor not in self.board.agents:
            raise OpError("FORBIDDEN", f"unknown agent {actor!r}; a person adds it with /agent add")
        if not spec.agents:
            raise OpError("FORBIDDEN", f"{spec.command} changes access and only a person may run it")

    # Input: op (str), args (dict | None), actor (str), confirm (bool), dry_run (bool)
    # Output: dict[str, Any]
    # Processing: Run one operation under the project lock, after picking up other processes' changes.
    # Changed by: Claude Code
    # Date: 2026-11-09
    def execute(
        self, op: str, args: dict[str, Any] | None = None, *, actor: str = USER,
        confirm: bool = False, dry_run: bool = False,
    ) -> dict[str, Any]:
        spec = OPERATIONS.get(op)
        if not self.is_main and op.startswith("agent.") and spec is not None and spec.mutates:
            # Agents belong to the project: changing them on any whiteboard changes the main one.
            reply = self.main.execute(op, args, actor=actor, confirm=confirm, dry_run=dry_run)
            with self.store.locked():
                self.refresh_agents()
            return reply
        with self.store.locked():
            self.sync()
            return self._execute(op, args, actor=actor, confirm=confirm, dry_run=dry_run)

    # Input: op (str), args (dict | None), actor (str), confirm (bool), dry_run (bool)
    # Output: dict[str, Any]
    # Processing: Run one operation with the project lock held and the board current.
    # Changed by: Claude Code
    # Date: 2026-11-09
    def _execute(
        self, op: str, args: dict[str, Any] | None = None, *, actor: str = USER,
        confirm: bool = False, dry_run: bool = False,
    ) -> dict[str, Any]:
        args = dict(args or {})
        try:
            spec = self.spec(op)
            self.check_actor(spec, actor)
            needs = spec.confirm(args) if callable(spec.confirm) else spec.confirm
            if needs and not confirm and not dry_run:
                preview = self._transaction(spec, args, actor, dry_run=True, confirmed=False)
                return envelope({
                    "status": "needs_confirmation",
                    "message": f"{spec.command} {spec.summary[0].lower()}{spec.summary[1:]} "
                               "Review the preview, then run it again with confirm.",
                    "preview": preview,
                })
            return envelope(self._transaction(spec, args, actor, dry_run=dry_run, confirmed=confirm))
        except OpError as exc:
            self._log(op, args, actor, ok=False, detail=exc.message)
            return {"ok": False, "data": None, "error": {"code": exc.code, "message": exc.message}}

    # Input: text (str), actor (str), confirm (bool), dry_run (bool)
    # Output: dict[str, Any]
    # Processing: Parse a slash command and run it.
    # Changed by: Sourish
    # Date: 2026-10-22
    def command(self, text: str, *, actor: str = USER, confirm: bool = False, dry_run: bool = False) -> dict[str, Any]:
        from backend.whiteboard.commands import parse

        try:
            op, args = parse(text)
        except OpError as exc:
            return {"ok": False, "data": None, "error": {"code": exc.code, "message": exc.message}}
        return self.execute(op, args, actor=actor, confirm=confirm, dry_run=dry_run)

    # Input: spec (OpSpec), args (dict), actor (str), dry_run (bool), confirmed (bool)
    # Output: dict[str, Any]
    # Processing: Run a handler atomically; record undo, log and save.
    # Changed by: Sourish
    # Date: 2026-10-22
    def _transaction(self, spec: OpSpec, args: dict[str, Any], actor: str, *, dry_run: bool, confirmed: bool) -> dict[str, Any]:
        before = self.board.to_dict()
        fs = FileJournal(self.root)
        ctx = Context(self, fs, actor, dry_run, confirmed)
        try:
            result = call(spec, ctx, args)
            if not self.is_main and {k: asdict(v) for k, v in self.board.agents.items()} != before["agents"]:
                raise OpError("FORBIDDEN", f"agents belong to the whole project; change them on {MAIN_BOARD} "
                                           "or with /agent commands outside a batch")
        except OpError:
            fs.rollback()
            self.board = Board.from_dict(before)
            raise
        except Exception as exc:  # a bug must not leave half a change behind
            fs.rollback()
            self.board = Board.from_dict(before)
            raise OpError("INTERNAL", f"{spec.command} failed: {type(exc).__name__}: {exc}") from exc
        changes = fs.changes()
        prompt_context = self.prompt_context
        if prompt_context:
            changed_paths = set(changes.get("files", {}))
            usage = prompt_context.get("tokens", {})
            token_total = sum(int(value or 0) for value in usage.values())
            for block in self.board.blocks.values():
                block_path = (block.path or block.meta.get("file") or "").strip("/")
                if block.id not in before.get("blocks", {}) or block_path in changed_paths:
                    if not block.meta.get("library"):
                        block.status = "generated"
                    block.meta["prompt"] = prompt_context["prompt"]
                    block.meta["tokens"] = token_total
                    block.meta["token_usage"] = usage
            provenance = self.board.view.setdefault("line_provenance", {})
            for path, (encoded_before, encoded_after) in changes.get("files", {}).items():
                old_lines = (base64.b64decode(encoded_before).decode(errors="replace").splitlines()
                             if encoded_before else [])
                new_lines = (base64.b64decode(encoded_after).decode(errors="replace").splitlines()
                             if encoded_after else [])
                generated_lines = []
                for tag, _, _, new_start, new_end in difflib.SequenceMatcher(
                        a=old_lines, b=new_lines, autojunk=False).get_opcodes():
                    if tag in ("insert", "replace"):
                        generated_lines.extend(range(new_start + 1, new_end + 1))
                if generated_lines:
                    line_map = provenance.setdefault(path, {})
                    for line in generated_lines:
                        line_map[str(line)] = {
                            "prompt": prompt_context["prompt"], "tokens": token_total,
                            "token_usage": usage,
                        }
        after = self.board.to_dict()
        summary = summarise(before, after, changes)
        out: dict[str, Any] = {"result": result, "changes": summary}
        if ctx.notes:
            out["notes"] = ctx.notes
        if dry_run:
            fs.rollback()
            self.board = Board.from_dict(before)
            out["dry_run"] = True
            return out
        if spec.mutates and summary:
            history = self.store.load_history()
            history["undo"].append({
                "op": spec.name, "actor": actor, "at": now(), "args": _short(args),
                "board_before": before, "board_after": after, "files": changes,
            })
            history["redo"] = []
            self.store.save_history(history)
        if after != before:
            self.save()
        self._log(spec.name, args, actor, ok=True, detail=summary)
        if summary and spec.name not in ("history.undo", "history.redo"):
            self.emit({"kind": "change", "actor": actor, "op": spec.name,
                       "command": self.current_command, "changes": summary})
        if actor != USER and summary:
            self._mark_work(actor, spec.name, summary)
        return out

    # Input: actor (str), op (str), summary (dict[str, Any])
    # Output: None
    # Processing: Mark the files an agent's change touched as being worked on, for the live view.
    # Changed by: Claude Code
    # Date: 2026-11-18
    def _mark_work(self, actor: str, op: str, summary: dict[str, Any]) -> None:
        from backend.whiteboard.ops import live

        paths = list(summary.get("files", []))
        for key in ("added", "changed"):
            for block_id in summary.get("blocks", {}).get(key, []):
                block = self.board.blocks.get(block_id)
                if block is not None and (block.path or block.meta.get("file")):
                    paths.append(block.path or block.meta["file"])
        paths = [p for p in paths if not p.endswith(BOARD_EXTENSION)]
        if paths and live.touch(self, actor, paths, op):
            live.announce(self, {"what": "working", "agent": actor, "files": sorted(set(paths))[:50]})

    # Input: op (str), args (dict), actor (str), ok (bool), detail (Any)
    # Output: None
    # Processing: Append one line to the operation log.
    # Changed by: Sourish
    # Date: 2026-10-22
    def _log(self, op: str, args: dict[str, Any], actor: str, *, ok: bool, detail: Any) -> None:
        self.store.append_log({"at": now(), "actor": actor, "op": op, "args": _short(args), "ok": ok,
                               "detail": detail})

    # Input: direction (str), actor (str)
    # Output: dict[str, Any]
    # Processing: Undo or redo the newest entry, refusing if files changed since, and announce it.
    # Changed by: Claude Code
    # Date: 2026-11-09
    def step_history(self, direction: str, actor: str = USER) -> dict[str, Any]:
        history = self.store.load_history()
        source, target = ("undo", "redo") if direction == "undo" else ("redo", "undo")
        if not history[source]:
            raise OpError("NOTHING_TO_" + direction.upper(), f"nothing to {direction}")
        entry = history[source][-1]
        if actor != USER and entry["actor"] != actor:
            raise OpError("FORBIDDEN", f"agent {actor!r} may only {direction} its own changes")
        expected = 1 if direction == "undo" else 0
        drifted = []
        for rel, states in entry["files"]["files"].items():
            path = self.root / rel
            now_bytes = path.read_bytes() if path.is_file() else None
            want = None if states[expected] is None else base64.b64decode(states[expected])
            if now_bytes != want:
                drifted.append(rel)
        if drifted:
            raise OpError("CONFLICT", f"{drifted} changed since {entry['op']}; {direction} would "
                                      "discard those edits. Commit or revert them first.")
        apply_changes(self.root, entry["files"], direction=direction)
        self.board = Board.from_dict(entry["board_before" if direction == "undo" else "board_after"])
        history[source].pop()
        history[target].append(entry)
        self.store.save_history(history)
        self.refresh_agents()  # an old copy of the agents must not come back with the board
        self.save()
        self._log(f"history.{direction}", {}, actor, ok=True, detail=entry["op"])
        self.emit({"kind": "change", "actor": actor, "op": f"history.{direction}",
                   "command": f"/{direction}", "changes": {"undone" if direction == "undo" else "redone": entry["op"]}})
        return {"action": direction, "op": entry["op"], "at": entry["at"],
                "files": sorted(entry["files"]["files"])}


# Input: args (dict[str, Any])
# Output: dict[str, Any]
# Processing: Arguments with long strings shortened, for logs.
# Changed by: Sourish
# Date: 2026-10-22
def _short(args: dict[str, Any]) -> dict[str, Any]:
    return {k: (v[:200] + "…" if isinstance(v, str) and len(v) > 200 else v) for k, v in args.items()}


# Input: data (dict[str, Any])
# Output: dict[str, Any]
# Processing: Wrap a result in the §4.1 success envelope.
# Changed by: Sourish
# Date: 2026-10-22
def envelope(data: dict[str, Any]) -> dict[str, Any]:
    return {"ok": True, "data": data, "error": None}
