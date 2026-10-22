"""Templates: reusable layouts placed in one step, with slots the library fills.

A template stores blocks by offset from its top-left corner, their styles and
the arrows between them. ``{name}`` in a block name is replaced on apply, so
one template makes ``orders-api`` and ``billing-api`` alike. A ``slot`` block
is a placeholder that ``/template fill`` fills with a verified library entry.
"""

from __future__ import annotations

from typing import Any

from whiteboard.engine import Context
from whiteboard.model import LAYER_FOR_KIND, Template
from whiteboard.ops.arrows import new_arrow
from whiteboard.ops.placement import grow_to_fit
from whiteboard.registry import OpError, integer, items, operation, pair


class _Blank(dict):
    """Leaves unknown ``{placeholders}`` as they are instead of failing."""

    # Input: key (str)
    # Output: str
    # Processing: Keep an unknown placeholder unchanged.
    # Changed by: Sourish
    # Date: 2026-10-22
    def __missing__(self, key: str) -> str:
        return "{" + key + "}"


# Input: none
# Output: list[Template]
# Processing: The templates every new board starts with.
# Changed by: Sourish
# Date: 2026-10-22
def builtin_templates() -> list[Template]:
    return [
        Template(
            name="rest-api-service", builtin=True,
            description="A REST service with its database and slots for validation and auth",
            blocks=[
                {"key": "api", "kind": "service", "name": "{name}", "dx": 0, "dy": 0},
                {"key": "db", "kind": "database", "name": "{name}-db", "dx": 320, "dy": 0},
                {"key": "validate", "kind": "slot", "name": "validate-input", "parent": "api", "dx": 20, "dy": 40,
                 "meta": {"query": "validate a request body against a schema"}},
                {"key": "auth", "kind": "slot", "name": "auth-check", "parent": "api", "dx": 200, "dy": 40,
                 "meta": {"query": "check a bearer token in a request header"}},
            ],
            arrows=[{"from": "api", "to": "db", "type": "database"}],
        ),
        Template(
            name="web-app", builtin=True,
            description="Frontend, API and database",
            blocks=[
                {"key": "web", "kind": "service", "name": "{name}-web", "dx": 0, "dy": 0},
                {"key": "api", "kind": "service", "name": "{name}-api", "dx": 320, "dy": 0},
                {"key": "db", "kind": "database", "name": "{name}-db", "dx": 640, "dy": 0},
            ],
            arrows=[{"from": "web", "to": "api", "type": "api"}, {"from": "api", "to": "db", "type": "database"}],
        ),
        Template(
            name="worker-queue", builtin=True,
            description="An API that queues jobs for a background worker",
            blocks=[
                {"key": "api", "kind": "service", "name": "{name}-api", "dx": 0, "dy": 0},
                {"key": "queue", "kind": "queue", "name": "{name}-queue", "dx": 320, "dy": 0},
                {"key": "worker", "kind": "service", "name": "{name}-worker", "dx": 640, "dy": 0},
                {"key": "retry", "kind": "slot", "name": "retry", "parent": "worker", "dx": 20, "dy": 40,
                 "meta": {"query": "retry a failed operation with exponential backoff"}},
            ],
            arrows=[{"from": "api", "to": "queue", "type": "event"}, {"from": "queue", "to": "worker", "type": "event"}],
        ),
    ]


# Input: ctx (Context), template (str)
# Output: Template
# Processing: A template by name, or NOT_FOUND listing them.
# Changed by: Sourish
# Date: 2026-10-22
def get(ctx: Context, template: str) -> Template:
    if template not in ctx.board.templates:
        raise OpError("NOT_FOUND", f"no template {template!r}; templates: {sorted(ctx.board.templates)}")
    return ctx.board.templates[template]


# Input: ctx (Context), template (Template), name (str), at (Any), layer (Any), params (dict)
# Output: list[dict[str, Any]]
# Processing: Where each of a template's blocks would go, without placing them.
# Changed by: Sourish
# Date: 2026-10-22
def plan(ctx: Context, template: Template, name: str, at: Any, layer: Any, params: dict) -> list[dict[str, Any]]:
    x0, y0 = pair(at, "at") if at is not None else (40, 40)
    values = _Blank(name=name, **{k: str(v) for k, v in params.items()})
    planned = []
    for spec in template.blocks:
        top = spec.get("parent") is None
        level = integer(layer, "layer") if layer is not None and top else spec.get(
            "layer", LAYER_FOR_KIND.get(spec["kind"], 1))
        planned.append({
            "key": spec["key"], "kind": spec["kind"], "name": spec["name"].format_map(values),
            "parent": spec.get("parent"), "layer": level,
            "x": x0 + spec["dx"] if top else spec["dx"], "y": y0 + spec["dy"] if top else spec["dy"],
            "style": spec.get("style", {}), "preset": spec.get("preset"), "meta": dict(spec.get("meta", {})),
        })
    return planned


# Input: ctx (Context), name (str), from_layer (Any), blocks (Any), placeholder (str | None), description (str)
# Output: dict[str, Any]
# Processing: Save a layer or a selection as a reusable template.
# Changed by: Sourish
# Date: 2026-10-22
@operation("template.create", command="/template create", tool="plovane_template_create", group="plovane_template")
def create(ctx: Context, name: str, from_layer: Any = None, blocks: Any = None, placeholder: str | None = None,
           description: str = "") -> dict[str, Any]:
    """Saves the current layer or selection as a reusable layout template with placeholders."""
    ctx.require(None)
    if from_layer is not None:
        number = integer(from_layer, "layer")
        chosen = [b for b in ctx.board.blocks.values() if b.layer == number and b.parent is None]
    else:
        chosen = [ctx.board.block(ref) for ref in items(blocks)]
    if not chosen:
        raise OpError("INVALID_ARGS", "give --from-layer N or --blocks a,b,c with at least one block")
    family = {b.id: b for b in chosen}
    for block in chosen:
        for child in ctx.board.descendants(block.id):
            family[child.id] = child
    left = min(b.x for b in chosen)
    top = min(b.y for b in chosen)
    specs = []
    for block in family.values():
        is_top = block.parent not in family
        label = block.name.replace(placeholder, "{name}") if placeholder else block.name
        specs.append({"key": block.id, "kind": block.kind, "name": label,
                      "parent": None if is_top else block.parent,
                      "dx": block.x - left if is_top else block.x, "dy": block.y - top if is_top else block.y,
                      "layer": block.layer, "style": dict(block.style), "preset": block.preset,
                      "meta": {k: v for k, v in block.meta.items() if k in ("query",)}})
    arrows = [{"from": a.source, "to": a.target, "type": a.type} for a in ctx.board.arrows.values()
              if a.source in family and a.target in family]
    ctx.board.templates[name] = Template(name=name, description=description, blocks=specs, arrows=arrows)
    return {"template": name, "blocks": len(specs), "arrows": len(arrows),
            "slots": [s["key"] for s in specs if s["kind"] == "slot"]}


# Input: ctx (Context), template (str), name (str), layer (Any), at (Any), params (dict | None)
# Output: dict[str, Any]
# Processing: Place a template's blocks, styles and arrows in one step.
# Changed by: Sourish
# Date: 2026-10-22
@operation("template.apply", command="/template apply", tool="plovane_template_apply", group="plovane_template")
def apply(ctx: Context, template: str, name: str = "app", layer: Any = None, at: Any = None,
          params: dict | None = None) -> dict[str, Any]:
    """Places a template's blocks, positions, styles, and connections in one step."""
    chosen = get(ctx, template)
    ctx.require(None)
    if any(b.meta.get("instance") == name for b in ctx.board.blocks.values()):
        raise OpError("CONFLICT", f"an applied template is already called {name!r}")
    ids: dict[str, str] = {}
    for spec in plan(ctx, chosen, name, at, layer, params or {}):
        block = ctx.new_block(spec["kind"], spec["name"], layer=spec["layer"],
                              parent=ids.get(spec["parent"]) if spec["parent"] else None,
                              meta=spec["meta"] | {"template": chosen.name, "instance": name},
                              status="empty" if spec["kind"] == "slot" else "built")
        block.x, block.y = ctx.snap(spec["x"]), ctx.snap(spec["y"])
        block.style = dict(spec["style"])
        block.preset = spec["preset"] or block.preset
        ids[spec["key"]] = block.id
    for block_id in ids.values():
        grow_to_fit(ctx, ctx.board.blocks[block_id])
    arrows = []
    for spec in chosen.arrows:
        if spec["from"] in ids and spec["to"] in ids:
            arrow = new_arrow(ctx, ctx.board.blocks[ids[spec["from"]]], ctx.board.blocks[ids[spec["to"]]],
                              spec["type"], status="planned" if spec["type"] == "api" else "built")
            arrows.append(arrow.id)
    return {"template": chosen.name, "instance": name, "blocks": list(ids.values()), "arrows": arrows}


# Input: ctx (Context), layer (Any)
# Output: dict[str, Any]
# Processing: The templates, optionally only those with blocks on a layer.
# Changed by: Sourish
# Date: 2026-10-22
@operation("template.list", command="/template list", tool="plovane_template_list", group="plovane_template",
           mutates=False)
def list_templates(ctx: Context, layer: Any = None) -> dict[str, Any]:
    """Shows available templates."""
    wanted = None
    if layer is not None:
        text = str(layer).lower()
        matches = [l.number for l in ctx.board.layers.values() if str(l.number) == text or text in l.name.lower()]
        if not matches:
            raise OpError("NOT_FOUND", f"no layer matches {layer!r}")
        wanted = set(matches)
    found = []
    for template in ctx.board.templates.values():
        layers = {b.get("layer", LAYER_FOR_KIND.get(b["kind"], 1)) for b in template.blocks}
        if wanted is None or layers & wanted:
            found.append({"name": template.name, "description": template.description, "builtin": template.builtin,
                          "blocks": len(template.blocks), "layers": sorted(layers),
                          "slots": [b["name"] for b in template.blocks if b["kind"] == "slot"]})
    return {"templates": found}


# Input: ctx (Context), template (str), name (str), at (Any), layer (Any), params (dict | None)
# Output: dict[str, Any]
# Processing: What a template would place, without placing it.
# Changed by: Sourish
# Date: 2026-10-22
@operation("template.preview", command="/template preview", tool="plovane_template_preview",
           group="plovane_template", mutates=False)
def preview(ctx: Context, template: str, name: str = "app", at: Any = None, layer: Any = None,
            params: dict | None = None) -> dict[str, Any]:
    """Shows what a template would place without placing it."""
    chosen = get(ctx, template)
    return {"template": chosen.name, "would_place": plan(ctx, chosen, name, at, layer, params or {}),
            "arrows": chosen.arrows}


# Input: ctx (Context), template (str), add_slot (str | None), slot_query (str | None), parent (str | None), remove_slot (str | None), rename (str | None), description (str | None)
# Output: dict[str, Any]
# Processing: Change a template's slots, name or description.
# Changed by: Sourish
# Date: 2026-10-22
@operation("template.edit", command="/template edit", tool="plovane_template_edit", group="plovane_template")
def edit(ctx: Context, template: str, add_slot: str | None = None, slot_query: str | None = None,
         parent: str | None = None, remove_slot: str | None = None, rename: str | None = None,
         description: str | None = None) -> dict[str, Any]:
    """Changes a template's blocks or slots."""
    ctx.require(None)
    chosen = get(ctx, template)
    keys = {b["key"] for b in chosen.blocks}
    if add_slot:
        if add_slot in keys:
            raise OpError("CONFLICT", f"{template} already has {add_slot!r}")
        if parent and parent not in keys:
            raise OpError("NOT_FOUND", f"{template} has no block {parent!r}; blocks: {sorted(keys)}")
        top = parent or next((b["key"] for b in chosen.blocks if b.get("parent") is None), None)
        siblings = [b for b in chosen.blocks if b.get("parent") == top]
        chosen.blocks.append({"key": add_slot, "kind": "slot", "name": add_slot, "parent": top,
                              "dx": 20 + 180 * len(siblings), "dy": 40,
                              "meta": {"query": slot_query or add_slot.replace("-", " ")}})
    if remove_slot:
        slot = next((b for b in chosen.blocks if b["key"] == remove_slot and b["kind"] == "slot"), None)
        if slot is None:
            raise OpError("NOT_FOUND", f"{template} has no slot {remove_slot!r}")
        chosen.blocks.remove(slot)
        chosen.arrows = [a for a in chosen.arrows if remove_slot not in (a["from"], a["to"])]
    if description is not None:
        chosen.description = description
    if rename:
        if rename in ctx.board.templates:
            raise OpError("CONFLICT", f"a template is already called {rename!r}")
        del ctx.board.templates[chosen.name]
        chosen.name = rename
        ctx.board.templates[rename] = chosen
    chosen.builtin = False
    return {"template": chosen.name, "blocks": [b["key"] for b in chosen.blocks],
            "slots": [b["key"] for b in chosen.blocks if b["kind"] == "slot"]}


# Input: ctx (Context), instance (str), language (str | None), from_library (Any)
# Output: dict[str, Any]
# Processing: Fill an applied template's empty slots with library entries.
# Changed by: Sourish
# Date: 2026-10-22
@operation("template.fill", command="/template fill", tool="plovane_template_fill", group="plovane_template")
def fill(ctx: Context, instance: str, language: str | None = None, from_library: Any = True) -> dict[str, Any]:
    """Fills a template's empty slots with verified library entries."""
    slots = [b for b in ctx.board.blocks.values()
             if b.meta.get("instance") == instance and b.kind == "slot" and not b.meta.get("library")]
    if not any(b.meta.get("instance") == instance for b in ctx.board.blocks.values()):
        raise OpError("NOT_FOUND", f"no applied template called {instance!r}")
    engine = ctx.library()
    filled = []
    for slot in slots:
        ctx.require(slot)
        query = slot.meta.get("query") or slot.name.replace("-", " ")
        found = engine.search(query, language=language, k=1)["data"]["recommendations"]
        if not found:
            filled.append({"slot": slot.id, "entry": None})
            continue
        best = found[0]
        slot.meta |= {"library": best["block_id"], "suggestion": best["name"], "license": best["license"]}
        slot.status = "filled"
        filled.append({"slot": slot.id, "entry": best["block_id"], "name": best["name"],
                       "next": f"/insert {best['block_id']} --into FOLDER to copy its code"})
    return {"instance": instance, "filled": filled}
