"""Whiteboard files (#72): every whiteboard is a .plo file full of commands.

    whiteboard.plo                   the main whiteboard; it also holds the project's agents and access
    designs/payments.plo             another whiteboard: its own drawing, opened in its own tab

The engine rewrites a whiteboard's .plo after every change, and rebuilds the
board from it when the file changes under it (``git pull``, a teammate, a
hand edit). A .plo file is JSON inside, so it stays readable and diffable::

    {"format": "plovane-whiteboard", "version": 1,
     "commands": ["/theme dark", "/block put auth --kind service --name auth ...", ...]}

Running the commands on a new board rebuilds the saved one. Most lines are
"put" commands (``/block put``, ``/arrow put``, ``/endpoint put``,
``/agent put``, ``/layer put``, ``/template put``). Each one restores a single
thing exactly and changes only the board, never the project's files.

    /boards                          every whiteboard in the project
    /board new designs/payments      a new, empty whiteboard (designs/payments.plo)
    /board save                      rewrite this whiteboard's own .plo now
    /board save designs/copy         save a copy of this whiteboard as designs/copy.plo
    /board load designs/copy.plo     replace this whiteboard with that file's (confirms)
    /board load                      rebuild this whiteboard from its own .plo (confirms)

Agents belong to the whole project, so only whiteboard.plo carries
``/agent put`` lines; every other whiteboard shares them. People only:
loading and the put commands restore access, so agents cannot run them.
"""

from __future__ import annotations

import json
import shlex
from pathlib import PurePosixPath
from typing import Any

from backend.whiteboard.engine import Context
from backend.whiteboard.model import (
    ARROW_TYPES, BLOCK_KINDS, DEFAULT_LAYERS, DEFAULT_PRESETS, Agent, Arrow, Block, Board, Endpoint, Layer,
    Template, default_board,
)
from backend.whiteboard.registry import OpError, integer, items, operation, pair, span
from backend.whiteboard.store import BOARD_EXTENSION, MAIN_BOARD, SKIP_DIRS, now

FORMAT = "plovane-whiteboard"
FILE_VERSION = 1
EXTENSION = BOARD_EXTENSION
DEFAULT_FILE = MAIN_BOARD

LOADABLE = frozenset({
    "block.put", "arrow.put", "endpoint.put", "agent.put", "layer.put", "template.put", "layer.remove",
    "layer.rename", "preset.define", "theme", "grid", "place", "size", "style", "block.rename",
    "project.describe", "overlay.put", "overlay.remove", "role.put", "role.remove",
})
"""What a whiteboard file may run. None of these touch project files, so
opening a file someone sent you can only change the board."""


# -- Writing commands ---------------------------------------------------------

# Input: value (Any)
# Output: str
# Processing: A number as short text that reads back exactly (200, not 200.0).
# Changed by: Claude Code
# Date: 2026-11-15
def num(value: Any) -> str:
    number = float(value)
    return str(int(number)) if number.is_integer() else repr(number)


# Input: value (str)
# Output: str
# Processing: Text quoted for a command line, JSON-encoded when the parser would misread it.
# Changed by: Claude Code
# Date: 2026-11-15
def text(value: str) -> str:
    """The parser splits on "->" and treats "--x" as a flag; such text travels as a JSON string."""
    value = str(value)
    if "->" in value or value.startswith("-") or value.startswith('"'):
        value = json.dumps(value).replace(">", "\\u003e")
    return shlex.quote(value)


# Input: value (Any)
# Output: str
# Processing: Any data as one quoted JSON argument the parser keeps whole.
# Changed by: Claude Code
# Date: 2026-11-15
def data(value: Any) -> str:
    return shlex.quote(json.dumps(value, separators=(",", ":"), sort_keys=True).replace(">", "\\u003e"))


# Input: board (Board)
# Output: list[Block]
# Processing: Every block, parents before their children and otherwise by id, so the order never drifts.
# Changed by: Claude Code
# Date: 2026-11-15
def parent_first(board: Board) -> list[Block]:
    ordered: list[Block] = []
    placed: set[str] = set()
    pending = sorted(board.blocks.values(), key=lambda b: b.id)
    while pending:
        ready = [b for b in pending if not b.parent or b.parent in placed or b.parent not in board.blocks]
        if not ready:  # a parent loop; keep going rather than lose blocks
            ready = pending[:1]
        for block in ready:
            ordered.append(block)
            placed.add(block.id)
        pending = [b for b in pending if b.id not in placed]
    return ordered


# Input: board (Board), agents (bool)
# Output: list[str]
# Processing: The commands that rebuild this board on a new one, in a fixed order; agents only for the main whiteboard.
# Changed by: Claude Code
# Date: 2026-11-15
def board_commands(board: Board, agents: bool = True) -> list[str]:
    lines: list[str] = []
    view = board.view
    if view.get("theme", "light") != "light":
        lines.append(f"/theme {view['theme']}")
    grid = view.get("grid") or {}
    if grid and (grid.get("size") != 20 or grid.get("snap") is not True):
        lines.append(f"/grid {num(grid.get('size', 20))} --snap {'on' if grid.get('snap') else 'off'}")
    if view.get("about"):
        lines.append(f"/project describe {text(view['about'])}")

    defaults = {n: Layer(n, name, lo, hi, list(shows)) for n, name, lo, hi, shows in DEFAULT_LAYERS}
    for n, layer in sorted(board.layers.items()):
        if defaults.get(n) != layer:
            show = f" --show {text(','.join(layer.shows))}" if layer.shows else ""
            lines.append(f"/layer put {n} --name {text(layer.name)} "
                         f"--zoom {num(layer.zoom_min)}-{num(layer.zoom_max)}{show}")
    lines += [f"/layer remove {n}" for n in sorted(defaults) if n not in board.layers]

    for name, style in sorted(board.presets.items()):
        if DEFAULT_PRESETS.get(name) != style and style:
            flags = " ".join(f"--{key.replace('_', '-')} {text(num(v) if isinstance(v, (int, float)) else v)}"
                             for key, v in sorted(style.items()))
            lines.append(f"/preset define {text(name)} {flags}")

    for name, template in sorted(board.templates.items()):
        if not template.builtin:
            line = f"/template put {text(name)}"
            if template.description:
                line += f" --description {text(template.description)}"
            lines.append(line + f" --blocks {data(template.blocks)} --arrows {data(template.arrows)}")

    lines += overlay_commands(board)

    for b in parent_first(board):
        line = (f"/block put {text(b.id)} --kind {b.kind} --name {text(b.name)} --layer {b.layer} "
                f"--at {num(b.x)},{num(b.y)} --size {num(b.w)}x{num(b.h)}")
        for key in ("parent", "path", "symbol"):
            if getattr(b, key):
                line += f" --{key} {text(getattr(b, key))}"
        line += f" --preset {text(b.preset) if b.preset else 'none'}"
        if b.status != "built":
            line += f" --status {text(b.status)}"
        if b.style:
            line += f" --style {data(b.style)}"
        if b.meta:
            line += f" --meta {data(b.meta)}"
        lines.append(line)

    for e in sorted(board.endpoints.values(), key=lambda e: e.id):
        line = f"/endpoint put {text(e.id)} --status {text(e.status)}"
        for key in ("request", "response", "mock", "last_test"):
            if getattr(e, key) is not None:
                line += f" --{key.replace('_', '-')} {data(getattr(e, key))}"
        if e.handler:
            line += f" --handler {text(e.handler)}"
        lines.append(line)

    for a in sorted(board.arrows.values(), key=lambda a: a.id):
        line = f"/arrow put {text(a.source)} -> {text(a.target)} --id {text(a.id)} --type {a.type}"
        if a.endpoint:
            line += f" --endpoint {text(a.endpoint)}"
        if a.status != "built":
            line += f" --status {text(a.status)}"
        if a.style:
            line += f" --style {data(a.style)}"
        if a.meta:
            line += f" --meta {data(a.meta)}"
        lines.append(line)

    if agents:
        for agent in sorted(board.agents.values(), key=lambda a: a.name):
            line = f"/agent put {text(agent.name)} --block {text(agent.block)}"
            if agent.grants:
                line += f" --grants {data(agent.grants)}"
            if agent.regions:
                line += f" --regions {data(agent.regions)}"
            lines.append(line)
    return lines


# Input: board (Board)
# Output: list[str]
# Processing: Lines for overlays and roles that differ from the defaults, groups before what is inside them.
# Changed by: Claude Code
# Date: 2026-11-22
def overlay_commands(board: Board) -> list[str]:
    from backend.whiteboard.overlays import default_overlays, default_roles

    lines: list[str] = []
    stock, roles = default_overlays(), default_roles()
    lines += [f"/overlay remove {text(oid)}" for oid in stock if oid not in board.overlays]
    depth = {}
    for oid in board.overlays:
        seen, current, level = set(), board.overlays[oid].get("parent"), 0
        while current and current in board.overlays and current not in seen:
            seen.add(current)
            current, level = board.overlays[current].get("parent"), level + 1
        depth[oid] = level
    for oid in sorted(board.overlays, key=lambda o: (depth[o], o)):
        if stock.get(oid) != board.overlays[oid]:
            lines.append(f"/overlay put {text(oid)} --data {data(board.overlays[oid])}")
    lines += [f"/role remove {text(rid)}" for rid in roles if rid not in board.roles]
    for rid in sorted(board.roles):
        if roles.get(rid) != board.roles[rid]:
            lines.append(f"/role put {text(rid)} --data {data(board.roles[rid])}")
    return lines


# Input: board (Board), project (str), stamp (bool), agents (bool)
# Output: dict[str, Any]
# Processing: The whole file: a header and the commands; copies carry when they were saved.
# Changed by: Claude Code
# Date: 2026-11-15
def board_file(board: Board, project: str, *, stamp: bool = True, agents: bool = True) -> dict[str, Any]:
    content: dict[str, Any] = {"format": FORMAT, "version": FILE_VERSION}
    if stamp:  # copies say where and when; a whiteboard's own file stays the same on every clone
        content["project"], content["saved_at"] = project, now()
    content["commands"] = board_commands(board, agents)
    return content


# Input: board (Board), agents (bool)
# Output: str
# Processing: A whiteboard's own .plo text: no timestamp or folder name, so it only changes when the board does.
# Changed by: Claude Code
# Date: 2026-11-15
def render(board: Board, agents: bool = True) -> str:
    content = board_file(board, "", stamp=False, agents=agents)
    return json.dumps(content, indent=1, ensure_ascii=False) + "\n"


# -- Reading values back ------------------------------------------------------

# Input: value (Any)
# Output: Any
# Processing: Text from a command line, decoding the JSON form text() writes.
# Changed by: Claude Code
# Date: 2026-11-15
def plain(value: Any) -> Any:
    if isinstance(value, str) and len(value) >= 2 and value.startswith('"') and value.endswith('"'):
        try:
            return json.loads(value)
        except ValueError:
            return value
    return value


# Input: value (Any), name (str), kind (type)
# Output: Any
# Processing: A JSON argument: already data (from MCP), or JSON text (from a command line).
# Changed by: Claude Code
# Date: 2026-11-15
def parsed(value: Any, name: str, kind: type | None = None) -> Any:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError as exc:
            near = value[max(0, exc.pos - 20): exc.pos + 20]
            raise OpError("INVALID_ARGS", f"--{name} must be JSON ({exc.msg} at character {exc.pos}, near {near!r})") from None
        except ValueError:
            raise OpError("INVALID_ARGS", f"--{name} must be JSON") from None
    if kind is not None and not isinstance(value, kind):
        raise OpError("INVALID_ARGS", f"--{name} must be a JSON {kind.__name__}")
    return value


# Input: content (Any)
# Output: list[str]
# Processing: The commands in a whiteboard file, after checking it is one.
# Changed by: Claude Code
# Date: 2026-11-15
def file_commands(content: Any) -> list[str]:
    if isinstance(content, str):
        try:
            content = json.loads(content)
        except ValueError as exc:
            raise OpError("INVALID_ARGS", f"not a whiteboard file: the JSON does not parse ({exc})") from None
    if not isinstance(content, dict) or content.get("format") != FORMAT:
        raise OpError("INVALID_ARGS", f'not a whiteboard file: it needs "format": "{FORMAT}"')
    if int(content.get("version", 0)) > FILE_VERSION:
        raise OpError("INVALID_ARGS", f"this file is version {content['version']}; this Plovane reads up to "
                                      f"{FILE_VERSION}, so update Plovane first")
    commands = content.get("commands")
    if not isinstance(commands, list) or not all(isinstance(c, str) for c in commands):
        raise OpError("INVALID_ARGS", 'a whiteboard file needs "commands": a list of command lines')
    return [c for c in commands if c.strip() and not c.strip().startswith("#")]


# Input: ctx (Context), content (Any), keep (Board | None)
# Output: int
# Processing: Replace the board with the one a file's commands build; keep what a file does not carry. Raises on a bad line.
# Changed by: Claude Code
# Date: 2026-11-15
def build_board(ctx: Context, content: Any, keep: Board | None = None) -> int:
    """Agents belong to the project: a whiteboard other than the main one keeps the
    project's agents and skips any ``/agent put`` lines."""
    from backend.whiteboard.commands import parse

    main = ctx.ws.is_main
    lines = []
    for number, line in enumerate(file_commands(content), start=1):
        try:
            op, args = parse(line)
        except OpError as exc:
            raise OpError(exc.code, f"line {number} ({line[:80]}): {exc.message}") from None
        if op not in LOADABLE:
            raise OpError("FORBIDDEN", f"line {number} runs {op}, which a whiteboard file may not run; "
                                       f"files may only use {sorted(LOADABLE)}")
        if op == "agent.put" and not main:
            ctx.notes.append(f"skipped line {number}: agents belong to the whole project, so only "
                             f"{MAIN_BOARD} sets them")
            continue
        lines.append((number, line, op, args))
    old = keep or ctx.board
    fresh = default_board()
    if not main:
        fresh.agents = old.agents
    # What a .plo does not carry stays as it was; overlay views are each person's own.
    fresh.alerts, fresh.compiles, fresh.views = old.alerts, old.compiles, old.views
    fresh.view = {**{k: v for k, v in old.view.items() if k != "about"},  # the file says what the project is
                  "theme": fresh.view["theme"], "grid": fresh.view["grid"]}
    ctx.ws.board = fresh
    for number, line, op, args in lines:
        try:
            ctx.execute(op, args)
        except OpError as exc:
            raise OpError(exc.code, f"line {number} ({line[:80]}) failed: {exc.message}") from None
    return len(lines)


# -- Put commands: restore one thing exactly ---------------------------------

# Input: ctx (Context), block, kind, name, layer, at, size, parent, path, symbol, preset, status, style, meta (Any)
# Output: dict[str, Any]
# Processing: Create or overwrite one block exactly as given; project files are not touched.
# Changed by: Claude Code
# Date: 2026-11-15
@operation("block.put", command="/block put", tool="plovane_block_put", group="plovane_block", agents=False)
def put_block(ctx: Context, block: str, kind: str, name: str, layer: Any, at: Any = "0,0", size: Any = None,
              parent: str | None = None, path: str | None = None, symbol: str | None = None,
              preset: str | None = None, status: str = "built", style: Any = None,
              meta: Any = None) -> dict[str, Any]:
    """Restores one block exactly (whiteboard files); does not create files."""
    block_id, parent, path, symbol = plain(block), plain(parent), plain(path), plain(symbol)
    ctx.require(None)
    if kind not in BLOCK_KINDS:
        raise OpError("INVALID_ARGS", f"unknown block kind {kind!r}")
    n = integer(layer, "layer")
    if n not in ctx.board.layers:
        raise OpError("NOT_FOUND", f"no layer {n}")
    if parent and parent not in ctx.board.blocks:
        raise OpError("NOT_FOUND", f"no parent block {parent!r}; parents come before their children")
    x, y = pair(at, "at")
    w, h = pair(size, "size", sep="x") if size is not None else (160, 80)
    preset = plain(preset)
    record = Block(id=block_id, kind=kind, name=str(plain(name)), layer=n, x=x, y=y, w=w, h=h, parent=parent,
                   path=path, symbol=symbol, preset=None if preset in (None, "none") else preset,
                   status=str(plain(status)), style=parsed(style or {}, "style", dict),
                   meta=parsed(meta or {}, "meta", dict))
    ctx.board.blocks[block_id] = record
    return {"block": block_id}


# Input: ctx (Context), source, target, id, type, endpoint, status, style, meta (Any)
# Output: dict[str, Any]
# Processing: Create or overwrite one arrow exactly as given; no code is written.
# Changed by: Claude Code
# Date: 2026-11-15
@operation("arrow.put", command="/arrow put", tool="plovane_arrow_put", group="plovane_connect", agents=False)
def put_arrow(ctx: Context, source: str, target: str, id: str | None = None, type: str = "calls",
              endpoint: str | None = None, status: str = "built", style: Any = None,
              meta: Any = None) -> dict[str, Any]:
    """Restores one arrow exactly (whiteboard files); does not write code."""
    ctx.require(None)
    source, target = plain(source), plain(target)
    for end in (source, target):
        if end not in ctx.board.blocks:
            raise OpError("NOT_FOUND", f"no block {end!r}; blocks come before arrows")
    if type not in ARROW_TYPES:
        raise OpError("INVALID_ARGS", f"arrow type is one of {list(ARROW_TYPES)}")
    arrow_id = plain(id) or ctx.board.new_id(f"{source}-to-{target}", pool=ctx.board.arrows)
    ctx.board.arrows[arrow_id] = Arrow(id=arrow_id, source=source, target=target, type=type,
                                       endpoint=plain(endpoint), status=str(plain(status)),
                                       style=parsed(style or {}, "style", dict), meta=parsed(meta or {}, "meta", dict))
    return {"arrow": arrow_id}


# Input: ctx (Context), endpoint, status, request, response, mock, last_test, handler (Any)
# Output: dict[str, Any]
# Processing: Create or overwrite one endpoint exactly as given; no handler code is written.
# Changed by: Claude Code
# Date: 2026-11-15
@operation("endpoint.put", command="/endpoint put", tool="plovane_endpoint_put", group="plovane_endpoint",
           agents=False)
def put_endpoint(ctx: Context, endpoint: str, status: str = "planned", request: Any = None, response: Any = None,
                 mock: Any = None, last_test: Any = None, handler: str | None = None) -> dict[str, Any]:
    """Restores one endpoint exactly (whiteboard files); does not write handlers."""
    from backend.whiteboard.ops.endpoints import split_ref

    ctx.require(None)
    service, method, route = split_ref(plain(endpoint))
    if service not in ctx.board.blocks:
        raise OpError("NOT_FOUND", f"no service block {service!r}; blocks come before endpoints")
    key = f"{service}:{method} {route}"
    ctx.board.endpoints[key] = Endpoint(
        id=key, service=service, method=method, path=route, status=str(plain(status)), handler=plain(handler),
        request=None if request is None else parsed(request, "request"),
        response=None if response is None else parsed(response, "response"),
        mock=None if mock is None else parsed(mock, "mock"),
        last_test=None if last_test is None else parsed(last_test, "last-test", dict))
    return {"endpoint": key}


# Input: ctx (Context), agent, block, grants, regions (Any)
# Output: dict[str, Any]
# Processing: Create or overwrite one agent and its access exactly as given.
# Changed by: Claude Code
# Date: 2026-11-15
@operation("agent.put", command="/agent put", tool="plovane_agent_put", group="plovane_agent", agents=False)
def put_agent(ctx: Context, agent: str, block: str, grants: Any = None, regions: Any = None) -> dict[str, Any]:
    """Restores one agent and its access (whiteboard files)."""
    from backend.whiteboard.ops.agents import check_mode

    ctx.require(None)
    name, block_id = str(plain(agent)), plain(block)
    if name == "user":
        raise OpError("INVALID_ARGS", "'user' is the person, not an agent")
    if block_id not in ctx.board.blocks:
        raise OpError("NOT_FOUND", f"no block {block_id!r} for agent {name}; blocks come before agents")
    grant_list = parsed(grants or [], "grants", list)
    region_list = parsed(regions or [], "regions", list)
    for entry in [*grant_list, *region_list]:
        if not isinstance(entry, dict):
            raise OpError("INVALID_ARGS", "each grant and region is a JSON object")
        check_mode(entry.get("mode", ""))
    if any("path" not in g for g in grant_list) or any(len(r.get("area") or []) != 4 for r in region_list):
        raise OpError("INVALID_ARGS", 'grants look like {"path": "src", "mode": "read"}; '
                                      'regions like {"area": [x1, y1, x2, y2], "mode": "read", "layer": 1}')
    ctx.board.agents[name] = Agent(name=name, block=block_id, grants=grant_list, regions=region_list)
    return {"agent": name, "block": block_id}


# Input: ctx (Context), number, name, zoom, show (Any)
# Output: dict[str, Any]
# Processing: Create or overwrite one layer exactly as given.
# Changed by: Claude Code
# Date: 2026-11-15
@operation("layer.put", command="/layer put", tool="plovane_layer_put", group="plovane_layer", agents=False)
def put_layer(ctx: Context, number: Any, zoom: Any, name: str | None = None, show: Any = None) -> dict[str, Any]:
    """Restores one layer exactly (whiteboard files)."""
    ctx.require(None)
    n = integer(number, "layer number")
    low, high = span(zoom, "zoom")
    ctx.board.layers[n] = Layer(n, str(plain(name) or f"Layer {n}"), low, high, items(plain(show)))
    return {"layer": n}


# Input: ctx (Context), template, description, blocks, arrows (Any)
# Output: dict[str, Any]
# Processing: Create or overwrite one custom template exactly as given.
# Changed by: Claude Code
# Date: 2026-11-15
@operation("template.put", command="/template put", tool="plovane_template_put", group="plovane_template",
           agents=False)
def put_template(ctx: Context, template: str, description: str = "", blocks: Any = None,
                 arrows: Any = None) -> dict[str, Any]:
    """Restores one custom template exactly (whiteboard files)."""
    ctx.require(None)
    name = str(plain(template))
    existing = ctx.board.templates.get(name)
    if existing is not None and existing.builtin:
        raise OpError("CONFLICT", f"{name!r} is a built-in template")
    ctx.board.templates[name] = Template(name=name, description=str(plain(description) or ""),
                                         blocks=parsed(blocks or [], "blocks", list),
                                         arrows=parsed(arrows or [], "arrows", list))
    return {"template": name}


# -- Whiteboards: list, create, save, load ------------------------------------

# Input: ctx (Context), path (str | None)
# Output: str
# Processing: A project-relative .plo path (.plo added when it has no extension), refusing anything outside the project.
# Changed by: Claude Code
# Date: 2026-11-15
def project_file(ctx: Context, path: str | None) -> str:
    rel = (path or DEFAULT_FILE).strip().lstrip("/")
    if not PurePosixPath(rel).suffix:
        rel += EXTENSION
    resolved = (ctx.root / rel).resolve()
    if not resolved.is_relative_to(ctx.root.resolve()):
        raise OpError("INVALID_ARGS", f"{path!r} is outside the project")
    return resolved.relative_to(ctx.root.resolve()).as_posix()


# Input: root (Path)
# Output: list[str]
# Processing: Every .plo file in the project, the main whiteboard first.
# Changed by: Claude Code
# Date: 2026-11-15
def whiteboards(root: Any) -> list[str]:
    import os
    from pathlib import Path

    found = set()
    base = Path(root)
    for folder, dirs, files in os.walk(base):
        dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS and not d.endswith(".egg-info"))
        here = Path(folder).relative_to(base).as_posix()
        found.update(f if here == "." else f"{here}/{f}" for f in files if f.endswith(EXTENSION))
    found.add(MAIN_BOARD)
    return [MAIN_BOARD, *sorted(found - {MAIN_BOARD})]


# Input: ctx (Context)
# Output: dict[str, Any]
# Processing: Every whiteboard in the project.
# Changed by: Claude Code
# Date: 2026-11-15
@operation("board.list", command="/boards", tool="plovane_board_list", group="plovane_history", mutates=False)
def list_boards(ctx: Context) -> dict[str, Any]:
    """Lists the project's whiteboards (.plo files)."""
    return {"main": MAIN_BOARD, "current": ctx.ws.board_file, "boards": whiteboards(ctx.root)}


# Input: ctx (Context), path (str), content (Any)
# Output: dict[str, Any]
# Processing: Create a new whiteboard file: empty, or from a whiteboard file's content.
# Changed by: Claude Code
# Date: 2026-11-15
@operation("board.new", command="/board new", tool="plovane_board_new", group="plovane_history", agents=False)
def new_board(ctx: Context, path: str, content: Any = None) -> dict[str, Any]:
    """Creates a new whiteboard (.plo file) to open in its own tab."""
    ctx.require(None)
    rel = project_file(ctx, path)
    if not rel.endswith(EXTENSION):
        raise OpError("INVALID_ARGS", f"whiteboard files end in {EXTENSION}")
    if ctx.fs.exists(rel):
        raise OpError("CONFLICT", f"{rel} already exists; open it instead")
    if content is None:
        text_out = json.dumps({"format": FORMAT, "version": FILE_VERSION, "commands": []}, indent=1) + "\n"
    else:
        file_commands(content)  # refuse anything that is not a whiteboard file
        text_out = content if isinstance(content, str) else json.dumps(content, indent=1, ensure_ascii=False) + "\n"
    ctx.fs.allow_plo = True
    try:
        ctx.fs.write(rel, text_out)
    finally:
        ctx.fs.allow_plo = False
    return {"board": rel}


# Input: ctx (Context), path (str | None)
# Output: dict[str, Any]
# Processing: Rewrite this whiteboard's own .plo, or save a copy of it as another .plo.
# Changed by: Claude Code
# Date: 2026-11-15
@operation("board.save", command="/board save", tool="plovane_board_save", group="plovane_history",
           mutates=False, agents=False)
def save(ctx: Context, path: str | None = None) -> dict[str, Any]:
    """Saves the whiteboard to its .plo file, or a copy to another one."""
    ctx.require(None)
    own = ctx.ws.board_file
    rel = project_file(ctx, path or own)
    if rel == own:
        if not ctx.dry_run:
            ctx.ws.plo_error = None
            ctx.ws.save(force=True)
        return {"saved": rel, "commands": len(board_commands(ctx.board, ctx.ws.is_main))}
    if rel == MAIN_BOARD:
        raise OpError("CONFLICT", f"{MAIN_BOARD} is the main whiteboard; open it to change it")
    content = board_file(ctx.board, ctx.root.name, agents=ctx.ws.is_main)  # only the main one owns agents
    ctx.fs.allow_plo = True
    try:
        ctx.fs.write(rel, json.dumps(content, indent=1, ensure_ascii=False) + "\n")
    finally:
        ctx.fs.allow_plo = False
    return {"saved": rel, "commands": len(content["commands"])}


# Input: ctx (Context), path (str | None), content (Any)
# Output: dict[str, Any]
# Processing: Replace this whiteboard with the one a .plo file's commands build, all or nothing.
# Changed by: Claude Code
# Date: 2026-11-15
@operation("board.load", command="/board load", tool="plovane_board_load", group="plovane_history",
           confirm=True, agents=False)
def load(ctx: Context, path: str | None = None, content: Any = None) -> dict[str, Any]:
    """Replaces the whiteboard with a saved .plo file of commands."""
    ctx.require(None)
    if content is None:
        rel = project_file(ctx, path or ctx.ws.board_file)
        if not ctx.fs.exists(rel):
            raise OpError("NOT_FOUND", f"no file {rel!r}")
        content = ctx.fs.read(rel)
    ran = build_board(ctx, content)
    if not ctx.dry_run:
        ctx.ws.plo_error = None
    missing = sorted({b.path for b in ctx.board.blocks.values()
                      if b.kind in ("file", "folder") and b.path and not ctx.fs.exists(b.path)})
    if missing:
        ctx.notes.append(f"{len(missing)} file or folder blocks point at paths this project does not have")
    return {"loaded": ran, "blocks": len(ctx.board.blocks), "arrows": len(ctx.board.arrows),
            "agents": sorted(ctx.board.agents), "missing_paths": missing[:50]}
