"""The whiteboard file: save the whole board to whiteboard.json and open it again.

The file is plain JSON describing the board (blocks, arrows, endpoints, layers,
styles, templates, agents and view). It holds no code: code stays in Git.
Only a person may save or open one. Opening asks first, and its preview lists
every change to agent access, so a shared file can never grant access quietly.
"""

from __future__ import annotations

import json
from typing import Any

from backend.whiteboard.engine import Context
from backend.whiteboard.model import Board
from backend.whiteboard.registry import OpError, operation

DEFAULT_FILE = "whiteboard.json"
FORMAT = "plovane-whiteboard"
FORMAT_VERSION = 1
MAX_BYTES = 20 * 1024 * 1024
LOCAL_ONLY = ("compiles", "alerts")
"""Board fields tied to this machine (pending compile previews, monitor alerts), left out of the file."""


# Input: file (Any)
# Output: str
# Processing: The project-relative path of a whiteboard file, adding .json when the name has no extension.
# Changed by: Claude Code
# Date: 2026-11-22
def file_name(file: Any) -> str:
    name = str(file or DEFAULT_FILE).strip().replace("\\", "/").strip("/")
    if not name:
        name = DEFAULT_FILE
    if "." not in name.rsplit("/", 1)[-1]:
        name += ".json"
    if not name.endswith(".json"):
        raise OpError("INVALID_PATH", f"{name!r}: a whiteboard file ends in .json")
    return name


# Input: board (Board)
# Output: dict[str, Any]
# Processing: The whiteboard file's content for a board.
# Changed by: Claude Code
# Date: 2026-11-22
def document(board: Board) -> dict[str, Any]:
    data = board.to_dict()
    for key in LOCAL_ONLY:
        data.pop(key, None)
    return {"format": FORMAT, "version": FORMAT_VERSION, "board": data}


# Input: board (Board)
# Output: str
# Processing: The whiteboard file as text: sorted keys and a fixed layout, so Git diffs show only real changes.
# Changed by: Claude Code
# Date: 2026-11-22
def dumps(board: Board) -> str:
    return json.dumps(document(board), indent=2, sort_keys=True, ensure_ascii=False) + "\n"


# Input: text (str)
# Output: Board
# Processing: Read a whiteboard file's text into a board, refusing anything that isn't one.
# Changed by: Claude Code
# Date: 2026-11-22
def parse(text: str) -> Board:
    if len(text.encode("utf-8")) > MAX_BYTES:
        raise OpError("INVALID_FILE", f"a whiteboard file is at most {MAX_BYTES // (1024 * 1024)} MB")
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise OpError("INVALID_FILE", f"not valid JSON (line {exc.lineno}): {exc.msg}") from None
    if not isinstance(data, dict) or data.get("format") != FORMAT:
        raise OpError("INVALID_FILE", f"not a Plovane whiteboard file (expected \"format\": \"{FORMAT}\")")
    if data.get("version") != FORMAT_VERSION:
        raise OpError("INVALID_FILE", f"whiteboard file version {data.get('version')!r} is not supported; "
                                      f"this Plovane reads version {FORMAT_VERSION}")
    if not isinstance(data.get("board"), dict):
        raise OpError("INVALID_FILE", "the file has no board")
    try:
        board = Board.from_dict(data["board"])
    except (TypeError, ValueError, AttributeError) as exc:
        raise OpError("INVALID_FILE", f"the board in this file is malformed: {exc}") from None
    missing = sorted({ref for a in board.arrows.values() for ref in (a.source, a.target)} - set(board.blocks))
    if missing:
        raise OpError("INVALID_FILE", f"arrows point at blocks that aren't in the file: {missing[:5]}")
    orphans = sorted(b.id for b in board.blocks.values() if b.parent and b.parent not in board.blocks)
    if orphans:
        raise OpError("INVALID_FILE", f"blocks sit inside blocks that aren't in the file: {orphans[:5]}")
    return board


# Input: before (Board), after (Board)
# Output: list[dict[str, Any]]
# Processing: Every agent whose access a load would change, with its access before and after.
# Changed by: Claude Code
# Date: 2026-11-22
def access_changes(before: Board, after: Board) -> list[dict[str, Any]]:
    changes = []
    for name in sorted(set(before.agents) | set(after.agents)):
        old, new = before.agents.get(name), after.agents.get(name)
        old_access = {"grants": old.grants, "regions": old.regions} if old else None
        new_access = {"grants": new.grants, "regions": new.regions} if new else None
        if old_access != new_access:
            changes.append({"agent": name, "before": old_access, "after": new_access})
    return changes


# Input: ctx (Context), file (Any)
# Output: dict[str, Any]
# Processing: Write the whole board to a whiteboard file in the project.
# Changed by: Claude Code
# Date: 2026-11-22
@operation("board.save", command="/board save", tool="plovane_board_save", group="plovane_board", agents=False)
def save(ctx: Context, file: Any = None) -> dict[str, Any]:
    """Saves the whole whiteboard to a JSON file in the project (whiteboard.json by default)."""
    name = file_name(file)
    if ctx.fs.is_dir(name):
        raise OpError("INVALID_PATH", f"{name!r} is a folder")
    text = dumps(ctx.board)
    changed = not ctx.fs.exists(name) or ctx.fs.read(name) != text
    if changed:
        ctx.fs.write(name, text)
    board = ctx.board
    return {"file": name, "saved": changed, "blocks": len(board.blocks), "arrows": len(board.arrows),
            "endpoints": len(board.endpoints), "agents": len(board.agents)}


# Input: ctx (Context), file (Any), content (Any)
# Output: dict[str, Any]
# Processing: Replace the board with one from a whiteboard file (or its text), as one undoable change.
# Changed by: Claude Code
# Date: 2026-11-22
@operation("board.load", command="/board load", tool="plovane_board_load", group="plovane_board",
           confirm=True, agents=False)
def load(ctx: Context, file: Any = None, content: Any = None) -> dict[str, Any]:
    """Opens a whiteboard file, replacing the current board; changes no project files."""
    if content is not None:
        if not isinstance(content, str):
            raise OpError("INVALID_ARGS", "content is the whiteboard file's text")
        source, text = "uploaded file", content
    else:
        source = file_name(file)
        text = ctx.fs.read(source)
    loaded = parse(text)
    for key in LOCAL_ONLY:
        setattr(loaded, key, getattr(ctx.board, key))
    agents = access_changes(ctx.board, loaded)
    missing = sorted({b.path for b in loaded.blocks.values()
                      if b.path and b.kind in ("file", "folder") and not ctx.fs.exists(b.path)})
    ctx.ws.board = loaded
    if agents:
        ctx.notes.append(f"Agent access changes for: {', '.join(a['agent'] for a in agents)}. "
                         "Check them before confirming.")
    if missing:
        ctx.notes.append(f"{len(missing)} blocks stand for files or folders this project doesn't have, "
                         f"e.g. {missing[0]}")
    return {"file": source, "blocks": len(loaded.blocks), "arrows": len(loaded.arrows),
            "endpoints": len(loaded.endpoints), "agents": len(loaded.agents),
            "access_changes": agents, "missing_paths": missing[:20]}
