"""View: find, focus, filter and automatic layout."""

from __future__ import annotations

from typing import Any

from whiteboard.engine import Context
from whiteboard.model import ARROW_TYPES, BLOCK_KINDS
from whiteboard.registry import OpError, flag, integer, items, operation

GAP = 60


# Input: ctx (Context), text (str)
# Output: dict[str, Any]
# Processing: Find and highlight blocks whose name, path or id contains text.
# Changed by: Claude Code
# Date: 2026-10-22
@operation("view.find", command="/find", tool="plovane_find", group="plovane_view", mutates=False)
def find(ctx: Context, text: str) -> dict[str, Any]:
    """Finds and highlights a block."""
    needle = text.lower()
    hits = [b for b in ctx.board.blocks.values()
            if needle in b.name.lower() or needle in (b.path or "").lower() or needle in b.id]
    if ctx.actor != "user":
        hits = [b for b in hits if ctx.access(b) >= 1]
    ctx.board.view["highlight"] = [b.id for b in hits]
    return {"text": text, "found": [{"block": b.id, "name": b.name, "kind": b.kind, "layer": b.layer,
                                     "path": b.path} for b in hits]}


# Input: ctx (Context), block (str | None), clear (Any)
# Output: dict[str, Any]
# Processing: Dim everything except a block and its connections.
# Changed by: Claude Code
# Date: 2026-10-22
@operation("view.focus", command="/focus", tool="plovane_focus", group="plovane_view", mutates=False)
def focus(ctx: Context, block: str | None = None, clear: Any = False) -> dict[str, Any]:
    """Dims everything except a block and its connections."""
    if flag(clear) or block is None:
        ctx.board.view["focus"], ctx.board.view["dimmed"] = None, []
        return {"focus": None}
    target = ctx.board.block(block)
    ctx.require(target, "read")
    keep = {target.id} | {b.id for b in ctx.board.descendants(target.id)}
    for arrow in ctx.board.arrows_of(target.id):
        keep |= {arrow.source, arrow.target}
    ctx.board.view["focus"] = target.id
    ctx.board.view["dimmed"] = sorted(set(ctx.board.blocks) - keep)
    return {"focus": target.id, "shown": sorted(keep)}


# Input: ctx (Context), direction (str), layer (Any)
# Output: dict[str, Any]
# Processing: Arrange a layer's top-level blocks in ranks along the arrows.
# Changed by: Claude Code
# Date: 2026-10-22
@operation("view.layout", command="/layout", tool="plovane_layout", group="plovane_view")
def layout(ctx: Context, direction: str = "left-to-right", layer: Any = None) -> dict[str, Any]:
    """Rearranges the canvas automatically."""
    if direction not in ("left-to-right", "top-to-bottom"):
        raise OpError("INVALID_ARGS", "direction is left-to-right or top-to-bottom")
    number = integer(layer, "layer") if layer is not None else ctx.board.view.get("layer", 1)
    blocks = {b.id: b for b in ctx.board.blocks.values() if b.layer == number and b.parent is None}
    for block in blocks.values():
        ctx.require(block)
    if not blocks:
        return {"layer": number, "moved": []}
    # Rank = longest path from a block with no incoming arrow; cycles are cut.
    edges = [(a.source, a.target) for a in ctx.board.arrows.values() if a.source in blocks and a.target in blocks]
    rank = {b: 0 for b in blocks}
    for _ in range(len(blocks)):
        changed = False
        for source, target in edges:
            if rank[target] < rank[source] + 1 <= len(blocks):
                rank[target], changed = rank[source] + 1, True
        if not changed:
            break
    columns: dict[int, list[str]] = {}
    for block_id in sorted(blocks, key=lambda b: (rank[b], blocks[b].y, blocks[b].x)):
        columns.setdefault(rank[block_id], []).append(block_id)
    across = max((blocks[b].w if direction == "left-to-right" else blocks[b].h) for b in blocks) + GAP
    for column, members in columns.items():
        offset = 40
        for block_id in members:
            block = blocks[block_id]
            if direction == "left-to-right":
                block.x, block.y = ctx.snap(40 + column * across), ctx.snap(offset)
                offset += block.h + GAP
            else:
                block.x, block.y = ctx.snap(offset), ctx.snap(40 + column * across)
                offset += block.w + GAP
    return {"layer": number, "direction": direction, "moved": sorted(blocks)}


# Input: ctx (Context), blocks (Any), arrows (Any), clear (Any)
# Output: dict[str, Any]
# Processing: Show only some block kinds or arrow types.
# Changed by: Claude Code
# Date: 2026-10-22
@operation("view.filter", command="/filter", tool="plovane_filter", group="plovane_view", mutates=False)
def filter_(ctx: Context, blocks: Any = None, arrows: Any = None, clear: Any = False) -> dict[str, Any]:
    """Shows only certain block or arrow types."""
    if flag(clear):
        ctx.board.view["filter"] = {}
        return {"filter": {}}
    kinds, types = items(blocks), items(arrows)
    unknown = [k for k in kinds if k not in BLOCK_KINDS] + [t for t in types if t not in ARROW_TYPES]
    if unknown:
        raise OpError("INVALID_ARGS", f"unknown kinds or arrow types: {unknown}")
    if not kinds and not types:
        raise OpError("INVALID_ARGS", "give --blocks kinds and/or --arrows types, or --clear")
    ctx.board.view["filter"] = {"blocks": kinds, "arrows": types}
    return {"filter": ctx.board.view["filter"]}
