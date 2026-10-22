"""Batch: many operations as one. All succeed or none do.

Each item runs inside the batch's single transaction, so a failure in item
five rolls back items one to four as well, files included, and the whole
batch is one entry on the undo stack.
"""

from __future__ import annotations

from typing import Any

from whiteboard.engine import Context
from whiteboard.registry import OPERATIONS, OpError, operation


# Input: commands (Any)
# Output: list[tuple[str, dict[str, Any]]]
# Processing: Batch items as (operation, args), from command text or JSON.
# Changed by: Sourish
# Date: 2026-10-22
def items_of(commands: Any) -> list[tuple[str, dict[str, Any]]]:
    from whiteboard.commands import parse

    if isinstance(commands, str):
        commands = [line for line in commands.splitlines() if line.strip()]
    if not isinstance(commands, list) or not commands:
        raise OpError("INVALID_ARGS", "a batch needs a list of commands, one per line")
    parsed = []
    for item in commands:
        if isinstance(item, str):
            parsed.append(parse(item if item.strip().startswith("/") else "/" + item.strip()))
        elif isinstance(item, dict) and "op" in item:
            parsed.append((item["op"], dict(item.get("args") or {})))
        else:
            raise OpError("INVALID_ARGS", f"batch item {item!r} is neither a command nor {{op, args}}")
    if any(op == "batch" for op, _ in parsed):
        raise OpError("INVALID_ARGS", "a batch cannot contain another batch")
    return parsed


# Input: args (dict[str, Any])
# Output: bool
# Processing: Whether any item in the batch needs confirmation.
# Changed by: Sourish
# Date: 2026-10-22
def needs_confirmation(args: dict[str, Any]) -> bool:
    try:
        parsed = items_of(args.get("commands"))
    except OpError:
        return False
    for op, op_args in parsed:
        spec = OPERATIONS.get(op)
        if spec and (spec.confirm(op_args) if callable(spec.confirm) else spec.confirm):
            return True
    return False


# Input: ctx (Context), commands (Any)
# Output: dict[str, Any]
# Processing: Run many operations in one transaction.
# Changed by: Sourish
# Date: 2026-10-22
@operation("batch", command="/batch", tool="plovane_batch", group="plovane_batch", confirm=needs_confirmation)
def batch(ctx: Context, commands: Any) -> dict[str, Any]:
    """Runs many operations as one; all succeed or none do."""
    results = []
    for index, (op, args) in enumerate(items_of(commands), start=1):
        if op in ("history.undo", "history.redo"):
            raise OpError("INVALID_ARGS", "undo and redo cannot run inside a batch")
        try:
            results.append({"op": op, "result": ctx.execute(op, args)})
        except OpError as exc:
            raise OpError(exc.code, f"item {index} ({op}) failed, so nothing in the batch was applied: "
                                    f"{exc.message}") from None
    return {"ran": len(results), "results": results}
