"""Readable whiteboards: text size, colour and labels people can read, on their own theme.

The browser is the one place that sees real pixels and real colours, so it
measures: every label's size on screen, its contrast against what is behind
it, and whether it fits. It reports that with ``/readable report`` and picks
text colours itself, a neutral tinted toward the person's accent that keeps
contrast on their theme. The backend keeps what the person (or the whiteboard
AI) chose and decides fixes:

    /readable set --min-size 13 --contrast AA|AAA     the person's targets (their view)
    /theme accent #7357E8 --mode dark                the person's theme; neutrals are worked out for it
    /readable check [target]                         problems from the last measurement
    /readable fix [target]                           scale small text, auto-colour low contrast, wrap what overflows
    /text size|color|weight|wrap <target> <value>    per block, kind:<kind>, layer:<n> or all
    /text shorten <block> --to "short label"         the whiteboard AI writes it; the full name stays as the tooltip

Contrast follows WCAG: 4.5:1 for AA, 7:1 for AAA.
"""

from __future__ import annotations

import time
from typing import Any

from backend.whiteboard.engine import USER, Context
from backend.whiteboard.model import HEX, pinned
from backend.whiteboard.registry import OpError, integer, operation

CONTRAST = {"AA": 4.5, "AAA": 7.0}
DEFAULTS = {"min_size": 12, "contrast": "AA"}
MIN_SIZE_RANGE = (9, 24)
SCALE_RANGE = (0.5, 3.0)
MAX_SHORT = 40
LONG_LABEL = 28
PAGE = {"light": "#FFFFFF", "dark": "#1E1F24"}
INK = {"light": "#1F2328", "dark": "#E8EAED"}


# Input: color (str)
# Output: tuple[float, float, float]
# Processing: A #RRGGBB colour as 0..1 channels.
# Changed by: Claude Code
# Date: 2026-11-26
def channels(color: str) -> tuple[float, float, float]:
    value = color.lstrip("#")
    return tuple(int(value[i:i + 2], 16) / 255 for i in (0, 2, 4))  # type: ignore[return-value]


# Input: color (str)
# Output: float
# Processing: WCAG relative luminance of a colour.
# Changed by: Claude Code
# Date: 2026-11-26
def luminance(color: str) -> float:
    lin = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in channels(color)]
    return 0.2126 * lin[0] + 0.7152 * lin[1] + 0.0722 * lin[2]


# Input: a (str), b (str)
# Output: float
# Processing: WCAG contrast ratio between two colours (1 to 21).
# Changed by: Claude Code
# Date: 2026-11-26
def contrast(a: str, b: str) -> float:
    high, low = sorted((luminance(a), luminance(b)), reverse=True)
    return (high + 0.05) / (low + 0.05)


# Input: a (str), b (str), t (float)
# Output: str
# Processing: Blend two colours, t of the way from a to b.
# Changed by: Claude Code
# Date: 2026-11-26
def mix(a: str, b: str, t: float) -> str:
    return "#" + "".join(f"{round((x + (y - x) * t) * 255):02X}" for x, y in zip(channels(a), channels(b)))


# Input: accent (str | None), mode (str)
# Output: dict[str, str]
# Processing: Neutral text colours for a theme: ink, muted and inverse, tinted toward the accent only as far as contrast allows.
# Changed by: Claude Code
# Date: 2026-11-26
def neutrals(accent: str | None, mode: str) -> dict[str, str]:
    page, ink = PAGE[mode], INK[mode]
    other = "dark" if mode == "light" else "light"
    out = {"page": page}
    for name, base, target, against in (("text", ink, 7.0, page), ("muted", mix(ink, page, 0.35), 4.5, page),
                                        ("inverse", INK[other], 7.0, PAGE[other])):
        chosen = base
        if accent:
            for tint in (0.18, 0.12, 0.06):
                candidate = mix(base, accent, tint)
                if contrast(candidate, against) >= target:
                    chosen = candidate
                    break
        out[name] = chosen
    return out


# Input: board (Board), actor (str)
# Output: dict[str, Any]
# Processing: An actor's readability targets and theme, with defaults filled in, for the browser and for checks.
# Changed by: Claude Code
# Date: 2026-11-26
def settings(board: Any, actor: str) -> dict[str, Any]:
    view = board.views.get(actor) or {}
    appearance = dict(view.get("appearance") or {})
    mode = appearance.get("mode") if appearance.get("mode") in PAGE else "light"
    appearance = {"mode": mode, "accent": appearance.get("accent"), **neutrals(appearance.get("accent"), mode)}
    return {**DEFAULTS, **(view.get("readable") or {}), "appearance": appearance}


# Input: ctx (Context), key (str), value (Any)
# Output: None
# Processing: Store one entry in the actor's own view, keeping its overlays and role.
# Changed by: Claude Code
# Date: 2026-11-26
def put_view(ctx: Context, key: str, value: Any) -> None:
    view = dict(ctx.board.views.get(ctx.actor) or {})
    view[key] = value
    ctx.board.views[ctx.actor] = view


# Input: ctx (Context), target (str)
# Output: list[Block]
# Processing: The blocks a target names: one block, kind:<kind>, layer:<n>, or all.
# Changed by: Claude Code
# Date: 2026-11-26
def targets(ctx: Context, target: str) -> list[Any]:
    text = str(target or "").strip()
    blocks = list(ctx.board.blocks.values())
    if text in ("", "all"):
        return [b for b in blocks if b.kind != "agent"]
    if text.startswith("kind:"):
        found = [b for b in blocks if b.kind == text[5:]]
    elif text.startswith("layer:"):
        found = [b for b in blocks if str(b.layer) == text[6:]]
    else:
        return [ctx.board.block(text)]
    if not found:
        raise OpError("NOT_FOUND", f"no blocks match {text!r}")
    return found


# Input: ctx (Context), blocks (list[Block]), key (str), value (Any)
# Output: list[str]
# Processing: Set (or with None remove) one text style key on blocks the actor may change; a block whose style the person pinned is left alone for the AI.
# Changed by: Claude Code
# Date: 2026-11-26
def set_text_style(ctx: Context, blocks: list[Any], key: str, value: Any) -> list[str]:
    changed = []
    for block in blocks:
        if ctx.actor != USER and pinned(block, "style"):
            continue
        ctx.require(block)
        if value is None:
            if block.style.pop(key, None) is not None:
                changed.append(block.id)
        elif block.style.get(key) != value:
            block.style[key] = value
            changed.append(block.id)
    return changed


# Input: ctx (Context), target (str), size (Any)
# Output: dict[str, Any]
# Processing: Scale blocks' text (auto removes the scale and lets the board size it).
# Changed by: Claude Code
# Date: 2026-11-26
@operation("text.size", command="/text size", tool="plovane_text_size", group="plovane_style")
def text_size(ctx: Context, target: str, size: Any) -> dict[str, Any]:
    """Makes a block's text bigger or smaller (a scale like 1.4), or auto; target: a block, kind:<kind>, layer:<n> or all."""
    if str(size).strip().lower() == "auto":
        value = None
    else:
        try:
            value = round(float(size), 2)
        except (TypeError, ValueError):
            raise OpError("INVALID_ARGS", "size is auto or a scale such as 1.3") from None
        if not SCALE_RANGE[0] <= value <= SCALE_RANGE[1]:
            raise OpError("INVALID_ARGS", f"a text scale is {SCALE_RANGE[0]} to {SCALE_RANGE[1]}")
    return {"changed": set_text_style(ctx, targets(ctx, target), "text_scale", value), "size": value or "auto"}


# Input: ctx (Context), target (str), color (str)
# Output: dict[str, Any]
# Processing: Set blocks' text colour, or auto so the browser picks a readable neutral for the theme.
# Changed by: Claude Code
# Date: 2026-11-26
@operation("text.color", command="/text color", tool="plovane_text_color", group="plovane_style")
def text_color(ctx: Context, target: str, color: str) -> dict[str, Any]:
    """Sets a block's text colour (#RRGGBB), or auto: a neutral that stays readable on the person's theme."""
    text = str(color).strip()
    if text.lower() == "auto":
        value = None
    elif HEX.match(text):
        value = text.upper()
    else:
        raise OpError("INVALID_ARGS", "color is auto or #RRGGBB")
    return {"changed": set_text_style(ctx, targets(ctx, target), "text_color", value), "color": value or "auto"}


# Input: ctx (Context), target (str), weight (str)
# Output: dict[str, Any]
# Processing: Set blocks' text weight.
# Changed by: Claude Code
# Date: 2026-11-26
@operation("text.weight", command="/text weight", tool="plovane_text_weight", group="plovane_style")
def text_weight(ctx: Context, target: str, weight: str) -> dict[str, Any]:
    """Makes a block's text normal or bold."""
    if weight not in ("normal", "bold"):
        raise OpError("INVALID_ARGS", "weight is normal or bold")
    value = None if weight == "normal" else "bold"
    return {"changed": set_text_style(ctx, targets(ctx, target), "text_weight", value), "weight": weight}


# Input: ctx (Context), target (str), lines (Any)
# Output: dict[str, Any]
# Processing: Let blocks' labels wrap onto up to this many lines (1 keeps one line).
# Changed by: Claude Code
# Date: 2026-11-26
@operation("text.wrap", command="/text wrap", tool="plovane_text_wrap", group="plovane_style")
def text_wrap(ctx: Context, target: str, lines: Any) -> dict[str, Any]:
    """Lets a block's label wrap onto up to 1-3 lines instead of being cut off."""
    count = integer(lines, "lines")
    if not 1 <= count <= 3:
        raise OpError("INVALID_ARGS", "lines is 1 to 3")
    return {"changed": set_text_style(ctx, targets(ctx, target), "text_wrap", None if count == 1 else count), "lines": count}


# Input: ctx (Context), block (str), to (str | None), clear (Any)
# Output: dict[str, Any]
# Processing: Show a shorter label on a block (the full name stays as its tooltip), or clear it.
# Changed by: Claude Code
# Date: 2026-11-26
@operation("text.shorten", command="/text shorten", tool="plovane_text_shorten", group="plovane_style")
def text_shorten(ctx: Context, block: str, to: str | None = None, clear: Any = False) -> dict[str, Any]:
    """Shows a shorter label on a block, written by the whiteboard AI or the person; the full name stays as the tooltip."""
    from backend.whiteboard.registry import flag

    target = ctx.board.block(block)
    ctx.require(target)
    if ctx.actor != USER and pinned(target, "name"):
        raise OpError("FORBIDDEN", f"the person pinned {target.id}'s name")
    if flag(clear):
        target.meta.pop("short_name", None)
        return {"block": target.id, "short": None}
    label = " ".join(str(to or "").split())
    if not label or len(label) > MAX_SHORT:
        raise OpError("INVALID_ARGS", f"give the short label with --to (1 to {MAX_SHORT} characters)")
    target.meta["short_name"] = label
    if ctx.actor == USER:
        target.meta["pinned"] = sorted(set(target.meta.get("pinned") or ()) | {"name"})
    return {"block": target.id, "short": label}


# Input: ctx (Context), min_size (Any), contrast (str | None)
# Output: dict[str, Any]
# Processing: Set the actor's own readability targets.
# Changed by: Claude Code
# Date: 2026-11-26
@operation("readable.set", command="/readable set", tool="plovane_readable_set", group="plovane_style", mutates=False)
def readable_set(ctx: Context, min_size: Any = None, contrast: str | None = None) -> dict[str, Any]:
    """Sets your readability targets: the smallest text size on screen and the contrast level (AA or AAA)."""
    current = dict((ctx.board.views.get(ctx.actor) or {}).get("readable") or {})
    if min_size is not None:
        size = integer(min_size, "min_size")
        if not MIN_SIZE_RANGE[0] <= size <= MIN_SIZE_RANGE[1]:
            raise OpError("INVALID_ARGS", f"min_size is {MIN_SIZE_RANGE[0]} to {MIN_SIZE_RANGE[1]} pixels")
        current["min_size"] = size
    if contrast is not None:
        if contrast.upper() not in CONTRAST:
            raise OpError("INVALID_ARGS", "contrast is AA or AAA")
        current["contrast"] = contrast.upper()
    put_view(ctx, "readable", current)
    return settings(ctx.board, ctx.actor)


# Input: ctx (Context), color (str), mode (str | None)
# Output: dict[str, Any]
# Processing: Record the actor's theme colour (and light or dark mode) and return the neutral text colours worked out for it.
# Changed by: Claude Code
# Date: 2026-11-26
@operation("theme.accent", command="/theme accent", tool="plovane_theme_accent", group="plovane_style", mutates=False)
def theme_accent(ctx: Context, color: str, mode: str | None = None) -> dict[str, Any]:
    """Records your theme colour (and light or dark mode); text colours are neutralised to stay readable on it."""
    text = str(color).strip()
    if not HEX.match(text):
        raise OpError("INVALID_ARGS", "color is #RRGGBB")
    current = dict((ctx.board.views.get(ctx.actor) or {}).get("appearance") or {})
    current["accent"] = text.upper()
    if mode is not None:
        if mode not in PAGE:
            raise OpError("INVALID_ARGS", "mode is light or dark")
        current["mode"] = mode
    put_view(ctx, "appearance", current)
    return settings(ctx.board, ctx.actor)["appearance"]


# Input: ctx (Context), data (Any)
# Output: dict[str, Any]
# Processing: Keep the browser's latest measurement of what is hard to read (in memory, per person).
# Changed by: Claude Code
# Date: 2026-11-26
@operation("readable.report", command="/readable report", tool="plovane_readable_report", group="plovane_style",
           mutates=False, agents=False)
def readable_report(ctx: Context, data: Any) -> dict[str, Any]:
    """Records what the browser measured as hard to read (used by /readable check and fix); people only."""
    from backend.whiteboard.ops.live import live_of
    from backend.whiteboard.ops.overlays import json_object

    record = json_object(data, "data")
    issues = []
    for issue in record.get("issues") or []:
        if not isinstance(issue, dict) or issue.get("block") not in ctx.board.blocks:
            continue
        if issue.get("kind") not in ("small", "contrast", "overflow"):
            continue
        issues.append({k: issue[k] for k in ("block", "kind", "px", "ratio", "overflow") if k in issue})
    live_of(ctx.ws).setdefault("legibility", {})[ctx.actor] = {"at": time.time(), "issues": issues[:500]}
    return {"issues": len(issues)}


# Input: ctx (Context), target (str)
# Output: list[dict[str, Any]]
# Processing: Problems for a target: the person's last browser measurement plus labels too long to read at a glance.
# Changed by: Claude Code
# Date: 2026-11-26
def problems(ctx: Context, target: str = "") -> list[dict[str, Any]]:
    from backend.whiteboard.ops.live import live_of

    wanted = {b.id for b in targets(ctx, target)}
    found = [i for i in (live_of(ctx.ws).get("legibility", {}).get(USER) or {}).get("issues", []) if i["block"] in wanted]
    for block_id in sorted(wanted):
        block = ctx.board.blocks[block_id]
        if len(block.name) > LONG_LABEL and not block.meta.get("short_name"):
            found.append({"block": block_id, "kind": "long_label", "chars": len(block.name)})
    return found


# Input: ctx (Context), target (str)
# Output: dict[str, Any]
# Processing: What is hard to read, from the last browser measurement and label lengths, without changing anything.
# Changed by: Claude Code
# Date: 2026-11-26
@operation("readable.check", command="/readable check", tool="plovane_readable_check", group="plovane_style",
           mutates=False)
def readable_check(ctx: Context, target: str = "") -> dict[str, Any]:
    """Lists text that is too small, too faint, cut off or too long to read, from the person's last view of the board."""
    from backend.whiteboard.ops.live import live_of

    found = problems(ctx, target)
    measured = (live_of(ctx.ws).get("legibility", {}).get(USER) or {}).get("at")
    return {"problems": found, "measured": bool(measured),
            "age_seconds": round(time.time() - measured) if measured else None,
            "targets": {k: v for k, v in settings(ctx.board, USER).items() if k != "appearance"}}


# Input: ctx (Context), target (str)
# Output: dict[str, Any]
# Processing: Fix what /readable check finds: scale small text up, hand low contrast to automatic colour, let cut-off labels wrap onto two lines; labels still too long are listed for /text shorten.
# Changed by: Claude Code
# Date: 2026-11-26
@operation("readable.fix", command="/readable fix", tool="plovane_readable_fix", group="plovane_style")
def readable_fix(ctx: Context, target: str = "") -> dict[str, Any]:
    """Fixes hard-to-read text: bigger where too small, auto colour where too faint, wrapped where cut off; lists labels to shorten."""
    wanted = settings(ctx.board, USER)
    fixed: list[dict[str, Any]] = []
    shorten: list[str] = []
    for issue in problems(ctx, target):
        block = ctx.board.blocks[issue["block"]]
        if ctx.actor != USER and pinned(block, "style") and issue["kind"] in ("small", "contrast"):
            continue
        if issue["kind"] == "small" and issue.get("px"):
            scale = float(block.style.get("text_scale", 1))
            new = round(min(SCALE_RANGE[1], scale * wanted["min_size"] / max(float(issue["px"]), 1)), 2)
            if new > scale:
                ctx.require(block)
                block.style["text_scale"] = new
                fixed.append({"block": block.id, "fix": f"text x{new}"})
        elif issue["kind"] == "contrast" and "text_color" in block.style:
            ctx.require(block)
            block.style.pop("text_color")
            fixed.append({"block": block.id, "fix": "automatic text colour"})
        elif issue["kind"] == "overflow":
            if block.style.get("text_wrap") or (ctx.actor != USER and pinned(block, "style")):
                shorten.append(block.id)
            else:
                ctx.require(block)
                block.style["text_wrap"] = 2
                fixed.append({"block": block.id, "fix": "wraps onto two lines"})
        elif issue["kind"] == "long_label":
            shorten.append(block.id)
    return {"fixed": fixed, "needs_short_label": sorted(set(shorten)),
            "hint": "write each a short label with /text shorten <block> --to \"...\"" if shorten else ""}
