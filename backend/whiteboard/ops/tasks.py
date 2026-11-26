"""Tasks: the temporary display of long-running work and how long it has left.

Tasks sit beside the drawing, not in it (see ``backend.whiteboard.tasks``), so
these commands change nothing on the board and any caller may use them: a
person, the whiteboard AI, Claude Code over MCP, or a script.
"""

from __future__ import annotations

from typing import Any

from backend.whiteboard.engine import Context
from backend.whiteboard.registry import OpError, operation
from backend.whiteboard.tasks import TaskBoard, TaskError


# Input: ctx (Context), at (str | None)
# Output: str | None
# Processing: The block id an update is about, so the browser can go there; OpError for a block that does not exist.
# Changed by: Claude Code
# Date: 2026-11-26
def place(ctx: Context, at: str | None) -> str | None:
    if not at:
        return None
    block = ctx.board.block(str(at))
    if block is None:
        raise OpError("NOT_FOUND", f"no block {at!r} to point the task at")
    return block.id


# Input: ctx (Context)
# Output: TaskBoard
# Processing: The project's task display.
# Changed by: Claude Code
# Date: 2026-11-26
def board(ctx: Context) -> TaskBoard:
    return TaskBoard(ctx.ws.root)


# Input: ctx (Context), name (str), estimate (Any), kind (str | None), at (str | None), message (str | None)
# Output: dict[str, Any]
# Processing: Start a task on the temporary display.
# Changed by: Claude Code
# Date: 2026-11-26
@operation("task.start", command="/task start", tool="plovane_task_start", group="plovane_task", mutates=False)
def task_start(ctx: Context, name: str, estimate: Any = None, kind: str | None = None,
               at: str | None = None, message: str | None = None) -> dict[str, Any]:
    """Starts a long task on the temporary display (estimate like 45m; without one, past runs of the same kind decide)."""
    try:
        task = board(ctx).start(name, by=ctx.actor, kind=kind, estimate=estimate, at=place(ctx, at), message=message)
    except TaskError as exc:
        raise OpError("INVALID_ARGS", str(exc)) from None
    return {"task": task, "message": f"{task['name']}: about {task['left_text']} left" if task["left"] is not None
            else f"{task['name']} started; no estimate yet (give --estimate, or send --progress)"}


# Input: ctx (Context), task (str), progress (Any), message (str | None), at (str | None), estimate (Any)
# Output: dict[str, Any]
# Processing: Update a task's progress, message, estimate or the block it is about.
# Changed by: Claude Code
# Date: 2026-11-26
@operation("task.update", command="/task update", tool="plovane_task_update", group="plovane_task", mutates=False)
def task_update(ctx: Context, task: str, progress: Any = None, message: str | None = None,
                at: str | None = None, estimate: Any = None) -> dict[str, Any]:
    """Updates a task (progress like 40%, a message, a new estimate, or --at the block it is now about)."""
    try:
        shown = board(ctx).update(task, by=ctx.actor, progress=progress, message=message,
                                  at=place(ctx, at), estimate=estimate)
    except TaskError as exc:
        raise OpError("NOT_FOUND" if str(exc).startswith("no task") else "INVALID_ARGS", str(exc)) from None
    return {"task": shown, "message": f"{shown['name']}: {shown['left_text']} left"}


# Input: ctx (Context), task (str), failed (bool), message (str | None)
# Output: dict[str, Any]
# Processing: Mark a task done (or failed); it leaves the display a few minutes later.
# Changed by: Claude Code
# Date: 2026-11-26
@operation("task.done", command="/task done", tool="plovane_task_done", group="plovane_task", mutates=False)
def task_done(ctx: Context, task: str, failed: Any = False, message: str | None = None) -> dict[str, Any]:
    """Marks a task done (or --failed); its time teaches the estimate for the next task of that kind."""
    failed = failed is True or str(failed).lower() in ("1", "true", "yes", "failed")
    try:
        shown = board(ctx).finish(task, by=ctx.actor, failed=failed, message=message)
    except TaskError as exc:
        raise OpError("NOT_FOUND" if str(exc).startswith("no task") else "INVALID_ARGS", str(exc)) from None
    return {"task": shown, "message": f"{shown['name']} {shown['status']} after {shown['elapsed_text']}"}


# Input: ctx (Context)
# Output: dict[str, Any]
# Processing: Every task on the temporary display with its time left.
# Changed by: Claude Code
# Date: 2026-11-26
@operation("task.list", command="/task list", tool="plovane_task_list", group="plovane_task", mutates=False)
def task_list(ctx: Context) -> dict[str, Any]:
    """Lists the tasks on the temporary display and how long each has left."""
    tasks = board(ctx).listing()
    return {"tasks": tasks, "running": sum(t["status"] == "running" for t in tasks)}
