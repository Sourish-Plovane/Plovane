"""Style: how blocks look. Presets first, raw flags when needed."""

from __future__ import annotations

from typing import Any

from whiteboard.engine import Context
from whiteboard.model import BLOCK_STYLE, THEMES, validate_style
from whiteboard.registry import OpError, flag, operation


# Input: values (dict[str, Any])
# Output: dict[str, Any]
# Processing: Only the style flags that were actually given.
# Changed by: Sourish
# Date: 2026-10-22
def given(values: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in values.items() if v is not None}


# Input: ctx (Context), block (str), preset (str | None), clear (Any), fill, border, border_width, border_style, radius, shape, shadow, icon, label_size, opacity (Any)
# Output: dict[str, Any]
# Processing: Set style flags or a preset on a block.
# Changed by: Sourish
# Date: 2026-10-22
@operation("style", command="/style", tool="plovane_style", group="plovane_style")
def style(ctx: Context, block: str, preset: str | None = None, clear: Any = False, fill: Any = None,
          border: Any = None, border_width: Any = None, border_style: Any = None, radius: Any = None,
          shape: Any = None, shadow: Any = None, icon: Any = None, label_size: Any = None,
          opacity: Any = None) -> dict[str, Any]:
    """Sets visual properties on a block, or applies a saved preset."""
    target = ctx.board.block(block)
    ctx.require(target)
    if flag(clear):
        target.style = {}
    if preset is not None:
        if preset not in ctx.board.presets:
            raise OpError("NOT_FOUND", f"no preset {preset!r}; presets: {sorted(ctx.board.presets)}")
        target.preset = preset
    flags = given({"fill": fill, "border": border, "border_width": border_width, "border_style": border_style,
                   "radius": radius, "shape": shape, "shadow": shadow, "icon": icon,
                   "label_size": label_size, "opacity": opacity})
    target.style |= validate_style(flags, BLOCK_STYLE)
    if preset is None and not flags and not flag(clear):
        raise OpError("INVALID_ARGS", "give --preset NAME or style flags such as --fill #E8F0FE")
    return {"block": target.id, "preset": target.preset, "style": ctx.board.resolved_style(target)}


# Input: ctx (Context), name (str), fill, border, border_width, border_style, radius, shape, shadow, icon, label_size, opacity (Any)
# Output: dict[str, Any]
# Processing: Save a named style for reuse.
# Changed by: Sourish
# Date: 2026-10-22
@operation("preset.define", command="/preset define", tool="plovane_preset_define", group="plovane_style")
def define_preset(ctx: Context, name: str, fill: Any = None, border: Any = None, border_width: Any = None,
                  border_style: Any = None, radius: Any = None, shape: Any = None, shadow: Any = None,
                  icon: Any = None, label_size: Any = None, opacity: Any = None) -> dict[str, Any]:
    """Saves a named style for reuse."""
    ctx.require(None)
    flags = given({"fill": fill, "border": border, "border_width": border_width, "border_style": border_style,
                   "radius": radius, "shape": shape, "shadow": shadow, "icon": icon,
                   "label_size": label_size, "opacity": opacity})
    if not flags:
        raise OpError("INVALID_ARGS", "a preset needs at least one style flag")
    ctx.board.presets[str(name)] = validate_style(flags, BLOCK_STYLE)
    users = [b.id for b in ctx.board.blocks.values() if b.preset == name]
    return {"preset": name, "style": ctx.board.presets[name], "blocks_using_it": users}


# Input: ctx (Context), name (str)
# Output: dict[str, Any]
# Processing: Switch the board's colour theme.
# Changed by: Sourish
# Date: 2026-10-22
@operation("theme", command="/theme", tool="plovane_theme", group="plovane_style")
def theme(ctx: Context, name: str) -> dict[str, Any]:
    """Changes all colors at once."""
    ctx.require(None)
    if name not in THEMES:
        raise OpError("INVALID_ARGS", f"themes: {list(THEMES)}")
    ctx.board.view["theme"] = name
    return {"theme": name}
