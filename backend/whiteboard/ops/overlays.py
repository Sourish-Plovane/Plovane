"""Overlay and role commands: switch overlays on or off, and pick a role.

Each person or agent has their own view (``board.views``): a role plus the
overlays they switched on or off on top of it. Changing a view never changes
code or anyone else's view, and it can be undone like any other change.
"""

from __future__ import annotations

from typing import Any

from backend.whiteboard.engine import Context
from backend.whiteboard.overlays import active, expand, view_of
from backend.whiteboard.registry import OpError, operation


# Input: ctx (Context), overlay (str)
# Output: str
# Processing: An overlay id that exists, or NOT_FOUND listing the ones that do.
# Changed by: Claude Code
# Date: 2026-11-22
def known(ctx: Context, overlay: str) -> str:
    oid = str(overlay).strip()
    if oid not in ctx.board.overlays:
        raise OpError("NOT_FOUND", f"no overlay {oid!r}; overlays: {sorted(ctx.board.overlays)}")
    return oid


# Input: ctx (Context), oid (str), on (list[str])
# Output: str
# Processing: An overlay's state for the panel: on or off, and for a group on, off or mixed.
# Changed by: Claude Code
# Date: 2026-11-22
def state(ctx: Context, oid: str, on: list[str]) -> str:
    members = expand(ctx.board.overlays, [oid])
    if not members:
        return "off"
    lit = sum(m in on for m in members)
    return "on" if lit == len(members) else "off" if lit == 0 else "mixed"


# Input: ctx (Context)
# Output: dict[str, Any]
# Processing: Every overlay with its parent, channel and on/off/mixed state, and the actor's role.
# Changed by: Claude Code
# Date: 2026-11-22
@operation("overlay.list", command="/overlay list", tool="plovane_overlay_list", group="plovane_overlay",
           mutates=False)
def list_overlays(ctx: Context) -> dict[str, Any]:
    """Lists the overlays, which are on, and the current role."""
    on = active(ctx.board, ctx.actor)
    rows = [{"overlay": oid, "label": spec.get("label", oid), "source": spec.get("source"),
             "parent": spec.get("parent"), "channel": spec.get("channel"), "state": state(ctx, oid, on)}
            for oid, spec in ctx.board.overlays.items()]
    return {"role": view_of(ctx.board, ctx.actor)["base_role"], "overlays": rows, "active": on}


# Input: ctx (Context), overlay (str), turn_on (bool)
# Output: dict[str, Any]
# Processing: Switch an overlay (or every overlay in a group) on or off in the actor's view.
# Changed by: Claude Code
# Date: 2026-11-22
def switch(ctx: Context, overlay: str, turn_on: bool) -> dict[str, Any]:
    oid = known(ctx, overlay)
    view = view_of(ctx.board, ctx.actor)
    role = ctx.board.roles.get(view["base_role"] or "", {})
    from_role = set(expand(ctx.board.overlays, list(role.get("overlays", []))))
    add, remove = list(view["overlays_add"]), list(view["overlays_remove"])
    for target in expand(ctx.board.overlays, [oid]):
        if turn_on:
            if target in from_role:
                remove = [r for r in remove if r != target]
            elif target not in add:
                add.append(target)
        else:
            if target in from_role:
                if target not in remove:
                    remove.append(target)
            else:
                add = [a for a in add if a != target]
    ctx.board.views[ctx.actor] = {"base_role": view["base_role"], "overlays_add": add, "overlays_remove": remove}
    on = active(ctx.board, ctx.actor)
    return {"overlay": oid, "state": state(ctx, oid, on), "active": on}


# Input: ctx (Context), overlay (str)
# Output: dict[str, Any]
# Processing: Switch an overlay or group on.
# Changed by: Claude Code
# Date: 2026-11-22
@operation("overlay.on", command="/overlay on", tool="plovane_overlay_on", group="plovane_overlay")
def overlay_on(ctx: Context, overlay: str) -> dict[str, Any]:
    """Switches an overlay (or every overlay in a group) on in your view."""
    return switch(ctx, overlay, True)


# Input: ctx (Context), overlay (str)
# Output: dict[str, Any]
# Processing: Switch an overlay or group off.
# Changed by: Claude Code
# Date: 2026-11-22
@operation("overlay.off", command="/overlay off", tool="plovane_overlay_off", group="plovane_overlay")
def overlay_off(ctx: Context, overlay: str) -> dict[str, Any]:
    """Switches an overlay (or every overlay in a group) off in your view."""
    return switch(ctx, overlay, False)


# Input: ctx (Context)
# Output: dict[str, Any]
# Processing: Every role with its overlays and starting zoom level, and the actor's current role.
# Changed by: Claude Code
# Date: 2026-11-22
@operation("role.list", command="/role list", tool="plovane_role_list", group="plovane_overlay", mutates=False)
def list_roles(ctx: Context) -> dict[str, Any]:
    """Lists the roles and which one you are using."""
    return {"current": view_of(ctx.board, ctx.actor)["base_role"],
            "roles": [{"role": rid, "label": spec.get("label", rid), "overlays": spec.get("overlays", []),
                       "default_level": spec.get("default_level")} for rid, spec in ctx.board.roles.items()]}


# Input: ctx (Context), role (str)
# Output: dict[str, Any]
# Processing: Use a role: its overlays replace the actor's own additions and removals.
# Changed by: Claude Code
# Date: 2026-11-22
@operation("role.use", command="/role use", tool="plovane_role_use", group="plovane_overlay")
def use_role(ctx: Context, role: str) -> dict[str, Any]:
    """Uses a role (developer, cyber, reviewer, ...): shows that role's overlays."""
    rid = str(role).strip()
    if rid not in ctx.board.roles:
        raise OpError("NOT_FOUND", f"no role {rid!r}; roles: {sorted(ctx.board.roles)}")
    ctx.board.views[ctx.actor] = {"base_role": rid, "overlays_add": [], "overlays_remove": []}
    return {"role": rid, "active": active(ctx.board, ctx.actor),
            "default_level": ctx.board.roles[rid].get("default_level")}
