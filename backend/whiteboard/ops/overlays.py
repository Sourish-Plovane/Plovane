"""Overlay and role commands: switch overlays on or off, and pick a role.

Each person or agent has their own view (``board.views``): a role plus the
overlays they switched on or off on top of it. Changing a view never changes
code or anyone else's view, and it can be undone like any other change.
"""

from __future__ import annotations

from typing import Any

from backend.whiteboard.engine import BOARD_AI, USER, Context
from backend.whiteboard.overlays import active, expand, view_of
from backend.whiteboard.registry import OpError, flag, operation


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


# Input: ctx (Context), person (Any)
# Output: str
# Processing: Whose view a switch changes: the actor's own, or with person the person's (only the whiteboard AI and the person may).
# Changed by: Claude Code
# Date: 2026-11-25
def viewer(ctx: Context, person: Any) -> str:
    if not flag(person):
        return ctx.actor
    if ctx.actor not in (USER, BOARD_AI):
        raise OpError("FORBIDDEN", "only the whiteboard AI may change the person's view")
    return USER


# Input: ctx (Context), overlay (str), turn_on (bool), who (str | None)
# Output: dict[str, Any]
# Processing: Switch an overlay (or every overlay in a group) on or off in one view (the actor's unless who is given).
# Changed by: Claude Code
# Date: 2026-11-25
def switch(ctx: Context, overlay: str, turn_on: bool, who: str | None = None) -> dict[str, Any]:
    oid = known(ctx, overlay)
    who = who or ctx.actor
    view = view_of(ctx.board, who)
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
    ctx.board.views[who] = {"base_role": view["base_role"], "overlays_add": add, "overlays_remove": remove}
    on = active(ctx.board, who)
    return {"overlay": oid, "state": state(ctx, oid, on), "active": on, "view": who}


# Input: ctx (Context), overlay (str), person (Any)
# Output: dict[str, Any]
# Processing: Switch an overlay or group on (in the person's view with person, for the whiteboard AI).
# Changed by: Claude Code
# Date: 2026-11-25
@operation("overlay.on", command="/overlay on", tool="plovane_overlay_on", group="plovane_overlay")
def overlay_on(ctx: Context, overlay: str, person: Any = False) -> dict[str, Any]:
    """Switches an overlay (or every overlay in a group) on in your view; --person: in the person's (whiteboard AI)."""
    return switch(ctx, overlay, True, viewer(ctx, person))


# Input: ctx (Context), overlay (str), person (Any)
# Output: dict[str, Any]
# Processing: Switch an overlay or group off (in the person's view with person, for the whiteboard AI).
# Changed by: Claude Code
# Date: 2026-11-25
@operation("overlay.off", command="/overlay off", tool="plovane_overlay_off", group="plovane_overlay")
def overlay_off(ctx: Context, overlay: str, person: Any = False) -> dict[str, Any]:
    """Switches an overlay (or every overlay in a group) off in your view; --person: in the person's (whiteboard AI)."""
    return switch(ctx, overlay, False, viewer(ctx, person))


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


OVERLAY_SOURCES = ("group", "premade", "custom")
CHANNELS = ("fill", "border", "badge", "chip", "icon", "port", "arrows", "line_highlight")
APPEARANCES = ("hidden", "count", "mark", "detail")


# Input: value (Any), name (str)
# Output: dict[str, Any]
# Processing: A JSON object argument: already data (from MCP), or JSON text (from a command line or .plo file).
# Changed by: Claude Code
# Date: 2026-11-22
def json_object(value: Any, name: str) -> dict[str, Any]:
    from backend.whiteboard.ops.boardfile import parsed

    return parsed(value, name, dict)


# Input: ctx (Context), overlay (str), data (Any)
# Output: dict[str, Any]
# Processing: Define or replace one overlay exactly, after checking its shape (handbook section 21.4).
# Changed by: Claude Code
# Date: 2026-11-22
@operation("overlay.put", command="/overlay put", tool="plovane_overlay_put", group="plovane_overlay", agents=False)
def overlay_put(ctx: Context, overlay: str, data: Any) -> dict[str, Any]:
    """Defines or replaces one overlay (used by .plo files); people only."""
    oid = str(overlay).strip()
    if not oid.replace("_", "").isalnum() or not oid[:1].isalpha() or oid.lower() != oid:
        raise OpError("INVALID_ARGS", "an overlay id is lowercase letters, digits and _, starting with a letter")
    spec = json_object(data, "data")
    if not isinstance(spec.get("label"), str) or not spec["label"].strip():
        raise OpError("INVALID_ARGS", "an overlay needs a label")
    if spec.get("source") not in OVERLAY_SOURCES:
        raise OpError("INVALID_ARGS", f"an overlay's source is one of {list(OVERLAY_SOURCES)}")
    parent = spec.get("parent")
    if parent is not None and ctx.board.overlays.get(parent, {}).get("source") != "group":
        raise OpError("INVALID_ARGS", f"parent {parent!r} must be a group overlay defined before this one")
    if spec["source"] != "group":
        if spec.get("channel") not in CHANNELS:
            raise OpError("INVALID_ARGS", f"an overlay's channel is one of {list(CHANNELS)}")
        appearance = spec.get("appearance")
        if not isinstance(appearance, dict) or any(not str(k).isdigit() or v not in APPEARANCES
                                                   for k, v in appearance.items()):
            raise OpError("INVALID_ARGS", "appearance maps zoom level numbers to hidden, count, mark or detail")
    ctx.board.overlays[oid] = spec
    return {"overlay": oid, "source": spec["source"]}


# Input: ctx (Context), overlay (str)
# Output: dict[str, Any]
# Processing: Remove one overlay and anything inside it; roles and views stop naming them.
# Changed by: Claude Code
# Date: 2026-11-22
@operation("overlay.remove", command="/overlay remove", tool="plovane_overlay_remove", group="plovane_overlay",
           agents=False)
def overlay_remove(ctx: Context, overlay: str) -> dict[str, Any]:
    """Removes an overlay (and the overlays inside a group); people only."""
    oid = known(ctx, overlay)
    gone, frontier = {oid}, [oid]
    while frontier:
        current = frontier.pop()
        for child, spec in ctx.board.overlays.items():
            if spec.get("parent") == current and child not in gone:
                gone.add(child)
                frontier.append(child)
    for name in gone:
        ctx.board.overlays.pop(name, None)
    for role in ctx.board.roles.values():
        role["overlays"] = [o for o in role.get("overlays", []) if o not in gone]
    for view in ctx.board.views.values():
        for key in ("overlays_add", "overlays_remove"):
            view[key] = [o for o in view.get(key, []) if o not in gone]
    return {"removed": sorted(gone)}


# Input: ctx (Context), role (str), data (Any)
# Output: dict[str, Any]
# Processing: Define or replace one role exactly.
# Changed by: Claude Code
# Date: 2026-11-22
@operation("role.put", command="/role put", tool="plovane_role_put", group="plovane_overlay", agents=False)
def role_put(ctx: Context, role: str, data: Any) -> dict[str, Any]:
    """Defines or replaces one role (used by .plo files); people only."""
    rid = str(role).strip()
    spec = json_object(data, "data")
    if not rid or not isinstance(spec.get("label"), str):
        raise OpError("INVALID_ARGS", "a role needs an id and a label")
    names = spec.get("overlays", [])
    if not isinstance(names, list) or not all(isinstance(n, str) for n in names):
        raise OpError("INVALID_ARGS", "a role's overlays is a list of overlay ids")
    unknown = [n for n in names if n not in ctx.board.overlays]
    if unknown:
        raise OpError("INVALID_ARGS", f"role {rid!r} names overlays that don't exist: {unknown}")
    if not isinstance(spec.get("default_level", 0), int):
        raise OpError("INVALID_ARGS", "a role's default_level is a zoom level number")
    ctx.board.roles[rid] = spec
    return {"role": rid, "overlays": names}


# Input: ctx (Context), role (str)
# Output: dict[str, Any]
# Processing: Remove one role; views that used it fall back to the first role.
# Changed by: Claude Code
# Date: 2026-11-22
@operation("role.remove", command="/role remove", tool="plovane_role_remove", group="plovane_overlay", agents=False)
def role_remove(ctx: Context, role: str) -> dict[str, Any]:
    """Removes a role; people only."""
    rid = str(role).strip()
    if rid not in ctx.board.roles:
        raise OpError("NOT_FOUND", f"no role {rid!r}; roles: {sorted(ctx.board.roles)}")
    del ctx.board.roles[rid]
    return {"removed": rid}


# Input: ctx (Context), overlay (str), data (Any), show (Any)
# Output: dict[str, Any]
# Processing: Define or replace a custom overlay as if/else rules over the board and live signals, and switch it on for the person.
# Changed by: Claude Code
# Date: 2026-11-25
@operation("overlay.rule", command="/overlay rule", tool="plovane_overlay_rule", group="plovane_overlay")
def overlay_rule(ctx: Context, overlay: str, data: Any, show: Any = True) -> dict[str, Any]:
    """Writes an overlay as if/else rules (e.g. an arrow turns red when its API call fails) and shows it to the person."""
    from backend.whiteboard.overlay_rules import RuleError, validate

    oid = str(overlay).strip()
    if not oid.replace("_", "").isalnum() or not oid[:1].isalpha() or oid.lower() != oid:
        raise OpError("INVALID_ARGS", "an overlay id is lowercase letters, digits and _, starting with a letter")
    existing = ctx.board.overlays.get(oid)
    if existing is not None and existing.get("source") != "custom":
        raise OpError("CONFLICT", f"{oid!r} is a {existing.get('source')} overlay; pick another id")
    spec = json_object(data, "data")
    label = spec.get("label")
    if not isinstance(label, str) or not label.strip():
        raise OpError("INVALID_ARGS", "a rule overlay needs a label")
    try:
        rules = validate(spec.get("rules"))
    except RuleError as exc:
        raise OpError("INVALID_ARGS", str(exc)) from None
    only_arrows = all(r["on"] == "arrow" for r in rules)
    channel = spec.get("channel") or ("arrows" if only_arrows else "badge")
    if channel not in CHANNELS:
        raise OpError("INVALID_ARGS", f"an overlay's channel is one of {list(CHANNELS)}")
    appearance = spec.get("appearance") or {"1": "count", "2": "count", "3": "mark", "4": "mark", "5": "detail"}
    if not isinstance(appearance, dict) or any(not str(k).isdigit() or v not in APPEARANCES for k, v in appearance.items()):
        raise OpError("INVALID_ARGS", "appearance maps zoom level numbers to hidden, count, mark or detail")
    priority = spec.get("priority", 50)
    if not isinstance(priority, int) or not 0 <= priority <= 100:
        raise OpError("INVALID_ARGS", "priority is a whole number from 0 to 100")
    style = spec.get("style") if isinstance(spec.get("style"), dict) else {}
    ctx.board.overlays[oid] = {
        "label": label.strip()[:80], "source": "custom", "created_by": "user" if ctx.actor == USER else "ai",
        "author": ctx.actor, "channel": channel, "priority": priority, "appearance": appearance,
        "style": {k: str(v)[:40] for k, v in style.items() if k in ("color", "badge", "icon")}, "rules": rules}
    shown = None
    if flag(show) and oid not in active(ctx.board, USER):
        shown = switch(ctx, oid, True, USER)["state"]
    return {"overlay": oid, "rules": len(rules), "channel": channel, "replaced": existing is not None,
            "shown_to_person": shown == "on"}


# Input: ctx (Context)
# Output: dict[str, Any]
# Processing: The premade overlay templates with their descriptions and blanks.
# Changed by: Claude Code
# Date: 2026-11-25
@operation("overlay.templates", command="/overlay template list", tool="plovane_overlay_templates",
           group="plovane_overlay", mutates=False)
def overlay_templates(ctx: Context) -> dict[str, Any]:
    """Lists the premade overlay templates the whiteboard AI fills in for a project (blanks and defaults)."""
    from backend.whiteboard.overlay_templates import listing

    return {"templates": listing()}


# Input: ctx (Context), template (str), id (str | None), set (Any), label (str | None), show (Any)
# Output: dict[str, Any]
# Processing: Fill a premade template's blanks for this project and save it as a rule overlay (shown to the person).
# Changed by: Claude Code
# Date: 2026-11-25
@operation("overlay.from_template", command="/overlay from", tool="plovane_overlay_from", group="plovane_overlay")
def overlay_from(ctx: Context, template: str, id: str | None = None, set: Any = None,  # noqa: A002
                 label: str | None = None, show: Any = True) -> dict[str, Any]:
    """Makes an overlay from a premade template, with its blanks filled in for this project (--set '{...}')."""
    from backend.whiteboard.overlay_templates import instantiate

    given = json_object(set, "set") if set not in (None, "") else {}
    try:
        spec = instantiate(str(template).strip(), given)
    except ValueError as exc:
        raise OpError("INVALID_ARGS", str(exc)) from None
    if label:
        spec["label"] = str(label)
    oid = str(id or template).strip()
    result = ctx.execute("overlay.rule", {"overlay": oid, "data": spec, "show": show})
    ctx.board.overlays[oid]["template"] = str(template).strip()
    return {**result, "template": str(template).strip(), "filled": given}
