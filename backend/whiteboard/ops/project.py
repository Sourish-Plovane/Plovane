"""Project structure: real folders and files, and the blocks that stand for them.

The rule from the command sheet: every visual action changes real project
state. Creating a folder block creates the folder; deleting it deletes the
folder, after showing what depends on it.
"""

from __future__ import annotations

import ast
import os
import re
from typing import Any

from backend.whiteboard import codegen
from backend.whiteboard.refactor import resolve_from
from backend.whiteboard.engine import Context
from backend.whiteboard.model import Block, pinned
from backend.whiteboard.ops.placement import apply_position
from backend.whiteboard.registry import OpError, flag, operation

JS_IMPORT = re.compile(r"""(?:from\s+|require\(\s*|import\s+)['"](\.{1,2}/[^'"]+)['"]""")
JS_EXTENSIONS = ("", ".js", ".ts", ".jsx", ".tsx", ".mjs", "/index.js", "/index.ts")


# Input: ctx (Context), folder (str)
# Output: str | None
# Processing: The id of the folder block for a path, creating the chain if missing.
# Changed by: Sourish
# Date: 2026-10-22
def ensure_folder_block(ctx: Context, folder: str) -> str | None:
    folder = folder.strip("/")
    if not folder or folder == ".":
        return None
    existing = ctx.board.block_for_path(folder)
    if existing:
        return existing.id
    parent = ensure_folder_block(ctx, os.path.dirname(folder))
    block = ctx.new_block("folder", os.path.basename(folder), parent=parent, path=folder,
                          id_hint=folder.replace("/", "-"))
    return block.id


# Input: ctx (Context)
# Output: dict[str, str]
# Processing: Every Python module name in the project mapped to its file.
# Changed by: Claude Code
# Date: 2026-11-21
def python_modules(ctx: Context) -> dict[str, str]:
    return {codegen.module_name(f): f for f in ctx.fs.files_under() if f.endswith(".py")}


# Input: ctx (Context), path (str), modules (dict[str, str] | None)
# Output: list[str]
# Processing: Project files a source file imports (Python and JavaScript/TypeScript). Pass ``modules`` (from python_modules) when checking many files, so the project is listed only once.
# Changed by: Claude Code
# Date: 2026-11-21
def imports_of(ctx: Context, path: str, modules: dict[str, str] | None = None) -> list[str]:
    language = codegen.language_of(path)
    try:
        text = ctx.fs.read(path)
    except OpError:
        return []
    found: list[str] = []
    if language == "python":
        modules = python_modules(ctx) if modules is None else modules
        try:
            tree = ast.parse(text)
        except SyntaxError:
            return []
        for node in ast.walk(tree):
            names = []
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom):
                # Relative imports (from .util import x) resolve against this file's package.
                module = resolve_from(path, node)
                names = [module] + [f"{module}.{a.name}" for a in node.names] if module else []
            for name in names:
                if name in modules and modules[name] != path:
                    found.append(modules[name])
    elif language in ("javascript", "typescript"):
        base = os.path.dirname(path)
        for match in JS_IMPORT.finditer(text):
            target = os.path.normpath(os.path.join(base, match.group(1))).replace(os.sep, "/")
            for ext in JS_EXTENSIONS:
                if ctx.fs.path(target + ext).is_file():
                    found.append(target + ext)
                    break
    return sorted(set(found))


# Input: ctx (Context), path (str)
# Output: list[str]
# Processing: Project files that import any file under a path (the project is listed once).
# Changed by: Claude Code
# Date: 2026-11-21
def importers_of(ctx: Context, path: str) -> list[str]:
    prefix = path.strip("/")
    inside = lambda f: f == prefix or f.startswith(prefix + "/")  # noqa: E731
    modules = python_modules(ctx)
    return sorted(
        f for f in ctx.fs.files_under()
        if not inside(f) and codegen.language_of(f) and any(inside(t) for t in imports_of(ctx, f, modules))
    )


# Input: ctx (Context), path (str)
# Output: list[Block]
# Processing: Blocks that stand for a path or anything under it, with their children.
# Changed by: Sourish
# Date: 2026-10-22
def blocks_under(ctx: Context, path: str) -> list[Block]:
    prefix = path.strip("/")
    roots = [b for b in ctx.board.blocks.values() if b.path and
             (b.path.strip("/") == prefix or b.path.strip("/").startswith(prefix + "/") or
              (b.meta.get("file") or "").startswith(prefix + "/"))]
    seen = {b.id: b for b in roots}
    for root in roots:
        for child in ctx.board.descendants(root.id):
            seen[child.id] = child
    return list(seen.values())


# Input: ctx (Context), ids (set[str])
# Output: dict[str, list[str]]
# Processing: Remove blocks with their arrows and endpoints.
# Changed by: Sourish
# Date: 2026-10-22
def drop_blocks(ctx: Context, ids: set[str]) -> dict[str, list[str]]:
    arrows = [a.id for a in ctx.board.arrows.values() if a.source in ids or a.target in ids]
    endpoints = [e.id for e in ctx.board.endpoints.values() if e.service in ids]
    arrows += [a.id for a in ctx.board.arrows.values() if a.endpoint in endpoints and a.id not in arrows]
    for arrow_id in arrows:
        del ctx.board.arrows[arrow_id]
    for endpoint_id in endpoints:
        del ctx.board.endpoints[endpoint_id]
    for block_id in ids:
        ctx.board.blocks.pop(block_id, None)
    for agent in ctx.board.agents.values():
        if agent.block in ids:
            agent.block = ""
    return {"blocks": sorted(ids), "arrows": sorted(arrows), "endpoints": sorted(endpoints)}


# Input: ctx (Context), path (str), at, anchor, right_of, left_of, below, above, inside, gap (Any)
# Output: dict[str, Any]
# Processing: Create a real folder and its block.
# Changed by: Sourish
# Date: 2026-10-22
@operation("folder.create", command="/folder create", tool="plovane_folder_create", group="plovane_project")
def create_folder(ctx: Context, path: str, at: Any = None, anchor: str | None = None, right_of: str | None = None,
                  left_of: str | None = None, below: str | None = None, above: str | None = None,
                  inside: str | None = None, gap: Any = None) -> dict[str, Any]:
    """Creates a real folder in the project and its block on the whiteboard."""
    folder = path.strip("/")
    ctx.require(folder)
    if ctx.fs.exists(folder) and not ctx.fs.is_dir(folder):
        raise OpError("CONFLICT", f"{folder} exists and is a file")
    existing = ctx.board.block_for_path(folder)
    if existing:
        raise OpError("CONFLICT", f"{folder} already has a block ({existing.id})")
    ctx.fs.mkdir(folder)
    block_id = ensure_folder_block(ctx, folder)
    block = ctx.board.blocks[block_id]
    apply_position(ctx, block, at=at, anchor=anchor, right_of=right_of, left_of=left_of, below=below,
                   above=above, inside=inside, gap=gap)
    return {"block": block.id, "path": folder, "layer": block.layer}


# Input: ctx (Context), path (str), template (str | None), at, anchor, right_of, left_of, below, above, inside, gap (Any)
# Output: dict[str, Any]
# Processing: Create a real file, optionally from a template, and its block.
# Changed by: Sourish
# Date: 2026-10-22
@operation("file.create", command="/file create", tool="plovane_file_create", group="plovane_project")
def create_file(ctx: Context, path: str, template: str | None = None, at: Any = None, anchor: str | None = None,
                right_of: str | None = None, left_of: str | None = None, below: str | None = None,
                above: str | None = None, inside: str | None = None, gap: Any = None) -> dict[str, Any]:
    """Creates a real file, optionally from a starter template."""
    file = path.strip("/")
    ctx.require(file)
    if ctx.fs.exists(file):
        raise OpError("CONFLICT", f"{file} already exists")
    ctx.fs.write(file, codegen.file_content(file, template))
    parent = ensure_folder_block(ctx, os.path.dirname(file))
    block = ctx.new_block("file", os.path.basename(file), parent=parent, path=file,
                          meta={"language": codegen.language_of(file)})
    apply_position(ctx, block, at=at, anchor=anchor, right_of=right_of, left_of=left_of, below=below,
                   above=above, inside=inside, gap=gap)
    return {"block": block.id, "path": file, "template": template, "layer": block.layer}


# Input: ctx (Context), path (str), content (str), base (str | None)
# Output: dict[str, Any]
# Processing: Write new text to an existing file, refusing if it changed since it was opened, and refresh its blocks.
# Changed by: Claude Code
# Date: 2026-11-14
@operation("file.save", command="/file save", tool="plovane_file_save", group="plovane_project")
def save_file(ctx: Context, path: str, content: str, base: str | None = None) -> dict[str, Any]:
    """Saves edited text to a file; refuses if it changed on disk since it was opened (base: its text or version)."""
    from backend.whiteboard.ops.blocks import symbol_blocks  # blocks imports this module
    from backend.whiteboard.ops.code import version_of

    file = path.strip("/")
    ctx.require(file)
    if not ctx.fs.exists(file) or ctx.fs.is_dir(file):
        raise OpError("NOT_FOUND", f"no file {file}; create it with /file create")
    if not isinstance(content, str):
        raise OpError("INVALID_ARGS", "content must be the file's full text")
    current = ctx.fs.read(file)
    # base is the text the editor opened (whiteboard) or its version_of fingerprint (code view).
    if base is not None and base != current and base != version_of(current):
        raise OpError("CONFLICT", f"{file} changed on disk after it was opened; reload it before saving")
    lines = content.count("\n") + (bool(content) and not content.endswith("\n"))
    if content == current:
        return {"path": file, "changed": False, "saved": False, "version": version_of(current), "lines": lines,
                "blocks_refreshed": False}
    ctx.fs.write(file, content)
    refreshed = False
    owner = ctx.board.block_for_path(file)
    if owner is not None and codegen.language_of(file):
        if any(b.kind in ("function", "class") and b.meta.get("file") == file for b in ctx.board.blocks.values()):
            try:
                symbol_blocks(ctx, owner)
            except SyntaxError:
                ctx.notes.append(f"{file} does not parse now; function blocks keep their old line numbers")
        if any(b.meta.get("source") == "ai" and b.meta.get("file") == file for b in ctx.board.blocks.values()):
            ctx.notes.append(f"the AI blocks of {file} may point at old lines; run /blocks generate {file} to redo them")
    steps = [b for b in ctx.board.blocks.values() if b.kind == "step" and b.meta.get("file") == file]
    if steps and not any(b.meta.get("changed") for b in steps):
        try:
            ctx.execute("file.blocks", {"file": file, "detail": steps[0].meta.get("detail", 2)})
            refreshed = True
        except OpError as exc:
            ctx.notes.append(f"saved, but the plain-English blocks could not be rebuilt: {exc.message}")
    return {"path": file, "changed": True, "saved": True, "version": version_of(content), "lines": lines,
            "blocks_refreshed": refreshed}


# Input: ctx (Context), under (str)
# Output: dict[str, Any]
# Processing: Every file in the project, or under one folder, for opening.
# Changed by: Claude
# Date: 2026-11-10
@operation("file.list", command="/files", tool="plovane_file_list", group="plovane_project", mutates=False)
def list_files(ctx: Context, under: str = "") -> dict[str, Any]:
    """Lists the project's files (skipping .git, node_modules and virtual environments)."""
    base = under.strip("/")
    ctx.require(base or None, "read")
    if base and not ctx.fs.is_dir(base):
        raise OpError("NOT_FOUND", f"no folder {base}")
    files = ctx.fs.files_under(base)
    return {"under": base, "count": len(files), "truncated": len(files) > MAX_LISTED,
            "files": [{"path": f, "language": codegen.language_of(f), "on_board": ctx.board.block_for_path(f) is not None}
                      for f in files[:MAX_LISTED]]}


MAX_LISTED = 5000
"""Most files /files returns; a bigger project is listed folder by folder."""


# Input: ctx (Context), path (str)
# Output: dict[str, Any]
# Processing: Delete a folder after listing what depends on it.
# Changed by: Sourish
# Date: 2026-10-22
@operation("folder.delete", command="/folder delete", tool="plovane_folder_delete", group="plovane_project",
           confirm=True)
def delete_folder(ctx: Context, path: str) -> dict[str, Any]:
    """Deletes a folder after showing what depends on it."""
    folder = path.strip("/")
    ctx.require(folder)
    if not ctx.fs.is_dir(folder):
        raise OpError("NOT_FOUND", f"no folder {folder}")
    importers = importers_of(ctx, folder)
    blocks = blocks_under(ctx, folder)
    ids = {b.id for b in blocks}
    incoming = [a.id for a in ctx.board.arrows.values() if a.target in ids and a.source not in ids]
    files = ctx.fs.files_under(folder)
    ctx.fs.remove(folder)
    removed = drop_blocks(ctx, ids)
    if importers:
        ctx.notes.append(f"these files import from {folder} and will break: {importers}")
    return {"deleted": folder, "files": files, "importers": importers, "incoming_arrows": incoming,
            "removed": removed}


PACK_GAP, PACK_PAD, PACK_HEAD = 24, 20, 48
"""Space between packed blocks, around them inside a folder, and above them for the folder's title."""


# Input: children (list[Block]), origin (tuple[float, float]), ratio (float)
# Output: tuple[float, float]
# Processing: Lay blocks out left to right in rows of a roughly fixed width; return the size used.
# Changed by: Claude
# Date: 2026-11-10
def pack_rows(children: list[Block], origin: tuple[float, float], ratio: float) -> tuple[float, float]:
    area = sum((b.w + PACK_GAP) * (b.h + PACK_GAP) for b in children)
    limit = max(max(b.w for b in children), (area * ratio) ** 0.5)
    x, y, row_h, used_w = 0.0, 0.0, 0.0, 0.0
    for block in children:
        if x and x + block.w > limit:
            x, y, row_h = 0.0, y + row_h + PACK_GAP, 0.0
        if not pinned(block, "x", "y"):
            block.x, block.y = origin[0] + x, origin[1] + y
        x += block.w + PACK_GAP
        row_h = max(row_h, block.h)
        used_w = max(used_w, x - PACK_GAP)
    return used_w, y + row_h


# Input: ctx (Context), added (list[str])
# Output: None
# Processing: Pack each imported folder tree bottom-up so nothing overlaps, then lay out the top-level folders.
# Changed by: Claude
# Date: 2026-11-10
def pack_tree(ctx: Context, added: list[str]) -> None:
    """Folders first, then files, by name. Each folder is sized to hold its packed children
    below its title, so zooming into it shows them inside it."""
    blocks = ctx.board.blocks
    order = lambda b: (b.kind != "folder", b.name.lower())  # noqa: E731

    # Input: block (Block)
    # Output: None
    # Processing: Pack one folder's children (after packing theirs) and size the folder to fit.
    # Changed by: Claude
    # Date: 2026-11-10
    def pack(block: Block) -> None:
        children = sorted((b for b in blocks.values() if b.parent == block.id and b.kind in ("folder", "file")),
                          key=order)
        if not children:
            return
        for child in children:
            pack(child)
        w, h = pack_rows(children, (PACK_PAD, PACK_HEAD), ratio=1.8)
        if not pinned(block, "w", "h"):
            block.w, block.h = max(block.w, w + 2 * PACK_PAD), max(block.h, h + PACK_HEAD + PACK_PAD)

    roots = []
    for block_id in added:
        block = blocks.get(block_id)
        while block is not None and block.parent in blocks:
            block = blocks[block.parent]
        if block is not None and block not in roots:
            roots.append(block)
    for root in roots:
        pack(root)
    top = sorted((b for b in blocks.values() if b.parent is None and b.kind in ("folder", "file")), key=order)
    if top:
        pack_rows(top, (40, 40), ratio=2.4)


# Input: ctx (Context), path (str), arrows (Any)
# Output: dict[str, Any]
# Processing: Create blocks for the project's existing folders, files and imports. A first import also adds free one-line summaries and the app's API routes (/summary, /endpoint scan).
# Changed by: Claude Code
# Date: 2026-11-21
@operation("project.import", command="/project import", tool="plovane_project_import", group="plovane_project")
def import_project(ctx: Context, path: str = "", arrows: Any = True) -> dict[str, Any]:
    """Maps existing folders, files and imports onto blocks; Git stays the source of truth."""
    from backend.whiteboard.ops.arrows import new_arrow  # arrows imports this module

    base = path.strip("/")
    ctx.require(base or None, "read")
    added_blocks, added_arrows = [], []
    files = [f for f in ctx.fs.files_under(base) if codegen.language_of(f)]
    for file in files:
        if ctx.board.block_for_path(file):
            continue
        parent = ensure_folder_block(ctx, os.path.dirname(file))
        block = ctx.new_block("file", os.path.basename(file), parent=parent, path=file,
                              meta={"language": codegen.language_of(file)})
        added_blocks.append(block.id)
    missing = []
    for block in ctx.board.blocks.values():
        if block.kind in ("file", "folder") and block.path and not ctx.fs.exists(block.path):
            block.status = "missing"
            missing.append(block.id)
    if flag(arrows):
        modules = python_modules(ctx)
        for file in files:
            source = ctx.board.block_for_path(file)
            for target_path in imports_of(ctx, file, modules):
                target = ctx.board.block_for_path(target_path)
                if source and target and not any(
                        a.source == source.id and a.target == target.id and a.type == "import"
                        for a in ctx.board.arrows.values()):
                    added_arrows.append(new_arrow(ctx, source, target, "import").id)
    extras: dict[str, Any] = {}
    if added_blocks:
        pack_tree(ctx, added_blocks)
        # A new project also gets its free one-line summaries and its API routes, in the same undo step.
        for op, args, key in (("code.summary", {"target": base}, "summaries"), ("endpoint.scan", {"under": base}, "routes")):
            try:
                extras[key] = ctx.execute(op, args)
            except OpError as exc:  # e.g. an agent without whole-board access: the import still stands
                ctx.notes.append(f"{op} skipped: {exc.message}")
    return {"files_found": len(files), "blocks_added": added_blocks, "import_arrows_added": added_arrows,
            "missing_on_disk": missing, "summarised": sum(1 for v in ((extras.get("summaries") or {}).get("files") or {}).values() if v),
            "routes_found": (extras.get("routes") or {}).get("routes", 0)}


# Input: ctx (Context), paths (Any)
# Output: dict[str, Any]
# Processing: Bring the board up to date with the code after files changed outside the whiteboard: new files get blocks, deleted ones are marked missing, import arrows follow the imports, and summaries, routes and outside APIs are re-read; one undo step, no AI.
# Changed by: Claude Code
# Date: 2026-11-25
@operation("project.refresh", command="/project refresh", tool="plovane_project_refresh", group="plovane_project")
def refresh_project(ctx: Context, paths: Any = None) -> dict[str, Any]:
    """Updates the board from the code after files changed outside the whiteboard (new, edited or deleted files)."""
    ctx.require(None, "read")
    wanted = [str(p).strip("/") for p in (paths if isinstance(paths, list) else str(paths or "").split(",")) if str(p).strip("/")]
    imported = ctx.execute("project.import", {"arrows": True})
    modules = python_modules(ctx)
    dropped: list[str] = []
    for path in wanted or [b.path for b in ctx.board.blocks.values() if b.kind == "file" and b.path]:
        source = ctx.board.block_for_path(path)
        if source is None:
            continue
        still = {t for t in imports_of(ctx, path, modules)} if ctx.fs.exists(path) else set()
        for arrow in list(ctx.board.arrows.values()):
            if arrow.source != source.id or arrow.type != "import":
                continue
            target = ctx.board.blocks.get(arrow.target)
            if target is None or target.path not in still:
                ctx.board.arrows.pop(arrow.id)
                dropped.append(arrow.id)
    extras: dict[str, Any] = {}
    for op, args, key in (("code.summary", {}, "summaries"), ("endpoint.scan", {}, "routes"),
                          ("outside.scan", {}, "outside_apis")):
        try:
            extras[key] = ctx.execute(op, args)
        except OpError as exc:
            ctx.notes.append(f"{op} skipped: {exc.message}")
    return {"paths": wanted, "blocks_added": imported["blocks_added"],
            "import_arrows_added": imported["import_arrows_added"], "import_arrows_removed": dropped,
            "missing_on_disk": imported["missing_on_disk"],
            "routes_found": (extras.get("routes") or {}).get("routes", 0),
            "outside_apis": sorted((extras.get("outside_apis") or {}).get("found") or {})}
