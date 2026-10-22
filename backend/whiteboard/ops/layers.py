"""Layers and zoom: which blocks show at which zoom level."""

from __future__ import annotations

from typing import Any

from backend.whiteboard.engine import Context
from backend.whiteboard.model import Layer
from backend.whiteboard.registry import OpError, integer, items, number, operation, span


# Input: layer (Layer)
# Output: dict[str, Any]
# Processing: A layer as JSON.
# Changed by: Sourish
# Date: 2026-10-22
def layer_record(layer: Layer) -> dict[str, Any]:
    return {"number": layer.number, "name": layer.name, "zoom": [layer.zoom_min, layer.zoom_max],
            "shows": layer.shows}


# Input: ctx (Context), number_ (int), low (float), high (float)
# Output: None
# Processing: Refuse a zoom range that overlaps another layer's.
# Changed by: Sourish
# Date: 2026-10-22
def check_overlap(ctx: Context, number_: int, low: float, high: float) -> None:
    if not 0 <= low < high <= 100:
        raise OpError("INVALID_ARGS", "zoom ranges run from 0 to 100, low below high")
    for other in ctx.board.layers.values():
        if other.number != number_ and low < other.zoom_max and other.zoom_min < high:
            raise OpError("CONFLICT", f"zoom {low:g}-{high:g} overlaps layer {other.number} "
                                      f"({other.zoom_min:g}-{other.zoom_max:g})")


# Input: ctx (Context), level (float)
# Output: Layer
# Processing: The layer whose zoom range contains a level.
# Changed by: Sourish
# Date: 2026-10-22
def layer_at(ctx: Context, level: float) -> Layer:
    for layer in sorted(ctx.board.layers.values(), key=lambda l: l.zoom_min):
        if layer.zoom_min <= level <= layer.zoom_max:
            return layer
    raise OpError("NOT_FOUND", f"no layer covers zoom {level:g}")


# Input: ctx (Context), layer (Layer), zoom (float)
# Output: dict[str, Any]
# Processing: Set the view to a layer and zoom, returning what is visible.
# Changed by: Sourish
# Date: 2026-10-22
def show(ctx: Context, layer: Layer, zoom: float) -> dict[str, Any]:
    ctx.board.view["zoom"], ctx.board.view["layer"] = zoom, layer.number
    visible = [b.id for b in ctx.board.blocks.values() if b.layer == layer.number]
    return {"zoom": zoom, "layer": layer_record(layer), "visible_blocks": visible}


# Input: ctx (Context), number (int), zoom (str), show (str | list | None), name (str | None)
# Output: dict[str, Any]
# Processing: Create a layer with its zoom range and block types.
# Changed by: Sourish
# Date: 2026-10-22
@operation("layer.define", command="/layer define", tool="plovane_layer_define", group="plovane_layer")
def define(ctx: Context, number: Any, zoom: Any, show: Any = None, name: str | None = None) -> dict[str, Any]:
    """Creates a layer with its zoom range and the block types it shows."""
    ctx.require(None)
    n = integer(number, "layer number")
    if n in ctx.board.layers:
        raise OpError("CONFLICT", f"layer {n} exists; change it with /layer edit")
    low, high = span(zoom, "zoom")
    check_overlap(ctx, n, low, high)
    layer = Layer(n, name or f"Layer {n}", low, high, items(show))
    ctx.board.layers[n] = layer
    return layer_record(layer)


# Input: ctx (Context), number (int), zoom (str | None), show (str | list | None)
# Output: dict[str, Any]
# Processing: Change a layer's zoom range or contents.
# Changed by: Sourish
# Date: 2026-10-22
@operation("layer.edit", command="/layer edit", tool="plovane_layer_edit", group="plovane_layer")
def edit(ctx: Context, number: Any, zoom: Any = None, show: Any = None) -> dict[str, Any]:
    """Changes a layer's zoom range or contents."""
    ctx.require(None)
    layer = get(ctx, number)
    if zoom is not None:
        low, high = span(zoom, "zoom")
        check_overlap(ctx, layer.number, low, high)
        layer.zoom_min, layer.zoom_max = low, high
    if show is not None:
        layer.shows = items(show)
    return layer_record(layer)


# Input: ctx (Context)
# Output: dict[str, Any]
# Processing: Every layer with its range, contents and block count.
# Changed by: Sourish
# Date: 2026-10-22
@operation("layer.list", command="/layer list", tool="plovane_layer_list", group="plovane_layer", mutates=False)
def list_layers(ctx: Context) -> dict[str, Any]:
    """Shows all layers with their ranges and contents."""
    return {"layers": [
        layer_record(layer) | {"blocks": sum(b.layer == layer.number for b in ctx.board.blocks.values())}
        for layer in sorted(ctx.board.layers.values(), key=lambda l: l.number)
    ]}


# Input: ctx (Context), number (int), name (str)
# Output: dict[str, Any]
# Processing: Give a layer a readable name.
# Changed by: Sourish
# Date: 2026-10-22
@operation("layer.rename", command="/layer rename", tool="plovane_layer_rename", group="plovane_layer")
def rename(ctx: Context, number: Any, name: str) -> dict[str, Any]:
    """Gives a layer a readable name."""
    ctx.require(None)
    layer = get(ctx, number)
    layer.name = str(name)
    return layer_record(layer)


# Input: ctx (Context), number (int)
# Output: dict[str, Any]
# Processing: Delete a layer, moving its blocks to the nearest layer.
# Changed by: Sourish
# Date: 2026-10-22
@operation("layer.remove", command="/layer remove", tool="plovane_layer_remove", group="plovane_layer",
           confirm=True)
def remove(ctx: Context, number: Any) -> dict[str, Any]:
    """Deletes a layer; its blocks move to the nearest layer."""
    ctx.require(None)
    layer = get(ctx, number)
    others = [n for n in ctx.board.layers if n != layer.number]
    if not others:
        raise OpError("CONFLICT", "the board needs at least one layer")
    nearest = min(others, key=lambda n: (abs(n - layer.number), n))
    moved = [b.id for b in ctx.board.blocks.values() if b.layer == layer.number]
    for block_id in moved:
        ctx.board.blocks[block_id].layer = nearest
    del ctx.board.layers[layer.number]
    if ctx.board.view.get("layer") == layer.number:
        ctx.board.view["layer"] = nearest
    return {"removed": layer.number, "blocks_moved_to": nearest, "moved": moved}


# Input: ctx (Context), number (Any)
# Output: Layer
# Processing: A layer by number, or NOT_FOUND.
# Changed by: Sourish
# Date: 2026-10-22
def get(ctx: Context, number: Any) -> Layer:
    n = integer(number, "layer number")
    if n not in ctx.board.layers:
        raise OpError("NOT_FOUND", f"no layer {n}; /layer list shows them")
    return ctx.board.layers[n]


# Input: ctx (Context), level (float | None), layer (int | None)
# Output: dict[str, Any]
# Processing: Jump to a zoom level or to a layer.
# Changed by: Sourish
# Date: 2026-10-22
@operation("view.zoom", command="/zoom", tool="plovane_zoom", group="plovane_view", mutates=False)
def zoom(ctx: Context, level: Any = None, layer: Any = None) -> dict[str, Any]:
    """Jumps to a zoom level or a layer."""
    if layer is not None:
        target = get(ctx, layer)
        return show(ctx, target, (target.zoom_min + target.zoom_max) / 2)
    if level is None:
        raise OpError("INVALID_ARGS", "give a zoom level (0-100) or layer N")
    value = number(level, "zoom level")
    return show(ctx, layer_at(ctx, value), value)


# Input: ctx (Context), block (str)
# Output: dict[str, Any]
# Processing: Zoom into a block: the next layer down, focused on it.
# Changed by: Sourish
# Date: 2026-10-22
@operation("view.zoom_into", command="/zoom into", tool="plovane_zoom_into", group="plovane_view", mutates=False)
def zoom_into(ctx: Context, block: str) -> dict[str, Any]:
    """Zooms into a specific block."""
    target = ctx.board.block(block)
    ctx.require(target, "read")
    deeper = sorted(n for n in ctx.board.layers if n > target.layer)
    layer = ctx.board.layers[deeper[0]] if deeper else ctx.board.layers[target.layer]
    result = show(ctx, layer, layer.zoom_min + (layer.zoom_max - layer.zoom_min) / 2)
    ctx.board.view["focus"] = target.id
    inside = [b.id for b in ctx.board.descendants(target.id)]
    return result | {"focus": target.id, "inside": inside}


# Input: ctx (Context)
# Output: dict[str, Any]
# Processing: Move up one layer.
# Changed by: Sourish
# Date: 2026-10-22
@operation("view.zoom_out", command="/zoom out", tool="plovane_zoom_out", group="plovane_view", mutates=False)
def zoom_out(ctx: Context) -> dict[str, Any]:
    """Moves up one layer."""
    current = ctx.board.view.get("layer", 1)
    higher = sorted(n for n in ctx.board.layers if n < current)
    if not higher:
        raise OpError("CONFLICT", f"layer {current} is already the top layer")
    layer = ctx.board.layers[higher[-1]]
    ctx.board.view["focus"] = None
    return show(ctx, layer, (layer.zoom_min + layer.zoom_max) / 2)
