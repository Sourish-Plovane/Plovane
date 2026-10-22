"""History and Git: undo, redo, snapshots, change history and syncing."""

from __future__ import annotations

import json
from typing import Any

from whiteboard.engine import Context
from whiteboard.registry import OpError, operation
from whiteboard.store import now


# Input: ctx (Context)
# Output: dict[str, Any]
# Processing: Undo the last change, files included.
# Changed by: Claude Code
# Date: 2026-10-22
@operation("history.undo", command="/undo", tool="plovane_undo", group="plovane_history", mutates=False)
def undo(ctx: Context) -> dict[str, Any]:
    """Undoes the last change."""
    if ctx.dry_run:
        return {"would_undo": peek(ctx, "undo")}
    return ctx.ws.step_history("undo", ctx.actor)


# Input: ctx (Context)
# Output: dict[str, Any]
# Processing: Redo the last undone change.
# Changed by: Claude Code
# Date: 2026-10-22
@operation("history.redo", command="/redo", tool="plovane_redo", group="plovane_history", mutates=False)
def redo(ctx: Context) -> dict[str, Any]:
    """Redoes the last undone change."""
    if ctx.dry_run:
        return {"would_redo": peek(ctx, "redo")}
    return ctx.ws.step_history("redo", ctx.actor)


# Input: ctx (Context), stack (str)
# Output: str | None
# Processing: The operation on top of the undo or redo stack.
# Changed by: Claude Code
# Date: 2026-10-22
def peek(ctx: Context, stack: str) -> str | None:
    entries = ctx.ws.store.load_history()[stack]
    return entries[-1]["op"] if entries else None


# Input: ctx (Context), name (str), restore (Any)
# Output: dict[str, Any]
# Processing: Save the board under a name, or restore a saved one.
# Changed by: Claude Code
# Date: 2026-10-22
@operation("history.snapshot", command="/snapshot", tool="plovane_snapshot", group="plovane_history")
def snapshot(ctx: Context, name: str, restore: Any = False) -> dict[str, Any]:
    """Saves the current whiteboard state."""
    from whiteboard.model import Board
    from whiteboard.registry import flag
    from whiteboard.store import STATE_DIR

    safe = "".join(c if c.isalnum() or c in "-_" else "-" for c in name).strip("-") or "snapshot"
    rel = f"{STATE_DIR}/snapshots/{safe}.json"
    target = ctx.root / rel
    if flag(restore):
        ctx.require(None)
        if not target.exists():
            raise OpError("NOT_FOUND", f"no snapshot {name!r}")
        ctx.ws.board = Board.from_dict(json.loads(target.read_text(encoding="utf-8"))["board"])
        return {"restored": safe}
    if ctx.dry_run:
        return {"would_save": rel}
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps({"name": name, "at": now(), "board": ctx.board.to_dict()}, indent=1),
                      encoding="utf-8")
    return {"saved": safe, "path": rel}


# Input: ctx (Context), block (str), last (Any)
# Output: dict[str, Any]
# Processing: A block's change history: whiteboard operations and Git commits.
# Changed by: Claude Code
# Date: 2026-10-22
@operation("history.show", command="/history", tool="plovane_history", group="plovane_history", mutates=False)
def history(ctx: Context, block: str, last: Any = 50) -> dict[str, Any]:
    """Shows changes over time."""
    from whiteboard.registry import integer

    target = ctx.board.block(block)
    ctx.require(target, "read")
    mentions = [e for e in ctx.ws.store.read_log() if target.id in json.dumps(e.get("args", {}))
                or target.id in json.dumps(e.get("detail", ""))]
    commits = []
    path = target.path or target.meta.get("file")
    if path and ctx.run(["git", "rev-parse", "--is-inside-work-tree"]).returncode == 0:
        out = ctx.run(["git", "log", f"-{integer(last, 'last')}", "--format=%h%x09%an%x09%ad%x09%s",
                       "--date=short", "--", path]).stdout
        commits = [dict(zip(("commit", "author", "date", "message"), line.split("\t", 3)))
                   for line in out.splitlines() if line]
    return {"block": target.id, "whiteboard": mentions[-integer(last, "last"):], "commits": commits}


# Input: ctx (Context)
# Output: dict[str, Any]
# Processing: Changed files compared to Git, mapped to their blocks.
# Changed by: Claude Code
# Date: 2026-10-22
@operation("git.status", command="/git status", tool="plovane_git_status", group="plovane_history", mutates=False)
def git_status(ctx: Context) -> dict[str, Any]:
    """Shows changed blocks compared to GitHub."""
    if ctx.run(["git", "rev-parse", "--is-inside-work-tree"]).returncode != 0:
        raise OpError("UNAVAILABLE", "the project is not a Git repository")
    branch = ctx.run(["git", "rev-parse", "--abbrev-ref", "HEAD"]).stdout.strip()
    changed = []
    for line in ctx.run(["git", "status", "--porcelain"]).stdout.splitlines():
        state, path = line[:2].strip(), line[3:].split(" -> ")[-1].strip('"')
        owner = ctx.board.block_for_path(path)
        changed.append({"path": path, "state": state, "block": owner.id if owner else None})
    ahead_behind = ctx.run(["git", "rev-list", "--left-right", "--count", "@{upstream}...HEAD"])
    counts = ahead_behind.stdout.split() if ahead_behind.returncode == 0 else []
    return {"branch": branch, "changed": changed,
            "behind": int(counts[0]) if counts else None, "ahead": int(counts[1]) if counts else None}


# Input: ctx (Context), direction (str)
# Output: dict[str, Any]
# Processing: Pull (fast-forward only) or push the current branch.
# Changed by: Claude Code
# Date: 2026-10-22
@operation("git.sync", command="/git sync", tool="plovane_git_sync", group="plovane_history", mutates=False,
           confirm=True)
def git_sync(ctx: Context, direction: str) -> dict[str, Any]:
    """Pulls or pushes changes."""
    ctx.require(None)
    if direction not in ("pull", "push"):
        raise OpError("INVALID_ARGS", "direction is pull or push")
    if ctx.dry_run:
        return {"would": f"git {direction}"}
    argv = ["git", "pull", "--ff-only"] if direction == "pull" else ["git", "push"]
    done = ctx.run(argv, timeout=300)
    if done.returncode != 0:
        raise OpError("GIT_FAILED", (done.stderr or done.stdout).strip()[-1500:])
    if direction == "pull":
        ctx.ws.board = ctx.ws.store.load_board()
    return {"ran": " ".join(argv), "output": (done.stdout + done.stderr).strip()[-1500:]}
