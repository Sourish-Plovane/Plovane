"""Blocks: add, move, group, split, merge, rename, delete and inspect.

Blocks that stand for code carry it with them: moving a file block moves the
file, renaming a function block renames the function, deleting a block
deletes its code (after showing what depends on it).
"""

from __future__ import annotations

import ast
import os
import re
from typing import Any

from backend.whiteboard import codegen
from backend.whiteboard.engine import Context
from backend.whiteboard.model import Block
from backend.whiteboard.ops.placement import apply_position, grow_to_fit, resolve_all
from backend.whiteboard.ops.project import blocks_under, drop_blocks, ensure_folder_block, importers_of
from backend.whiteboard.refactor import Symbol, apply_edits, rename_edits, rewrite_js_imports, rewrite_python_imports, symbols
from backend.whiteboard.registry import OpError, integer, operation

LOGICAL_KINDS = ("service", "server", "external", "database", "queue", "group", "note", "slot")


# Input: ctx (Context), block (Block)
# Output: tuple[Symbol, str, str]
# Processing: The symbol, file text and file path for a function, method or class block.
# Changed by: Sourish
# Date: 2026-10-22
def function_unit(ctx: Context, block: Block) -> tuple[Symbol, str, str]:
    file = block.meta.get("file")
    qualname = block.symbol or block.name
    if not file:
        raise OpError("INVALID_ARGS", f"{block.id} is not tied to a file")
    text = ctx.fs.read(file)
    for symbol in symbols(text, file):
        if symbol.qualname == qualname:
            return symbol, text, file
    raise OpError("NOT_FOUND", f"{qualname} is no longer in {file}")


# Input: lines (list[str]), start (int)
# Output: int
# Processing: Extend a definition's first line up over decorators and its comment block.
# Changed by: Sourish
# Date: 2026-10-22
def leading_start(lines: list[str], start: int) -> int:
    """``start`` is 1-based; returns a 1-based line including what belongs
    to the definition above it."""
    index = start - 1
    while index > 0:
        above = lines[index - 1].strip()
        if above.startswith(("@", "#", "//", "/**", "*", "*/")):
            index -= 1
        else:
            break
    return index + 1


# Input: ctx (Context), block (Block), new (str)
# Output: list[str]
# Processing: Rename a function, method or class where it is really referred to.
# Changed by: Sourish
# Date: 2026-10-22
def rename_symbol(ctx: Context, block: Block, new: str) -> list[str]:
    """Python: through the syntax tree (see whiteboard/refactor.py), so an
    unrelated local with the same name is never touched. Other languages:
    the defining file plus the files that import it, by whole word."""
    symbol, text, file = function_unit(ctx, block)
    language = codegen.language_of(file)
    if language == "python":
        files = {f: ctx.fs.read(f) for f in ctx.fs.files_under() if f.endswith(".py")}
        edits, notes = rename_edits(files, file, symbol.qualname, new)
        ctx.notes.extend(notes)
        targets = {path: apply_edits(files[path], found) for path, found in edits.items()}
    else:
        pattern = re.compile(rf"\b{re.escape(symbol.name)}\b")
        users = [file] + [f for f in importers_of(ctx, file) if codegen.language_of(f) == language]
        targets = {path: pattern.sub(new, ctx.fs.read(path)) for path in users}
    changed = []
    for path, updated in targets.items():
        if updated != ctx.fs.read(path):
            ctx.require(path)
            ctx.fs.write(path, updated)
            changed.append(path)
    return sorted(changed)


# Input: ctx (Context), old (str), new (str)
# Output: list[str]
# Processing: Move a file or folder and fix every import that pointed into it.
# Changed by: Sourish
# Date: 2026-10-22
def move_with_imports(ctx: Context, old: str, new: str) -> list[str]:
    """Imports in other files are pointed at the new location, and the moved
    files' own relative imports are recomputed. Returns the files changed."""
    old, new = old.strip("/"), new.strip("/")
    was_dir = ctx.fs.is_dir(old)
    before = {f: f for f in ctx.fs.files_under(old)} if was_dir else {old: old}
    ctx.fs.move(old, new)
    file_moves = {f: new + f[len(old):] for f in before}
    module_moves = {codegen.module_name(o): codegen.module_name(n)
                    for o, n in file_moves.items() if o.endswith(".py")}
    if was_dir and module_moves:
        module_moves[codegen.module_name(old + "/__init__.py")] = codegen.module_name(new + "/__init__.py")
    reverse = {n: o for o, n in file_moves.items()}
    changed = []
    for path in ctx.fs.files_under():
        language = codegen.language_of(path)
        previous = reverse.get(path, path)
        text = ctx.fs.read(path)
        if language == "python" and module_moves:
            updated = rewrite_python_imports(text, path, previous, module_moves)
        elif language in ("javascript", "typescript"):
            updated = rewrite_js_imports(text, path, previous, file_moves, lambda p: ctx.fs.path(p).is_file())
        else:
            continue
        if updated != text:
            ctx.require(path)
            ctx.fs.write(path, updated)
            changed.append(path)
    return sorted(changed)


# Input: ctx (Context)
# Output: None
# Processing: Re-point import arrows' recorded import lines after files moved.
# Changed by: Sourish
# Date: 2026-10-22
def refresh_import_arrows(ctx: Context) -> None:
    from backend.whiteboard.ops.arrows import file_of

    for arrow in ctx.board.arrows.values():
        code = arrow.meta.get("import")
        source, target = ctx.board.blocks.get(arrow.source), ctx.board.blocks.get(arrow.target)
        if code and source and target and file_of(source) and file_of(target):
            arrow.meta["import"] = {"file": file_of(source), "line": codegen.import_line(file_of(source), file_of(target))}


# Input: ctx (Context), unit (Symbol), file (str)
# Output: list[str]
# Processing: Files that refer to a symbol, found the same way rename finds them.
# Changed by: Sourish
# Date: 2026-10-22
def callers_of(ctx: Context, unit: Symbol, file: str) -> list[str]:
    language = codegen.language_of(file)
    if language == "python":
        files = {f: ctx.fs.read(f) for f in ctx.fs.files_under() if f.endswith(".py")}
        edits, _ = rename_edits(files, file, unit.qualname, unit.name + "_")
        return sorted(f for f in edits if f != file)
    pattern = re.compile(rf"\b{re.escape(unit.name)}\s*\(")
    return sorted(f for f in importers_of(ctx, file) if pattern.search(ctx.fs.read(f)))


# Input: ctx (Context), owner (Block)
# Output: dict[str, Block]
# Processing: Blocks for every function, class and method in a file block, made if missing.
# Changed by: Sourish
# Date: 2026-10-22
def symbol_blocks(ctx: Context, owner: Block) -> dict[str, Block]:
    """Classes become containers holding their methods, so class-heavy code
    shows its real structure. Line numbers are refreshed on every call."""
    existing = {b.symbol: b for b in ctx.board.blocks.values()
                if b.kind in ("function", "class") and b.meta.get("file") == owner.path}
    found: dict[str, Block] = {}
    for symbol in symbols(ctx.fs.read(owner.path), owner.path):
        block = existing.get(symbol.qualname)
        if block is None:
            parent = found[symbol.parent].id if symbol.parent in found else owner.id
            block = ctx.new_block("class" if symbol.kind == "class" else "function", symbol.name, parent=parent,
                                  symbol=symbol.qualname, id_hint=f"{owner.id}-{symbol.qualname.replace('.', '-')}",
                                  meta={"file": owner.path})
        block.meta |= {"lines": [symbol.start_line, symbol.end_line], "signature": symbol.signature,
                       "method_of": symbol.parent}
        found[symbol.qualname] = block
    for block in found.values():
        if block.kind == "class":
            grow_to_fit(ctx, block)
    return found


# Input: ctx (Context), block (Block), old_prefix (str), new_prefix (str)
# Output: None
# Processing: Re-point a block and everything under it from one path to another.
# Changed by: Sourish
# Date: 2026-10-22
def repath(ctx: Context, block: Block, old_prefix: str, new_prefix: str) -> None:
    # Input: value (str | None)
    # Output: str | None
    # Processing: A path with the old prefix swapped for the new one.
    # Changed by: Sourish
    # Date: 2026-10-22
    def swap(value: str | None) -> str | None:
        if value and (value == old_prefix or value.startswith(old_prefix + "/")):
            return new_prefix + value[len(old_prefix):]
        return value

    for target in [block, *ctx.board.descendants(block.id)]:
        target.path = swap(target.path)
        if "file" in target.meta:
            target.meta["file"] = swap(target.meta["file"])
    for endpoint in ctx.board.endpoints.values():
        endpoint.handler = swap(endpoint.handler)


# Input: ctx (Context), kind (str), name (str), layer (Any), path (str | None), at, anchor, right_of, left_of, below, above, inside, gap (Any)
# Output: dict[str, Any]
# Processing: Add a block; folder and file blocks create the real folder or file.
# Changed by: Sourish
# Date: 2026-10-22
@operation("block.add", command="/block add", tool="plovane_block_add", group="plovane_block")
def add(ctx: Context, kind: str, name: str, layer: Any = None, path: str | None = None, at: Any = None,
        anchor: str | None = None, right_of: str | None = None, left_of: str | None = None,
        below: str | None = None, above: str | None = None, inside: str | None = None,
        gap: Any = None) -> dict[str, Any]:
    """Adds a block to a layer."""
    position = {"at": at, "anchor": anchor, "right_of": right_of, "left_of": left_of, "below": below,
                "above": above, "inside": inside, "gap": gap}
    if kind in ("folder", "file"):
        op = "folder.create" if kind == "folder" else "file.create"
        return ctx.execute(op, {"path": path or name, **{k: v for k, v in position.items() if v is not None}})
    if kind in ("function", "step", "endpoint", "agent", "scan"):
        raise OpError("INVALID_ARGS", {
            "function": "function blocks come from code: /block split FILE --by function",
            "step": "step blocks come from code: /blocks view FILE",
            "endpoint": "endpoints are added with /endpoint add",
            "agent": "agents are added with /agent add",
            "scan": "scan blocks are added with /scan",
        }[kind])
    ctx.require(path or None)
    if path:
        ctx.fs.mkdir(path.strip("/"))
    block = ctx.new_block(kind, name, layer=None if layer is None else integer(layer, "layer"),
                          path=path.strip("/") if path else None)
    apply_position(ctx, block, **position)
    return {"block": block.id, "kind": kind, "layer": block.layer, "x": block.x, "y": block.y}


# Input: ctx (Context), block (str), into (str | None), at, anchor, right_of, left_of, below, above, gap (Any)
# Output: dict[str, Any]
# Processing: Move a block on the canvas or into a container; file blocks move the file.
# Changed by: Sourish
# Date: 2026-10-22
@operation("block.move", command="/block move", tool="plovane_block_move", group="plovane_block")
def move(ctx: Context, block: str, into: str | None = None, at: Any = None, anchor: str | None = None,
         right_of: str | None = None, left_of: str | None = None, below: str | None = None,
         above: str | None = None, gap: Any = None) -> dict[str, Any]:
    """Moves a block on the canvas or into a group; moving a file block moves the real file."""
    target = ctx.board.block(block)
    ctx.require(target)
    moved_from = moved_to = None
    if into:
        container = ctx.board.block(into)
        ctx.require(container)
        if target.kind in ("file", "folder") and target.path and container.path and \
                container.kind in ("folder", "service", "server"):
            moved_from, moved_to = target.path, f"{container.path.strip('/')}/{os.path.basename(target.path)}"
            if moved_from != moved_to:
                ctx.require(moved_to)
                fixed = move_with_imports(ctx, moved_from, moved_to)
                repath(ctx, target, moved_from, moved_to)
                refresh_import_arrows(ctx)
                if fixed:
                    ctx.notes.append(f"imports updated in {fixed}")
        apply_position(ctx, target, inside=container.id, at=at, anchor=anchor)
    elif not apply_position(ctx, target, at=at, anchor=anchor, right_of=right_of, left_of=left_of,
                            below=below, above=above, gap=gap):
        raise OpError("INVALID_ARGS", "give --into BLOCK, --at x,y or a relative position")
    return {"block": target.id, "parent": target.parent, "x": target.x, "y": target.y,
            "moved_file": {"from": moved_from, "to": moved_to} if moved_from else None,
            "imports_updated": fixed if moved_from and moved_from != moved_to else []}


# Input: ctx (Context), blocks (Any), name (str), kind (str), path (str | None)
# Output: dict[str, Any]
# Processing: Put blocks inside a new group, server or real folder.
# Changed by: Sourish
# Date: 2026-10-22
@operation("block.group", command="/block group", tool="plovane_block_group", group="plovane_block")
def group(ctx: Context, blocks: Any, name: str, kind: str = "group", path: str | None = None) -> dict[str, Any]:
    """Groups blocks into a folder or server."""
    members = resolve_all(ctx, blocks, minimum=1)
    if kind not in ("group", "server", "service", "folder"):
        raise OpError("INVALID_ARGS", "kind is group, server, service or folder")
    parents = {m.parent for m in members}
    if kind == "folder":
        folder = (path or name).strip("/")
        ctx.require(folder)
        ctx.fs.mkdir(folder)
        container = ctx.board.blocks[ensure_folder_block(ctx, folder)]
        for member in members:
            ctx.execute("block.move", {"block": member.id, "into": container.id})
    else:
        container = ctx.new_block(kind, name, layer=min(m.layer for m in members),
                                  parent=parents.pop() if len(parents) == 1 else None)
        left, top = min(m.x for m in members), min(m.y for m in members)
        container.x, container.y = ctx.snap(left - 20), ctx.snap(top - 40)
        for member in members:
            member.parent = container.id
            member.x, member.y = member.x - container.x, member.y - container.y
        grow_to_fit(ctx, container)
    return {"group": container.id, "kind": container.kind, "members": [m.id for m in members]}


# Input: ctx (Context), block (str), by (str)
# Output: dict[str, Any]
# Processing: Show a block's contents as child blocks: its functions or its files.
# Changed by: Sourish
# Date: 2026-10-22
@operation("block.split", command="/block split", tool="plovane_block_split", group="plovane_block")
def split(ctx: Context, block: str, by: str = "function") -> dict[str, Any]:
    """Splits a block into smaller blocks."""
    target = ctx.board.block(block)
    ctx.require(target)
    created = []
    if by == "function":
        if target.kind != "file" or not target.path:
            raise OpError("INVALID_ARGS", "split --by function works on a file block")
        before = set(ctx.board.blocks)
        symbol_blocks(ctx, target)
        created = sorted(set(ctx.board.blocks) - before)
    elif by == "file":
        if not target.path or not ctx.fs.is_dir(target.path):
            raise OpError("INVALID_ARGS", "split --by file works on a block with a folder path")
        for file in ctx.fs.files_under(target.path):
            if os.path.dirname(file) == target.path.strip("/") and not ctx.board.block_for_path(file):
                child = ctx.new_block("file", os.path.basename(file), parent=target.id, path=file,
                                      meta={"language": codegen.language_of(file)})
                created.append(child.id)
    else:
        raise OpError("INVALID_ARGS", "by is function or file")
    grow_to_fit(ctx, target)
    ctx.notes.append("the code is unchanged; the new blocks map onto what is already there")
    return {"block": target.id, "created": created}


# Input: ctx (Context), blocks (Any)
# Output: dict[str, Any]
# Processing: Combine blocks into the first one, merging files when they are files.
# Changed by: Sourish
# Date: 2026-10-22
@operation("block.merge", command="/block merge", tool="plovane_block_merge", group="plovane_block",
           confirm=True)
def merge(ctx: Context, blocks: Any) -> dict[str, Any]:
    """Combines blocks into one."""
    targets = resolve_all(ctx, blocks, minimum=2)
    keep, others = targets[0], targets[1:]
    merged_files = []
    for other in others:
        if keep.kind == "file" and other.kind == "file" and keep.path and other.path:
            if codegen.language_of(keep.path) != codegen.language_of(other.path):
                raise OpError("CONFLICT", f"cannot merge {other.path} into a file of another language")
            c = codegen.comment(codegen.language_of(keep.path))
            merged = codegen.append_block(ctx.fs.read(keep.path),
                                          f"{c} Merged from {other.path}\n{ctx.fs.read(other.path)}")
            ctx.fs.write(keep.path, merged)
            ctx.fs.remove(other.path)
            merged_files.append(other.path)
            for child in ctx.board.descendants(other.id):
                if child.meta.get("file") == other.path:
                    child.meta["file"] = keep.path
        elif other.kind in ("file", "folder") and other.path:
            raise OpError("CONFLICT", f"{other.id} is a {other.kind}; merge files with files only")
        for child in ctx.board.blocks.values():
            if child.parent == other.id:
                child.parent = keep.id
        for arrow in ctx.board.arrows.values():
            arrow.source = keep.id if arrow.source == other.id else arrow.source
            arrow.target = keep.id if arrow.target == other.id else arrow.target
        for endpoint in ctx.board.endpoints.values():
            if endpoint.service == other.id:
                endpoint.service = keep.id
        del ctx.board.blocks[other.id]
    # Merging can leave self-loops and duplicate arrows; drop them.
    seen = set()
    for arrow_id, arrow in list(ctx.board.arrows.items()):
        key = (arrow.source, arrow.target, arrow.type, arrow.endpoint)
        if arrow.source == arrow.target or key in seen:
            del ctx.board.arrows[arrow_id]
        seen.add(key)
    grow_to_fit(ctx, keep)
    return {"kept": keep.id, "merged": [o.id for o in others], "files_merged": merged_files}


# Input: ctx (Context), block (str), name (str)
# Output: dict[str, Any]
# Processing: Rename a block; files, folders and functions are renamed in code too.
# Changed by: Sourish
# Date: 2026-10-22
@operation("block.rename", command="/block rename", tool="plovane_block_rename", group="plovane_block")
def rename(ctx: Context, block: str, name: str) -> dict[str, Any]:
    """Renames a block."""
    target = ctx.board.block(block)
    ctx.require(target)
    if "/" in name:
        raise OpError("INVALID_ARGS", "a name cannot contain /; use /block move to change folders")
    changed: list[str] = []
    if target.kind in ("file", "folder") and target.path:
        new_path = "/".join([*target.path.strip("/").split("/")[:-1], name])
        ctx.require(new_path)
        old_path = target.path.strip("/")
        fixed = move_with_imports(ctx, old_path, new_path)
        repath(ctx, target, old_path, new_path)
        refresh_import_arrows(ctx)
        changed = sorted({new_path, *fixed})
    elif target.kind in ("function", "class"):
        if not re.match(r"^[A-Za-z_]\w*$", name):
            raise OpError("INVALID_ARGS", f"{name!r} is not a valid identifier")
        old_qualname = target.symbol or target.name
        changed = rename_symbol(ctx, target, name)
        new_qualname = old_qualname.rsplit(".", 1)[0] + "." + name if "." in old_qualname else name
        for child in [target, *ctx.board.descendants(target.id)]:
            if child.symbol == old_qualname or (child.symbol or "").startswith(old_qualname + "."):
                child.symbol = new_qualname + child.symbol[len(old_qualname):]
            if child.meta.get("function") == old_qualname:
                child.meta["function"] = new_qualname
    old = target.name
    target.name = name
    return {"block": target.id, "from": old, "to": name, "files_changed": changed}


# Input: ctx (Context), block (str)
# Output: dict[str, Any]
# Processing: Delete a block and its code after listing its dependents.
# Changed by: Sourish
# Date: 2026-10-22
@operation("block.delete", command="/block delete", tool="plovane_block_delete", group="plovane_block",
           confirm=True)
def delete(ctx: Context, block: str) -> dict[str, Any]:
    """Removes a block and its code after showing dependents."""
    target = ctx.board.block(block)
    ctx.require(target)
    family = {target.id} | {b.id for b in ctx.board.descendants(target.id)}
    dependents: dict[str, Any] = {
        "incoming_arrows": [a.id for a in ctx.board.arrows.values() if a.target in family and a.source not in family],
        "children": sorted(family - {target.id}),
    }
    code_removed = None
    if target.kind in ("file", "folder") and target.path and ctx.fs.exists(target.path):
        dependents["importers"] = importers_of(ctx, target.path)
        family |= {b.id for b in blocks_under(ctx, target.path)}
        ctx.fs.remove(target.path)
        code_removed = target.path
    elif target.kind in ("function", "class"):
        unit, text, file = function_unit(ctx, target)
        lines = text.splitlines(keepends=True)
        start = leading_start([l.rstrip("\n") for l in lines], unit.start_line)
        dependents["callers"] = callers_of(ctx, unit, file)
        remaining = lines[: start - 1] + lines[unit.end_line:]
        if codegen.language_of(file) == "python" and unit.kind == "method":
            # Removing a class's last method would leave an empty, invalid body.
            try:
                ast.parse("".join(remaining))
            except SyntaxError:
                indent = re.match(r"\s*", lines[unit.start_line - 1]).group(0)
                remaining.insert(start - 1, f"{indent}pass\n")
        ctx.fs.write(file, "".join(remaining))
        code_removed = f"{file}:{start}-{unit.end_line}"
    elif target.kind == "step":
        from backend.whiteboard.ops.english import delete_step_code

        code_removed = delete_step_code(ctx, target)
    removed = drop_blocks(ctx, family)
    if dependents["incoming_arrows"] or dependents.get("importers") or dependents.get("callers"):
        ctx.notes.append("other code depended on this block; see dependents")
    return {"deleted": target.id, "code_removed": code_removed, "dependents": dependents, "removed": removed}


# Input: ctx (Context), block (str)
# Output: dict[str, Any]
# Processing: A block's code, connections and source.
# Changed by: Sourish
# Date: 2026-10-22
@operation("block.info", command="/block info", tool="plovane_block_info", group="plovane_block", mutates=False)
def info(ctx: Context, block: str) -> dict[str, Any]:
    """Shows a block's code, connections, and source."""
    target = ctx.board.block(block)
    ctx.require(target, "read")
    code = None
    if target.kind == "file" and target.path and ctx.fs.exists(target.path):
        code = ctx.fs.read(target.path)[:20000]
    elif target.kind == "function":
        code = function_unit(ctx, target)[0].code
    elif target.kind == "step" and target.meta.get("file"):
        lines = ctx.fs.read(target.meta["file"]).splitlines()
        start, end = target.meta["lines"]
        code = "\n".join(lines[start - 1:end])
    names = {b.id: b.name for b in ctx.board.blocks.values()}
    return {
        "block": target.id, "kind": target.kind, "name": target.name, "layer": target.layer,
        "path": target.path, "symbol": target.symbol, "status": target.status, "parent": target.parent,
        "style": ctx.board.resolved_style(target), "meta": target.meta, "code": code,
        "children": [b.id for b in ctx.board.blocks.values() if b.parent == target.id],
        "outgoing": [{"to": names.get(a.target, a.target), "type": a.type, "endpoint": a.endpoint,
                      "status": a.status} for a in ctx.board.arrows.values() if a.source == target.id],
        "incoming": [{"from": names.get(a.source, a.source), "type": a.type, "endpoint": a.endpoint,
                      "status": a.status} for a in ctx.board.arrows.values() if a.target == target.id],
        "endpoints": [e.id for e in ctx.board.endpoints.values() if e.service == target.id],
        "library_entry": target.meta.get("library"),
    }
