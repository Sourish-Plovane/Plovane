"""View: find, focus, filter and automatic layout."""

from __future__ import annotations

from typing import Any

from backend.whiteboard.engine import Context
from backend.whiteboard.model import ARROW_TYPES, BLOCK_KINDS, Block
from backend.whiteboard.ops.placement import grow_to_fit
from backend.whiteboard.registry import OpError, flag, integer, items, operation

GAP = 60


# Input: ctx (Context), text (str)
# Output: dict[str, Any]
# Processing: Find and highlight blocks whose name, path or id contains text.
# Changed by: Sourish
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
# Changed by: Sourish
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
# Changed by: Sourish
# Date: 2026-10-22
@operation("view.layout", command="/layout", tool="plovane_layout", group="plovane_view")
def layout(ctx: Context, direction: str = "left-to-right", layer: Any = None) -> dict[str, Any]:
    """Rearranges the canvas automatically."""
    if direction not in ("left-to-right", "top-to-bottom"):
        raise OpError("INVALID_ARGS", "direction is left-to-right or top-to-bottom")
    number = integer(layer, "layer") if layer is not None else ctx.board.view.get("layer", 1)
    on_layer = [b for b in ctx.board.blocks.values() if b.layer == number]
    for block in on_layer:
        ctx.require(block)
    # Blocks inside a folder or class are laid out within it, group by group.
    groups: dict[str | None, dict[str, Block]] = {}
    for block in on_layer:
        groups.setdefault(block.parent, {})[block.id] = block
    for parent, members in groups.items():
        arrange(ctx, members, direction, start=(20, 40) if parent else (40, 40))
    # Then grow each container to fit, deepest first.
    containers = [ctx.board.blocks[p] for p in groups if p in ctx.board.blocks]
    for container in sorted(containers, key=lambda c: len(ancestors(ctx, c)), reverse=True):
        grow_to_fit(ctx, container)
    moved = sorted(b.id for b in on_layer)
    return {"layer": number, "direction": direction, "moved": moved}


# Input: ctx (Context), block (Block)
# Output: list[str]
# Processing: The ids of a block's containers, nearest first.
# Changed by: Sourish
# Date: 2026-10-22
def ancestors(ctx: Context, block: Block) -> list[str]:
    found, current = [], block.parent
    while current and current in ctx.board.blocks and current not in found:
        found.append(current)
        current = ctx.board.blocks[current].parent
    return found


# Input: ctx (Context), blocks (dict[str, Block]), direction (str), start (tuple[float, float])
# Output: None
# Processing: Place sibling blocks in ranks along the arrows between them.
# Changed by: Sourish
# Date: 2026-10-22
def arrange(ctx: Context, blocks: dict[str, Block], direction: str, start: tuple[float, float]) -> None:
    """Rank is the longest path from a block with no incoming arrow; cycles
    are cut. Each rank is a column (left-to-right) or a row (top-to-bottom)."""
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
    x0, y0 = start
    for column, members in columns.items():
        offset = y0 if direction == "left-to-right" else x0
        for block_id in members:
            block = blocks[block_id]
            if direction == "left-to-right":
                block.x, block.y = ctx.snap(x0 + column * across), ctx.snap(offset)
                offset += block.h + GAP
            else:
                block.x, block.y = ctx.snap(offset), ctx.snap(y0 + column * across)
                offset += block.w + GAP


# Input: ctx (Context), blocks (Any), arrows (Any), clear (Any)
# Output: dict[str, Any]
# Processing: Show only some block kinds or arrow types.
# Changed by: Sourish
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


# Input: board (Board), block (Block)
# Output: bool
# Processing: Whether a block's parent is just the folder its own path already names.
# Changed by: Sourish
# Date: 2026-10-22
def parent_in_path(board, block: Block) -> bool:
    """True for a file or folder whose parent is the folder block for its
    directory, so ``/board`` can leave the repeated parent out."""
    parent = board.blocks.get(block.parent or "")
    if parent is None or block.kind not in ("file", "folder") or not block.path or parent.kind != "folder":
        return False
    return (parent.path or "").strip("/") == block.path.strip("/").rpartition("/")[0]


# Input: ctx (Context), layer (Any), block (str | None), limit (Any)
# Output: dict[str, Any]
# Processing: Read the board: an overview, one layer, or one block with everything inside it.
# Changed by: Sourish
# Date: 2026-10-22
@operation("board.read", command="/board", tool="plovane_board_read", group="plovane_view", mutates=False)
def read(ctx: Context, layer: Any = None, block: str | None = None, limit: Any = 200) -> dict[str, Any]:
    """Reads the whiteboard: blocks, arrows, endpoints and status, for an AI to get the picture first.

    ``parent`` is left out when it adds nothing: top-level blocks, and files
    or folders whose parent is the folder already named in their path."""
    cap = integer(limit, "limit")
    board = ctx.board
    if block is not None:
        root = board.block(block)
        ctx.require(root, "read")
        chosen = [root, *board.descendants(root.id)]
    else:
        number = integer(layer, "layer") if layer is not None else None
        chosen = [b for b in board.blocks.values() if number is None or b.layer == number]
    if ctx.actor != "user":  # an agent sees only what it may read
        chosen = [b for b in chosen if ctx.access(b) >= 1]
    ids = {b.id for b in chosen}
    shown = sorted(chosen, key=lambda b: (b.layer, b.parent or "", b.y, b.x))[:cap]
    names = {b.id: b.name for b in board.blocks.values()}
    arrows = [a for a in board.arrows.values() if a.source in ids or a.target in ids]
    endpoints = [e for e in board.endpoints.values() if e.service in ids]
    return {
        "view": {k: board.view.get(k) for k in ("zoom", "layer", "focus", "theme")},
        "layers": [{"number": l.number, "name": l.name, "zoom": [l.zoom_min, l.zoom_max],
                    "blocks": sum(b.layer == l.number for b in chosen)}
                   for l in sorted(board.layers.values(), key=lambda l: l.number)],
        "blocks": [{"id": b.id, "kind": b.kind, "name": b.name, "layer": b.layer,
                    **({"parent": b.parent} if b.parent and not parent_in_path(board, b) else {}),
                    "path": b.path or b.meta.get("file"), "status": b.status,
                    **({"health": b.meta["health"]} if b.meta.get("health") else {}),
                    **({"library": b.meta["library"]} if b.meta.get("library") else {})} for b in shown],
        "blocks_total": len(chosen),
        "truncated": len(chosen) > cap,
        "arrows": [{"from": names.get(a.source, a.source), "to": names.get(a.target, a.target), "type": a.type,
                    "status": a.status, **({"endpoint": a.endpoint} if a.endpoint else {})} for a in arrows[:cap]],
        "endpoints": [{"id": e.id, "status": e.status,
                       "callers": [names.get(a.source, a.source) for a in board.arrows.values() if a.endpoint == e.id]}
                      for e in endpoints],
        "alerts": len(board.alerts),
        "pending_compiles": sorted(board.compiles),
        "templates": sorted(board.templates),
        "agents": sorted(board.agents),
    }
