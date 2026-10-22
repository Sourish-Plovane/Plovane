"""Where the whiteboard keeps its state, and how file changes are recorded.

Inside the project, under ``.plovane/``:

    board.json        the board: layout, styles, layers, endpoints. Committed with the code.
    snapshots/        named copies of board.json (/snapshot). Committed.
    history.json      undo and redo stacks. Local, gitignored.
    log.jsonl         every operation run, by whom. Local, gitignored.
    compile/          code previews waiting for /compile apply. Local.
    monitor/          output of processes started by /monitor. Local.
    submissions.jsonl blocks queued for the library by /save. Local.

The code itself stays where it is: Git is the source of truth, and the board
only maps onto it.
"""

from __future__ import annotations

import base64
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from whiteboard.model import Board, default_board
from whiteboard.registry import OpError

STATE_DIR = ".plovane"
HISTORY_LIMIT = 50
IGNORED = ("history.json", "log.jsonl", "compile/", "monitor/", "submissions.jsonl")
PROTECTED = (".git", STATE_DIR)
"""Paths no operation may write to through the file journal."""


# Input: none
# Output: str
# Processing: The current UTC time as ISO 8601.
# Changed by: Sourish
# Date: 2026-10-22
def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Store:
    """Reads and writes ``.plovane/`` for one project."""

    # Input: root (Path)
    # Output: None
    # Processing: Locate the state folder for a project root.
    # Changed by: Sourish
    # Date: 2026-10-22
    def __init__(self, root: Path) -> None:
        self.root = root
        self.dir = root / STATE_DIR
        self.board_path = self.dir / "board.json"
        self.history_path = self.dir / "history.json"
        self.log_path = self.dir / "log.jsonl"
        self.snapshots = self.dir / "snapshots"
        self.monitor_dir = self.dir / "monitor"
        self.submissions = self.dir / "submissions.jsonl"

    # Input: none
    # Output: None
    # Processing: Create .plovane/ with its gitignore on first use.
    # Changed by: Sourish
    # Date: 2026-10-22
    def ensure(self) -> None:
        self.dir.mkdir(exist_ok=True)
        ignore = self.dir / ".gitignore"
        if not ignore.exists():
            ignore.write_text("# Local whiteboard state; board.json and snapshots/ are shared.\n"
                              + "\n".join(IGNORED) + "\n")

    # Input: none
    # Output: Board
    # Processing: The saved board, or a default one for a new project.
    # Changed by: Sourish
    # Date: 2026-10-22
    def load_board(self) -> Board:
        if self.board_path.exists():
            return Board.from_dict(json.loads(self.board_path.read_text(encoding="utf-8")))
        return default_board()

    # Input: board (Board)
    # Output: None
    # Processing: Write board.json, atomically.
    # Changed by: Sourish
    # Date: 2026-10-22
    def save_board(self, board: Board) -> None:
        self.ensure()
        temp = self.board_path.with_suffix(".tmp")
        temp.write_text(json.dumps(board.to_dict(), indent=1, sort_keys=True) + "\n", encoding="utf-8")
        temp.replace(self.board_path)

    # Input: none
    # Output: dict[str, list]
    # Processing: The undo and redo stacks.
    # Changed by: Sourish
    # Date: 2026-10-22
    def load_history(self) -> dict[str, list]:
        if self.history_path.exists():
            return json.loads(self.history_path.read_text(encoding="utf-8"))
        return {"undo": [], "redo": []}

    # Input: history (dict[str, list])
    # Output: None
    # Processing: Save the stacks, keeping the newest HISTORY_LIMIT entries.
    # Changed by: Sourish
    # Date: 2026-10-22
    def save_history(self, history: dict[str, list]) -> None:
        self.ensure()
        trimmed = {k: v[-HISTORY_LIMIT:] for k, v in history.items()}
        self.history_path.write_text(json.dumps(trimmed), encoding="utf-8")

    # Input: entry (dict[str, Any])
    # Output: None
    # Processing: Append one operation to the log.
    # Changed by: Sourish
    # Date: 2026-10-22
    def append_log(self, entry: dict[str, Any]) -> None:
        self.ensure()
        with self.log_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry, default=str) + "\n")

    # Input: actor (str | None), last (int | None)
    # Output: list[dict[str, Any]]
    # Processing: Logged operations, optionally by one actor, newest last.
    # Changed by: Sourish
    # Date: 2026-10-22
    def read_log(self, actor: str | None = None, last: int | None = None) -> list[dict[str, Any]]:
        if not self.log_path.exists():
            return []
        rows = [json.loads(line) for line in self.log_path.read_text(encoding="utf-8").splitlines() if line]
        if actor is not None:
            rows = [r for r in rows if r.get("actor") == actor]
        return rows[-last:] if last else rows


# Input: data (bytes | None)
# Output: str | None
# Processing: File bytes as base64 text for JSON.
# Changed by: Sourish
# Date: 2026-10-22
def _encode(data: bytes | None) -> str | None:
    return None if data is None else base64.b64encode(data).decode()


# Input: data (str | None)
# Output: bytes | None
# Processing: Base64 text back to file bytes.
# Changed by: Sourish
# Date: 2026-10-22
def _decode(data: str | None) -> bytes | None:
    return None if data is None else base64.b64decode(data)


class FileJournal:
    """Project file access that remembers every change, so it can be undone.

    Records each file's content before its first change. ``changes()`` pairs
    that with the content now, which is everything undo and redo need.
    """

    # Input: root (Path)
    # Output: None
    # Processing: Start an empty journal over a project root.
    # Changed by: Sourish
    # Date: 2026-10-22
    def __init__(self, root: Path) -> None:
        self.root = root.resolve()
        self._before: dict[str, bytes | None] = {}
        self._dirs_created: list[str] = []
        self._dirs_removed: list[str] = []

    # Input: rel (str), write (bool)
    # Output: Path
    # Processing: Resolve a project-relative path, refusing escapes and protected folders.
    # Changed by: Sourish
    # Date: 2026-10-22
    def path(self, rel: str, *, write: bool = False) -> Path:
        cleaned = str(rel).strip().replace("\\", "/").strip("/")
        target = (self.root / cleaned).resolve()
        if target != self.root and self.root not in target.parents:
            raise OpError("INVALID_PATH", f"{rel!r} is outside the project")
        first = cleaned.split("/", 1)[0]
        if write and first in PROTECTED:
            raise OpError("INVALID_PATH", f"{rel!r} is inside {first}/, which commands may not change")
        return target

    # Input: target (Path)
    # Output: str
    # Processing: The project-relative form of an absolute path.
    # Changed by: Sourish
    # Date: 2026-10-22
    def rel(self, target: Path) -> str:
        return target.resolve().relative_to(self.root).as_posix()

    # Input: rel (str)
    # Output: None
    # Processing: Remember a file's content before its first change.
    # Changed by: Sourish
    # Date: 2026-10-22
    def _remember(self, rel: str) -> None:
        key = self.rel(self.path(rel))
        if key not in self._before:
            target = self.root / key
            self._before[key] = target.read_bytes() if target.is_file() else None

    # Input: rel (str)
    # Output: bool
    # Processing: Whether a file or folder exists.
    # Changed by: Sourish
    # Date: 2026-10-22
    def exists(self, rel: str) -> bool:
        return self.path(rel).exists()

    # Input: rel (str)
    # Output: bool
    # Processing: Whether a folder exists.
    # Changed by: Sourish
    # Date: 2026-10-22
    def is_dir(self, rel: str) -> bool:
        return self.path(rel).is_dir()

    # Input: rel (str)
    # Output: str
    # Processing: A text file's content.
    # Changed by: Sourish
    # Date: 2026-10-22
    def read(self, rel: str) -> str:
        target = self.path(rel)
        if not target.is_file():
            raise OpError("NOT_FOUND", f"no file {rel!r}")
        return target.read_text(encoding="utf-8", errors="replace")

    # Input: rel (str), text (str)
    # Output: None
    # Processing: Write a text file, creating its folders, journalled.
    # Changed by: Sourish
    # Date: 2026-10-22
    def write(self, rel: str, text: str) -> None:
        target = self.path(rel, write=True)
        if target.is_dir():
            raise OpError("INVALID_PATH", f"{rel!r} is a folder")
        if target.parent != self.root:
            self.mkdir(self.rel(target.parent))
        self._remember(rel)
        target.write_text(text, encoding="utf-8")

    # Input: rel (str)
    # Output: None
    # Processing: Create a folder and its parents, journalled.
    # Changed by: Sourish
    # Date: 2026-10-22
    def mkdir(self, rel: str) -> None:
        target = self.path(rel, write=True)
        missing = []
        probe = target
        while probe != self.root and not probe.exists():
            missing.append(probe)
            probe = probe.parent
        for folder in reversed(missing):
            folder.mkdir()
            self._dirs_created.append(self.rel(folder))

    # Input: rel (str)
    # Output: None
    # Processing: Delete a file, or a folder and everything in it, journalled.
    # Changed by: Sourish
    # Date: 2026-10-22
    def remove(self, rel: str) -> None:
        target = self.path(rel, write=True)
        if target == self.root:
            raise OpError("INVALID_PATH", "refusing to delete the project root")
        if target.is_file():
            self._remember(rel)
            target.unlink()
            return
        if not target.is_dir():
            raise OpError("NOT_FOUND", f"no file or folder {rel!r}")
        for file in sorted(p for p in target.rglob("*") if p.is_file()):
            self._remember(self.rel(file))
        folders = sorted((p for p in target.rglob("*") if p.is_dir()), key=lambda p: -len(p.parts))
        self._dirs_removed.extend(self.rel(p) for p in [*folders, target])
        shutil.rmtree(target)

    # Input: source (str), destination (str)
    # Output: None
    # Processing: Move a file or folder, journalled as copy-then-delete.
    # Changed by: Sourish
    # Date: 2026-10-22
    def move(self, source: str, destination: str) -> None:
        src, dst = self.path(source, write=True), self.path(destination, write=True)
        if not src.exists():
            raise OpError("NOT_FOUND", f"no file or folder {source!r}")
        if dst.exists():
            raise OpError("CONFLICT", f"{destination!r} already exists")
        if src.is_file():
            self.write(destination, src.read_text(encoding="utf-8", errors="replace"))
        else:
            self.mkdir(destination)
            for file in sorted(p for p in src.rglob("*") if p.is_file()):
                inner = file.relative_to(src).as_posix()
                self._remember(f"{destination}/{inner}")
                (dst / inner).parent.mkdir(parents=True, exist_ok=True)
                (dst / inner).write_bytes(file.read_bytes())
        self.remove(source)

    # Input: rel (str)
    # Output: list[str]
    # Processing: Every file under a folder, project-relative.
    # Changed by: Sourish
    # Date: 2026-10-22
    def files_under(self, rel: str = "") -> list[str]:
        base = self.path(rel)
        skip = {".git", STATE_DIR, "node_modules", "__pycache__", ".venv", "venv", ".mypy_cache", ".pytest_cache"}
        found = []
        for file in sorted(base.rglob("*")):
            parts = file.relative_to(self.root).parts
            if file.is_file() and not skip.intersection(parts):
                found.append(file.relative_to(self.root).as_posix())
        return found

    # Input: none
    # Output: dict[str, Any]
    # Processing: Every recorded change as before/after content.
    # Changed by: Sourish
    # Date: 2026-10-22
    def changes(self) -> dict[str, Any]:
        files = {}
        for rel, before in self._before.items():
            target = self.root / rel
            after = target.read_bytes() if target.is_file() else None
            if before != after:
                files[rel] = [_encode(before), _encode(after)]
        return {
            "files": files,
            "dirs_created": [d for d in self._dirs_created if d not in self._dirs_removed],
            "dirs_removed": [d for d in self._dirs_removed if d not in self._dirs_created],
        }

    # Input: none
    # Output: None
    # Processing: Put every recorded file and folder back as it was.
    # Changed by: Sourish
    # Date: 2026-10-22
    def rollback(self) -> None:
        apply_changes(self.root, {
            "files": {k: [_encode(v), None] for k, v in self._before.items()},
            "dirs_created": list(self._dirs_created),
            "dirs_removed": list(self._dirs_removed),
        }, direction="undo")


# Input: root (Path), changes (dict[str, Any]), direction (str)
# Output: None
# Processing: Apply recorded changes backwards (undo) or forwards (redo).
# Changed by: Sourish
# Date: 2026-10-22
def apply_changes(root: Path, changes: dict[str, Any], *, direction: str) -> None:
    index = 0 if direction == "undo" else 1
    make = changes["dirs_removed"] if direction == "undo" else changes["dirs_created"]
    drop = changes["dirs_created"] if direction == "undo" else changes["dirs_removed"]
    for folder in sorted(make, key=len):
        (root / folder).mkdir(parents=True, exist_ok=True)
    for rel, states in changes["files"].items():
        target, content = root / rel, _decode(states[index])
        if content is None:
            if target.is_file():
                target.unlink()
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content)
    for folder in sorted(drop, key=len, reverse=True):
        path = root / folder
        if not path.is_dir():
            continue
        # Python's bytecode caches are regenerated on demand and must not keep
        # a folder alive that the undone change created.
        for cache in [p for p in path.iterdir() if p.name == "__pycache__" and p.is_dir()]:
            shutil.rmtree(cache)
        if not any(path.iterdir()):
            path.rmdir()
