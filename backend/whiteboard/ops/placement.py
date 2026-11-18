"""Placement and coordinates: where blocks sit and how big they are.

Relative placement (``--right-of``, ``--below``, ``--inside``) is the form AI
layouts should use: it survives the other block moving, and it costs an agent
no arithmetic.
"""

from __future__ import annotations

from typing import Any

from backend.whiteboard.engine import Context
from backend.whiteboard.model import Block
from backend.whiteboard.registry import OpError, flag, integer, items, number, operation, pair

DEFAULT_GAP = 40


# Input: ctx (Context), block (Block), at (Any), anchor (str | None), right_of (str | None), left_of (str | None), below (str | None), above (str | None), inside (str | None), gap (Any)
# Output: bool
# Processing: Apply an exact or relative position; False if none was given.
# Changed by: Sourish
# Date: 2026-10-22
def apply_position(
    ctx: Context, block: Block, *, at: Any = None, anchor: str | None = None,
    right_of: str | None = None, left_of: str | None = None, below: str | None = None,
    above: str | None = None, inside: str | None = None, gap: Any = None,
) -> bool:
    """Shared by every command that creates or moves a block."""
    spacing = number(gap, "gap") if gap is not None else DEFAULT_GAP
    relative = {"right_of": right_of, "left_of": left_of, "below": below, "above": above}
    given = {k: v for k, v in relative.items() if v}
    if len(given) > 1:
        raise OpError("INVALID_ARGS", "give one of --right-of, --left-of, --below, --above")
    if inside:
        container = ctx.board.block(inside)
        if container.id == block.id:
            raise OpError("INVALID_ARGS", "a block cannot go inside itself")
        if container.id in {b.id for b in ctx.board.descendants(block.id)}:
            raise OpError("INVALID_ARGS", "a block cannot go inside one of its own children")
        block.parent = container.id
        # A child is never on a shallower layer than its container.
        block.layer = max(block.layer, container.layer)
        if not at and not given:
            ctx.auto_place(block)
            grow_to_fit(ctx, container)
            return True
    if given:
        key, ref = next(iter(given.items()))
        other = ctx.board.block(ref)
        if other.id == block.id:
            raise OpError("INVALID_ARGS", "a block cannot be placed relative to itself")
        # Same coordinate space as the reference block.
        block.parent = other.parent
        block.layer = other.layer
        if key == "right_of":
            block.x, block.y = other.x + other.w + spacing, other.y
        elif key == "left_of":
            block.x, block.y = other.x - block.w - spacing, other.y
        elif key == "below":
            block.x, block.y = other.x, other.y + other.h + spacing
        else:
            block.x, block.y = other.x, other.y - block.h - spacing
    elif at is not None:
        x, y = pair(at, "at")
        if anchor == "center":
            x, y = x - block.w / 2, y - block.h / 2
        elif anchor not in (None, "top-left"):
            raise OpError("INVALID_ARGS", "anchor is top-left (default) or center")
        block.x, block.y = x, y
    else:
        return bool(inside)
    block.x, block.y = ctx.snap(block.x), ctx.snap(block.y)
    if block.parent:
        grow_to_fit(ctx, ctx.board.blocks[block.parent])
    return True


# Input: ctx (Context), container (Block)
# Output: None
# Processing: Grow a container so its children fit inside it.
# Changed by: Sourish
# Date: 2026-10-22
def grow_to_fit(ctx: Context, container: Block) -> None:
    children = [b for b in ctx.board.blocks.values() if b.parent == container.id]
    if children:
        container.w = max(container.w, max(c.x + c.w for c in children) + 20)
        container.h = max(container.h, max(c.y + c.h for c in children) + 20)


# Input: ctx (Context), block (Block)
# Output: dict[str, Any]
# Processing: A block's position and size as JSON.
# Changed by: Sourish
# Date: 2026-10-22
def geometry(ctx: Context, block: Block) -> dict[str, Any]:
    ax, ay = ctx.board.absolute(block)
    return {"block": block.id, "layer": block.layer, "x": block.x, "y": block.y, "w": block.w, "h": block.h,
            "parent": block.parent, "absolute": {"x": ax, "y": ay}}


# Input: ctx (Context), block (str), at (Any), anchor (str | None), right_of, left_of, below, above, inside (str | None), gap (Any)
# Output: dict[str, Any]
# Processing: Place a block at coordinates or relative to another.
# Changed by: Sourish
# Date: 2026-10-22
@operation("place", command="/place", tool="plovane_place", group="plovane_place")
def place(ctx: Context, block: str, at: Any = None, anchor: str | None = None, right_of: str | None = None,
          left_of: str | None = None, below: str | None = None, above: str | None = None,
          inside: str | None = None, gap: Any = None) -> dict[str, Any]:
    """Places a block at exact coordinates, or relative to another block."""
    target = ctx.board.block(block)
    ctx.require(target)
    if not apply_position(ctx, target, at=at, anchor=anchor, right_of=right_of, left_of=left_of,
                          below=below, above=above, inside=inside, gap=gap):
        raise OpError("INVALID_ARGS", "give --at x,y or one of --right-of/--left-of/--below/--above/--inside")
    return geometry(ctx, target)


# Input: ctx (Context), blocks (str | list), edge (str)
# Output: dict[str, Any]
# Processing: Align blocks along an edge or centre line.
# Changed by: Sourish
# Date: 2026-10-22
@operation("align", command="/align", tool="plovane_align", group="plovane_place")
def align(ctx: Context, blocks: Any, edge: str = "top") -> dict[str, Any]:
    """Aligns blocks along an edge or center."""
    targets = resolve_all(ctx, blocks, minimum=2)
    if edge == "top":
        y = min(b.y for b in targets)
        for b in targets:
            b.y = y
    elif edge == "bottom":
        bottom = max(b.y + b.h for b in targets)
        for b in targets:
            b.y = bottom - b.h
    elif edge == "left":
        x = min(b.x for b in targets)
        for b in targets:
            b.x = x
    elif edge == "right":
        right = max(b.x + b.w for b in targets)
        for b in targets:
            b.x = right - b.w
    elif edge == "center":
        middle = sum(b.y + b.h / 2 for b in targets) / len(targets)
        for b in targets:
            b.y = ctx.snap(middle - b.h / 2)
    else:
        raise OpError("INVALID_ARGS", "edge is top, bottom, left, right or center")
    return {"aligned": [geometry(ctx, b) for b in targets]}


# Input: ctx (Context), blocks (str | list), direction (str), gap (Any)
# Output: dict[str, Any]
# Processing: Space blocks evenly in a row or column.
# Changed by: Sourish
# Date: 2026-10-22
@operation("distribute", command="/distribute", tool="plovane_distribute", group="plovane_place")
def distribute(ctx: Context, blocks: Any, direction: str = "horizontal", gap: Any = None) -> dict[str, Any]:
    """Spaces blocks evenly."""
    targets = resolve_all(ctx, blocks, minimum=2)
    spacing = number(gap, "gap") if gap is not None else DEFAULT_GAP
    if direction not in ("horizontal", "vertical"):
        raise OpError("INVALID_ARGS", "direction is horizontal or vertical")
    axis = "x" if direction == "horizontal" else "y"
    size = "w" if axis == "x" else "h"
    ordered = sorted(targets, key=lambda b: getattr(b, axis))
    cursor = getattr(ordered[0], axis)
    for b in ordered:
        setattr(b, axis, ctx.snap(cursor))
        cursor = getattr(b, axis) + getattr(b, size) + spacing
    return {"distributed": [geometry(ctx, b) for b in ordered]}


# Input: ctx (Context), size (Any), snap (Any)
# Output: dict[str, Any]
# Processing: Set grid size and snapping.
# Changed by: Sourish
# Date: 2026-10-22
@operation("grid", command="/grid", tool="plovane_grid", group="plovane_place")
def grid(ctx: Context, size: Any = None, snap: Any = None) -> dict[str, Any]:
    """Sets grid size and snapping."""
    ctx.require(None)
    settings = ctx.board.view.setdefault("grid", {"size": 20, "snap": True})
    if size is not None:
        value = integer(size, "grid size")
        if value <= 0:
            raise OpError("INVALID_ARGS", "grid size must be positive")
        settings["size"] = value
    if snap is not None:
        settings["snap"] = flag(snap)
    return {"grid": settings}


# Input: ctx (Context), block (str)
# Output: dict[str, Any]
# Processing: A block's position and size.
# Changed by: Sourish
# Date: 2026-10-22
@operation("coords", command="/coords", tool="plovane_coords", group="plovane_place", mutates=False)
def coords(ctx: Context, block: str) -> dict[str, Any]:
    """Shows a block's position and size."""
    target = ctx.board.block(block)
    ctx.require(target, "read")
    return geometry(ctx, target)


# Input: ctx (Context), block (str), size (Any), fit (Any)
# Output: dict[str, Any]
# Processing: Set a block's size, or fit it to its name and children.
# Changed by: Sourish
# Date: 2026-10-22
@operation("size", command="/size", tool="plovane_size", group="plovane_place")
def resize(ctx: Context, block: str, size: Any = None, fit: Any = False) -> dict[str, Any]:
    """Sets a block's width and height, or fits it to its contents."""
    target = ctx.board.block(block)
    ctx.require(target)
    if flag(fit):
        label = max(120, len(target.name) * 8 + 40)
        children = [b for b in ctx.board.blocks.values() if b.parent == target.id]
        target.w = max(label, max((c.x + c.w for c in children), default=0) + 20)
        target.h = max(40, max((c.y + c.h for c in children), default=0) + 20)
    elif size is not None:
        w, h = pair(size, "size", sep="x") if "x" in str(size) else pair(size, "size")
        if w <= 0 or h <= 0:
            raise OpError("INVALID_ARGS", "width and height must be positive")
        target.w, target.h = w, h
    else:
        raise OpError("INVALID_ARGS", "give a size like 240x120, or --fit")
    return geometry(ctx, target)


# Input: ctx (Context), blocks (Any), minimum (int)
# Output: list[Block]
# Processing: Resolve a list of block refs the actor may change.
# Changed by: Sourish
# Date: 2026-10-22
def resolve_all(ctx: Context, blocks: Any, minimum: int = 1) -> list[Block]:
    refs = items(blocks)
    if len(refs) < minimum:
        raise OpError("INVALID_ARGS", f"give at least {minimum} blocks, comma-separated")
    targets = [ctx.board.block(ref) for ref in refs]
    for target in targets:
        ctx.require(target)
    return targets
