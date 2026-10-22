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
import inspect
import os
import subprocess
from pathlib import Path
from typing import Any

from whiteboard.model import DEFAULT_SIZE, LAYER_FOR_KIND, Block, Board
from whiteboard.registry import OPERATIONS, OpError, OpSpec
from whiteboard.store import FileJournal, Store, apply_changes, now

USER = "user"
MODES = {"none": 0, "read": 1, "read-write": 2}


# Input: before (dict[str, Any]), after (dict[str, Any]), files (dict[str, Any])
# Output: dict[str, Any]
# Processing: What an operation changed: blocks, arrows, endpoints and files.
# Changed by: Sourish
# Date: 2026-10-22
def summarise(before: dict[str, Any], after: dict[str, Any], files: dict[str, Any]) -> dict[str, Any]:
    summary: dict[str, Any] = {}
    for key in ("blocks", "arrows", "endpoints"):
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
        from whiteboard.model import BLOCK_KINDS

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


class Workspace:
    """One project's whiteboard: its board, history and files."""

    # Input: root (Path | str), library (Any), llm (Any)
    # Output: None
    # Processing: Open (or start) the whiteboard for a project folder.
    # Changed by: Sourish
    # Date: 2026-10-22
    def __init__(self, root: Path | str, *, library: Any = None, llm: Any = None) -> None:
        """``library`` is a callable returning the model engine, and ``llm`` a
        callable ``(prompt, system) -> str``. Both default to the real ones
        and are injected by tests."""
        import whiteboard.ops  # noqa: F401  (registers every operation)

        self.root = Path(root).resolve()
        if not self.root.is_dir():
            raise OpError("NOT_FOUND", f"project folder {root} does not exist")
        self.store = Store(self.root)
        self.board = self.store.load_board()
        self._library = library
        self._llm = llm
        self.processes: dict[str, subprocess.Popen] = {}

    # Input: none
    # Output: Any
    # Processing: The model engine, loaded on first use.
    # Changed by: Sourish
    # Date: 2026-10-22
    def library(self) -> Any:
        if self._library is None:
            from ml.serving.engine import get_engine

            self._library = get_engine
        try:
            return self._library()
        except RuntimeError as exc:
            raise OpError("MODEL_UNAVAILABLE", str(exc)) from None

    # Input: prompt (str), system (str | None)
    # Output: str
    # Processing: Ask the configured LLM; Claude by default.
    # Changed by: Sourish
    # Date: 2026-10-22
    def llm(self, prompt: str, system: str | None = None) -> str:
        if self._llm is not None:
            return self._llm(prompt, system)
        from ml.serving import llm

        reply = llm.ask(prompt, provider=os.environ.get("PLOVANE_LLM_PROVIDER", "anthropic"),
                        system=system, max_tokens=4096)
        if "error" in reply:
            raise OpError("LLM_UNAVAILABLE", reply["error"])
        return reply["text"]

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
    # Processing: Run one operation and answer in the §4.1 envelope.
    # Changed by: Sourish
    # Date: 2026-10-22
    def execute(
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
        from whiteboard.commands import parse

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
        except OpError:
            fs.rollback()
            self.board = Board.from_dict(before)
            raise
        except Exception as exc:  # a bug must not leave half a change behind
            fs.rollback()
            self.board = Board.from_dict(before)
            raise OpError("INTERNAL", f"{spec.command} failed: {type(exc).__name__}: {exc}") from exc
        changes = fs.changes()
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
            self.store.save_board(self.board)
        self._log(spec.name, args, actor, ok=True, detail=summary)
        return out

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
    # Processing: Undo or redo the newest entry, refusing if files changed since.
    # Changed by: Sourish
    # Date: 2026-10-22
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
        self.store.save_board(self.board)
        self._log(f"history.{direction}", {}, actor, ok=True, detail=entry["op"])
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
