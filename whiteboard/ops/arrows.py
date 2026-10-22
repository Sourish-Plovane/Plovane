"""Arrows and connections: typed links between blocks, backed by code where possible.

An ``import`` arrow writes the import statement; an ``api`` arrow to an
endpoint writes the client call (see ``endpoints.py``). Other types record
the design: a database or event link the code does not show yet.
"""

from __future__ import annotations

from collections import deque
from typing import Any

from whiteboard import codegen
from whiteboard.engine import Context
from whiteboard.model import ARROW_STYLE, ARROW_TYPE_STYLE, ARROW_TYPES, Arrow, Block, validate_style
from whiteboard.ops.project import importers_of, imports_of
from whiteboard.registry import OpError, flag, integer, operation


# Input: ctx (Context), source (Block), target (Block), type_ (str), endpoint (str | None), status (str)
# Output: Arrow
# Processing: Add an arrow with a fresh id.
# Changed by: Sourish
# Date: 2026-10-22
def new_arrow(ctx: Context, source: Block, target: Block, type_: str, endpoint: str | None = None,
              status: str = "built") -> Arrow:
    if type_ not in ARROW_TYPES:
        raise OpError("INVALID_ARGS", f"arrow type is one of {list(ARROW_TYPES)}")
    arrow = Arrow(id=ctx.board.new_id(f"{source.id}-to-{target.id}", pool=ctx.board.arrows),
                  source=source.id, target=target.id, type=type_, endpoint=endpoint, status=status)
    ctx.board.arrows[arrow.id] = arrow
    return arrow


# Input: ctx (Context), source (str), target (str), type_ (str | None)
# Output: Arrow
# Processing: The one arrow between two blocks, optionally of one type.
# Changed by: Sourish
# Date: 2026-10-22
def find_arrow(ctx: Context, source: str, target: str, type_: str | None = None) -> Arrow:
    a, b = ctx.board.block(source), ctx.board.block(target)
    found = [x for x in ctx.board.arrows.values()
             if x.source == a.id and x.target == b.id and (type_ is None or x.type == type_)]
    if not found:
        raise OpError("NOT_FOUND", f"no arrow {a.id} -> {b.id}")
    if len(found) > 1:
        raise OpError("AMBIGUOUS", f"{len(found)} arrows {a.id} -> {b.id}; give --type")
    return found[0]


# Input: block (Block)
# Output: str | None
# Processing: The file a file or function block's code lives in.
# Changed by: Sourish
# Date: 2026-10-22
def file_of(block: Block) -> str | None:
    return block.path if block.kind == "file" else block.meta.get("file")


# Input: arrow (Arrow)
# Output: dict[str, Any]
# Processing: An arrow as JSON with its resolved style.
# Changed by: Sourish
# Date: 2026-10-22
def arrow_record(arrow: Arrow) -> dict[str, Any]:
    style = dict(ARROW_TYPE_STYLE.get(arrow.type, {})) | arrow.style
    if arrow.status == "planned":
        style["dash"] = style.get("dash") or 6
    if arrow.status == "failing":
        style["color"] = "#D93025"
    return {"id": arrow.id, "from": arrow.source, "to": arrow.target, "type": arrow.type,
            "endpoint": arrow.endpoint, "status": arrow.status, "style": style, "meta": arrow.meta}


# Input: ctx (Context), source (str), target (str), type (str), endpoint (str | None), request (Any), curve, color, label, width, dash (Any)
# Output: dict[str, Any]
# Processing: Draw a typed arrow and write the code it stands for.
# Changed by: Sourish
# Date: 2026-10-22
@operation("connect", command="/connect", tool="plovane_connect", group="plovane_connect")
def connect(ctx: Context, source: str, target: str, type: str = "calls", endpoint: str | None = None,
            request: Any = None, curve: Any = None, color: Any = None, label: Any = None, width: Any = None,
            dash: Any = None) -> dict[str, Any]:
    """Draws a typed arrow; creates or links real code. Unbuilt endpoints show as dashed "planned"."""
    a, b = ctx.board.block(source), ctx.board.block(target)
    ctx.require(a)
    ctx.require(b, "read")
    style = validate_style({k: v for k, v in {"curve": curve, "color": color, "label": label, "width": width,
                                               "dash": dash}.items() if v is not None}, ARROW_STYLE)
    if type == "api" and endpoint:
        ref = endpoint if ":" in endpoint else f"{b.id}:{endpoint}"
        result = ctx.execute("endpoint.link", {"source": a.id, "endpoint": ref, "request": request})
        ctx.board.arrows[result["arrow"]].style |= style
        return result
    if any(x.source == a.id and x.target == b.id and x.type == type for x in ctx.board.arrows.values()):
        raise OpError("CONFLICT", f"{a.id} -> {b.id} ({type}) already exists")
    status, code = "built", None
    if type == "import":
        source_file, target_file = file_of(a), file_of(b)
        if not source_file or not target_file:
            raise OpError("INVALID_ARGS", "import arrows join file (or function) blocks")
        line = codegen.import_line(source_file, target_file)
        ctx.fs.write(source_file, codegen.add_import(ctx.fs.read(source_file), line,
                                                     codegen.language_of(source_file)))
        code = {"file": source_file, "line": line}
    elif type == "api":
        status = "planned"
        ctx.notes.append("no endpoint given, so the arrow is planned; link one with --endpoint 'POST /path'")
    arrow = new_arrow(ctx, a, b, type, status=status)
    arrow.style = style
    if code:
        arrow.meta["import"] = code
    return arrow_record(arrow)


# Input: ctx (Context), source (str), target (str), type (str | None)
# Output: dict[str, Any]
# Processing: Remove an arrow and the code it wrote.
# Changed by: Sourish
# Date: 2026-10-22
@operation("disconnect", command="/disconnect", tool="plovane_disconnect", group="plovane_connect",
           confirm=True)
def disconnect(ctx: Context, source: str, target: str, type: str | None = None) -> dict[str, Any]:
    """Removes a connection."""
    arrow = find_arrow(ctx, source, target, type)
    ctx.require(ctx.board.blocks[arrow.source])
    if arrow.type == "api" and arrow.endpoint:
        return ctx.execute("endpoint.unlink", {"source": arrow.source, "endpoint": arrow.endpoint})
    code = arrow.meta.get("import")
    if code and ctx.fs.exists(code["file"]):
        ctx.fs.write(code["file"], codegen.remove_import(ctx.fs.read(code["file"]), code["line"]))
    del ctx.board.arrows[arrow.id]
    return {"removed": arrow.id, "code_removed": code}


# Input: ctx (Context), block (str)
# Output: dict[str, Any]
# Processing: Everything a block connects to, both ways.
# Changed by: Sourish
# Date: 2026-10-22
@operation("connections", command="/connections", tool="plovane_connections", group="plovane_connect",
           mutates=False)
def connections(ctx: Context, block: str) -> dict[str, Any]:
    """Lists everything a block connects to."""
    target = ctx.board.block(block)
    ctx.require(target, "read")
    return {
        "block": target.id,
        "outgoing": [arrow_record(a) for a in ctx.board.arrows.values() if a.source == target.id],
        "incoming": [arrow_record(a) for a in ctx.board.arrows.values() if a.target == target.id],
    }


# Input: ctx (Context), request (str)
# Output: dict[str, Any]
# Processing: The path a request takes: callers, endpoint, and everything downstream.
# Changed by: Sourish
# Date: 2026-10-22
@operation("trace", command="/trace", tool="plovane_trace", group="plovane_connect", mutates=False)
def trace(ctx: Context, request: str) -> dict[str, Any]:
    """Highlights the full path a request takes."""
    wanted = request.strip()
    method, _, route = wanted.partition(" ")
    if not route:
        method, route = "", wanted
    matches = [e for e in ctx.board.endpoints.values()
               if e.id == wanted or (e.path == route and method.upper() in ("", e.method))]
    if not matches:
        raise OpError("NOT_FOUND", f"no endpoint {wanted!r}; /endpoint list shows them")
    hops, highlighted = [], set()
    for endpoint in matches:
        callers = [a for a in ctx.board.arrows.values() if a.endpoint == endpoint.id]
        for arrow in callers:
            hops.append({"from": arrow.source, "to": endpoint.service, "via": endpoint.id, "type": "api"})
            highlighted |= {arrow.source, arrow.id}
        highlighted.add(endpoint.service)
        queue, seen = deque([endpoint.service]), {endpoint.service}
        while queue:
            current = queue.popleft()
            for arrow in ctx.board.arrows.values():
                if arrow.source == current and arrow.target not in seen and arrow.endpoint != endpoint.id:
                    hops.append({"from": current, "to": arrow.target, "via": arrow.endpoint, "type": arrow.type})
                    highlighted |= {arrow.target, arrow.id}
                    seen.add(arrow.target)
                    queue.append(arrow.target)
    ctx.board.view["highlight"] = sorted(highlighted)
    return {"request": wanted, "endpoints": [e.id for e in matches], "hops": hops,
            "highlighted": sorted(highlighted)}


# Input: ctx (Context), block (str), reverse (Any), depth (Any)
# Output: dict[str, Any]
# Processing: What a block depends on (or what depends on it), drawn and in code.
# Changed by: Sourish
# Date: 2026-10-22
@operation("deps", command="/deps", tool="plovane_deps", group="plovane_connect", mutates=False)
def deps(ctx: Context, block: str, reverse: Any = False, depth: Any = None) -> dict[str, Any]:
    """Shows what a block depends on, or what depends on it."""
    target = ctx.board.block(block)
    ctx.require(target, "read")
    backwards = flag(reverse)
    limit = integer(depth, "depth") if depth is not None else 10
    found, queue = [], deque([(target.id, 0)])
    seen = {target.id}
    while queue:
        current, level = queue.popleft()
        if level >= limit:
            continue
        for arrow in ctx.board.arrows.values():
            near, far = (arrow.target, arrow.source) if backwards else (arrow.source, arrow.target)
            if near == current and far not in seen:
                seen.add(far)
                found.append({"block": far, "depth": level + 1, "type": arrow.type})
                queue.append((far, level + 1))
    in_code = []
    path = file_of(target) or (target.path if target.kind == "folder" else None)
    if path and ctx.fs.exists(path):
        files = importers_of(ctx, path) if backwards else (imports_of(ctx, path) if target.kind != "folder" else [])
        drawn = {ctx.board.blocks[f["block"]].path for f in found if f["block"] in ctx.board.blocks}
        in_code = [{"file": f, "drawn": f in drawn} for f in files]
    return {"block": target.id, "direction": "dependents" if backwards else "dependencies",
            "drawn": found, "in_code": in_code}


# Input: ctx (Context), source (str), target (str), type (str | None), width, dash, head, color, label, curve, curvature (Any)
# Output: dict[str, Any]
# Processing: Restyle an existing arrow.
# Changed by: Sourish
# Date: 2026-10-22
@operation("arrow.style", command="/arrow style", tool="plovane_arrow_style", group="plovane_connect")
def arrow_style(ctx: Context, source: str, target: str, type: str | None = None, width: Any = None,
                dash: Any = None, head: Any = None, color: Any = None, label: Any = None, curve: Any = None,
                curvature: Any = None) -> dict[str, Any]:
    """Restyles an existing arrow."""
    arrow = find_arrow(ctx, source, target, type)
    ctx.require(ctx.board.blocks[arrow.source])
    flags = {k: v for k, v in {"width": width, "dash": dash, "head": head, "color": color, "label": label,
                               "curve": curve, "curvature": curvature}.items() if v is not None}
    if not flags:
        raise OpError("INVALID_ARGS", "give style flags such as --width 2 --dash 4")
    arrow.style |= validate_style(flags, ARROW_STYLE)
    return arrow_record(arrow)


# Input: ctx (Context), source (str), target (str), curve (Any), curvature (Any), type (str | None)
# Output: dict[str, Any]
# Processing: Change how an arrow bends.
# Changed by: Sourish
# Date: 2026-10-22
@operation("arrow.route", command="/arrow route", tool="plovane_arrow_route", group="plovane_connect")
def arrow_route(ctx: Context, source: str, target: str, curve: Any = None, curvature: Any = None,
                type: str | None = None) -> dict[str, Any]:
    """Changes how an arrow bends."""
    if curve is None and curvature is None:
        raise OpError("INVALID_ARGS", "give --curve straight|smooth|orthogonal and/or --curvature 0-1")
    return arrow_style(ctx, source, target, type=type, curve=curve, curvature=curvature)
