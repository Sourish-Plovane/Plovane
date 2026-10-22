"""Safe code changes: symbols, real references, and imports that follow a move.

Renaming by text match breaks code: renaming a function ``total`` must not
touch an unrelated local variable called ``total`` in another file. For
Python this module reads the syntax tree instead:

- :func:`symbols` lists functions, classes and their methods.
- :func:`rename_edits` finds the places that really refer to one symbol: its
  definition, uses not shadowed by a local of the same name, imports of it
  (``from pkg.orders import total``) and attribute uses through the module
  (``orders.total``, ``pkg.orders.total``).
- :func:`rewrite_python_imports` and :func:`rewrite_js_imports` fix import
  statements after files move, both in the files that import a moved file
  and in the moved file's own relative imports.

Every edit is a byte range on one line, because ``ast`` reports columns as
UTF-8 byte offsets.
"""

from __future__ import annotations

import ast
import os
import re
from dataclasses import dataclass

from whiteboard import codegen

JS_SPEC = re.compile(r"""((?:from\s+|require\(\s*|import\s+)['"])(\.{1,2}/[^'"]+)(['"])""")
JS_EXTENSIONS = ("", ".js", ".ts", ".jsx", ".tsx", ".mjs", "/index.js", "/index.ts")


@dataclass(frozen=True)
class Symbol:
    """A function, class or method in a source file."""

    name: str
    qualname: str
    """``Cart.add`` for a method, the bare name otherwise."""
    kind: str
    """``function``, ``class`` or ``method``."""
    start_line: int
    end_line: int
    code: str
    signature: str | None = None
    docstring: str = ""
    parent: str | None = None
    """The class a method belongs to."""
    is_template: bool = False


@dataclass(frozen=True)
class Edit:
    line: int
    """1-based line number."""
    start: int
    """Byte offset where the replaced text starts."""
    end: int
    """Byte offset where it ends."""
    text: str


# -- Symbols -------------------------------------------------------------------------


# Input: text (str), path (str)
# Output: list[Symbol]
# Processing: Every function, class and method in a file, in source order.
# Changed by: Sourish
# Date: 2026-10-22
def symbols(text: str, path: str) -> list[Symbol]:
    """Python is read exactly; other languages fall back to the extractor's
    top-level functions."""
    language = codegen.language_of(path)
    if language != "python":
        from ml.ingest.extract import BRACE_LANGUAGES, extract_braced

        if language not in BRACE_LANGUAGES:
            return []
        return [Symbol(u.name, u.name, "function", u.start_line, u.end_line, u.code, u.signature, u.docstring)
                for u in extract_braced(text, language, library_rules=False)]
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return []
    found: list[Symbol] = []

    # Input: node (ast.AST), kind (str), parent (str | None)
    # Output: Symbol
    # Processing: A Symbol for one definition node.
    # Changed by: Sourish
    # Date: 2026-10-22
    def make(node, kind: str, parent: str | None) -> Symbol:
        start = min([node.lineno, *(d.lineno for d in node.decorator_list)])
        signature = f"({ast.unparse(node.args)})" if not isinstance(node, ast.ClassDef) else None
        return Symbol(node.name, f"{parent}.{node.name}" if parent else node.name, kind, start,
                      node.end_lineno or node.lineno, ast.get_source_segment(text, node) or "",
                      signature, ast.get_docstring(node) or "", parent)

    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            found.append(make(node, "function", None))
        elif isinstance(node, ast.ClassDef):
            found.append(make(node, "class", None))
            for item in node.body:
                if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    found.append(make(item, "method", node.name))
    return found


# Input: text (str), qualname (str)
# Output: ast.AST | None
# Processing: The definition node for a qualified name.
# Changed by: Sourish
# Date: 2026-10-22
def definition(text: str, qualname: str) -> ast.AST | None:
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return None
    scope: list[ast.stmt] = tree.body
    node = None
    for part in qualname.split("."):
        node = next((n for n in scope if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
                     and n.name == part), None)
        if node is None:
            return None
        scope = node.body
    return node


# -- Module names ---------------------------------------------------------------------


# Input: path (str), node (ast.ImportFrom)
# Output: str | None
# Processing: The absolute module an ImportFrom names, resolving relative dots.
# Changed by: Sourish
# Date: 2026-10-22
def resolve_from(path: str, node: ast.ImportFrom) -> str | None:
    if node.level == 0:
        return node.module
    module = codegen.module_name(path)
    package = module.split(".") if path.endswith("__init__.py") else module.split(".")[:-1]
    if node.level - 1 > len(package):
        return None
    base = package[: len(package) - (node.level - 1)]
    return ".".join(base + ([node.module] if node.module else [])) or None


# -- Scopes ---------------------------------------------------------------------------


# Input: node (ast.AST)
# Output: set[str]
# Processing: Names a function, lambda, class body or comprehension binds locally.
# Changed by: Sourish
# Date: 2026-10-22
def local_names(node: ast.AST) -> set[str]:
    bound: set[str] = set()
    declared_global: set[str] = set()
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
        args = node.args
        for arg in [*args.posonlyargs, *args.args, *args.kwonlyargs, args.vararg, args.kwarg]:
            if arg is not None:
                bound.add(arg.arg)
    if isinstance(node, (ast.ListComp, ast.SetComp, ast.GeneratorExp, ast.DictComp)):
        roots = [g.target for g in node.generators]
    elif isinstance(node, ast.Lambda):
        roots = []
    else:
        roots = list(getattr(node, "body", []))
    stack = list(roots)
    while stack:
        current = stack.pop()
        if isinstance(current, ast.Name) and isinstance(current.ctx, (ast.Store, ast.Del)):
            bound.add(current.id)
        elif isinstance(current, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            bound.add(current.name)
            stack.extend(current.decorator_list)
            continue  # its body is its own scope
        elif isinstance(current, (ast.Lambda, ast.ListComp, ast.SetComp, ast.GeneratorExp, ast.DictComp)):
            continue
        elif isinstance(current, (ast.Import, ast.ImportFrom)):
            for alias in current.names:
                bound.add(alias.asname or alias.name.split(".")[0])
        elif isinstance(current, ast.ExceptHandler) and current.name:
            bound.add(current.name)
        elif isinstance(current, (ast.Global, ast.Nonlocal)):
            declared_global.update(current.names)
        stack.extend(ast.iter_child_nodes(current))
    return bound - declared_global


# Input: tree (ast.Module), name (str)
# Output: list[ast.Name]
# Processing: Uses of a module-level name that no inner scope shadows.
# Changed by: Sourish
# Date: 2026-10-22
def global_uses(tree: ast.Module, name: str) -> list[ast.Name]:
    found: list[ast.Name] = []

    # Input: node (ast.AST), shadowed (bool)
    # Output: None
    # Processing: Walk one node, tracking whether name is shadowed here.
    # Changed by: Sourish
    # Date: 2026-10-22
    def visit(node: ast.AST, shadowed: bool) -> None:
        if isinstance(node, ast.Name):
            if node.id == name and not shadowed:
                found.append(node)
            return
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for outer in [*node.decorator_list, *node.args.defaults, *node.args.kw_defaults,
                          *( [node.returns] if node.returns else [])]:
                if outer is not None:
                    visit(outer, shadowed)
            inner = shadowed or name in local_names(node)
            for stmt in node.body:
                visit(stmt, inner)
            return
        if isinstance(node, ast.Lambda):
            for outer in [*node.args.defaults, *node.args.kw_defaults]:
                if outer is not None:
                    visit(outer, shadowed)
            visit(node.body, shadowed or name in local_names(node))
            return
        if isinstance(node, ast.ClassDef):
            for outer in [*node.decorator_list, *node.bases, *(k.value for k in node.keywords)]:
                visit(outer, shadowed)
            # Names bound in a class body do not reach its methods.
            class_shadow = shadowed or name in local_names(node)
            for stmt in node.body:
                visit(stmt, shadowed if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef)) else class_shadow)
            return
        if isinstance(node, (ast.ListComp, ast.SetComp, ast.GeneratorExp, ast.DictComp)):
            inner = shadowed or name in local_names(node)
            visit(node.generators[0].iter, shadowed)
            for index, generator in enumerate(node.generators):
                if index:
                    visit(generator.iter, inner)
                visit(generator.target, inner)
                for condition in generator.ifs:
                    visit(condition, inner)
            for part in ("elt", "key", "value"):
                if hasattr(node, part):
                    visit(getattr(node, part), inner)
            return
        for child in ast.iter_child_nodes(node):
            visit(child, shadowed)

    visit(tree, False)
    return found


# -- Rename -----------------------------------------------------------------------------


# Input: lines (list[str]), node (ast.AST), name (str), new (str)
# Output: Edit | None
# Processing: The edit renaming a def or class statement's own name.
# Changed by: Sourish
# Date: 2026-10-22
def _def_name_edit(lines: list[str], node: ast.AST, name: str, new: str) -> Edit | None:
    raw = lines[node.lineno - 1].encode()
    match = re.compile(rb"(?:def|class)\s+(" + re.escape(name.encode()) + rb")\b").search(raw, node.col_offset)
    return Edit(node.lineno, match.start(1), match.end(1), new) if match else None


# Input: node (ast.AST), new (str)
# Output: Edit
# Processing: The edit renaming an Attribute node's attribute.
# Changed by: Sourish
# Date: 2026-10-22
def _attr_edit(node: ast.Attribute, new: str) -> Edit:
    end = node.end_col_offset
    return Edit(node.end_lineno, end - len(node.attr.encode()), end, new)


# Input: files (dict[str, str]), defining (str), qualname (str), new (str)
# Output: tuple[dict[str, list[Edit]], list[str]]
# Processing: Every edit that renames one Python symbol across the project, and notes.
# Changed by: Sourish
# Date: 2026-10-22
def rename_edits(files: dict[str, str], defining: str, qualname: str, new: str) -> tuple[dict[str, list[Edit]], list[str]]:
    """``files`` maps every project .py path to its text. Methods are renamed
    in their class only (with ``self.``/``cls.`` uses); callers through other
    objects cannot be found reliably, so they are named in the notes."""
    edits: dict[str, list[Edit]] = {}
    notes: list[str] = []
    text = files[defining]
    tree = ast.parse(text)
    lines = text.splitlines()
    node = definition(text, qualname)
    if node is None:
        return {}, [f"{qualname} is not defined in {defining}"]
    name = node.name
    own: list[Edit] = [e for e in [_def_name_edit(lines, node, name, new)] if e]

    if "." in qualname:  # a method: rename within its class
        owner = definition(text, qualname.rsplit(".", 1)[0])
        for child in ast.walk(owner):
            if isinstance(child, ast.Attribute) and child.attr == name and isinstance(child.value, ast.Name) \
                    and child.value.id in ("self", "cls"):
                own.append(_attr_edit(child, new))
        callers = [p for p, t in files.items() if re.search(rf"\.{re.escape(name)}\s*\(", t) and p != defining]
        if callers:
            notes.append(f"{qualname} may also be called through other objects in {callers}; check those by hand")
        edits[defining] = own
        return edits, notes

    module = codegen.module_name(defining)
    for use in global_uses(tree, name):
        own.append(Edit(use.lineno, use.col_offset, use.end_col_offset, new))
    for child in ast.walk(tree):  # __all__ = ["total", ...]
        if isinstance(child, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "__all__" for t in child.targets):
            for item in ast.walk(child.value):
                if isinstance(item, ast.Constant) and item.value == name:
                    own.append(Edit(item.lineno, item.col_offset + 1, item.end_col_offset - 1, new))
    edits[defining] = own

    for path, source in files.items():
        if path == defining:
            continue
        try:
            other = ast.parse(source)
        except SyntaxError:
            continue
        found: list[Edit] = []
        module_aliases: set[str] = set()
        bound_directly = False
        for child in ast.walk(other):
            if isinstance(child, ast.ImportFrom):
                resolved = resolve_from(path, child)
                for alias in child.names:
                    if resolved == module and alias.name == name:
                        found.append(Edit(alias.lineno, alias.col_offset, alias.col_offset + len(name.encode()), new))
                        bound_directly = bound_directly or alias.asname is None
                    elif resolved and f"{resolved}.{alias.name}" == module:
                        module_aliases.add(alias.asname or alias.name)
            elif isinstance(child, ast.Import):
                for alias in child.names:
                    if alias.name == module:
                        module_aliases.add(alias.asname or alias.name)
        if bound_directly:
            for use in global_uses(other, name):
                found.append(Edit(use.lineno, use.col_offset, use.end_col_offset, new))
        if module_aliases:
            for child in ast.walk(other):
                if isinstance(child, ast.Attribute) and child.attr == name and ast.unparse(child.value) in module_aliases:
                    found.append(_attr_edit(child, new))
        if found:
            edits[path] = found
    return edits, notes


# Input: text (str), edits (list[Edit])
# Output: str
# Processing: Apply byte-range edits to a text, last first so offsets hold.
# Changed by: Sourish
# Date: 2026-10-22
def apply_edits(text: str, edits: list[Edit]) -> str:
    lines = text.splitlines(keepends=True)
    seen = set()
    for edit in sorted(edits, key=lambda e: (e.line, e.start), reverse=True):
        key = (edit.line, edit.start, edit.end)
        if key in seen:
            continue
        seen.add(key)
        raw = lines[edit.line - 1].encode()
        lines[edit.line - 1] = (raw[: edit.start] + edit.text.encode() + raw[edit.end:]).decode()
    return "".join(lines)


# -- Imports after a move -------------------------------------------------------------------


# Input: module (str), moves (dict[str, str])
# Output: str | None
# Processing: Where a module now lives, if it or a parent package moved.
# Changed by: Sourish
# Date: 2026-10-22
def moved_module(module: str | None, moves: dict[str, str]) -> str | None:
    if not module:
        return None
    for old, new in sorted(moves.items(), key=lambda m: -len(m[0])):
        if module == old or module.startswith(old + "."):
            return new + module[len(old):]
    return None


# Input: text (str), path_now (str), path_before (str), moves (dict[str, str])
# Output: str
# Processing: Fix a Python file's imports after modules moved (module name -> module name).
# Changed by: Sourish
# Date: 2026-10-22
def rewrite_python_imports(text: str, path_now: str, path_before: str, moves: dict[str, str]) -> str:
    """Imports of a moved module are pointed at its new name. When this file
    itself moved, its relative imports are rewritten as absolute ones, since
    the dots now count from a different package."""
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return text
    lines = text.splitlines()
    edits: list[Edit] = []
    dotted: dict[str, str] = {}
    self_moved = path_now != path_before
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                target = moved_module(alias.name, moves)
                if target:
                    edits.append(Edit(alias.lineno, alias.col_offset, alias.col_offset + len(alias.name.encode()), target))
                    if alias.asname is None:
                        dotted[alias.name] = target
        elif isinstance(node, ast.ImportFrom):
            resolved = resolve_from(path_before, node)
            target = moved_module(resolved, moves)
            single = node.names[0] if len(node.names) == 1 else None
            submodule = moved_module(f"{resolved}.{single.name}", moves) if resolved and single else None
            if submodule and not target:
                # from pkg import orders, where pkg.orders moved
                package, _, leaf = submodule.rpartition(".")
                alias_text = leaf if leaf == single.name or single.asname else f"{leaf} as {single.name}"
                raw = lines[node.lineno - 1].encode()
                header = re.compile(rb"from\s+[.\w]*\s+import\s+").match(raw, node.col_offset)
                if header and package and node.lineno == node.end_lineno:
                    alias_end = single.col_offset + len(single.name.encode())
                    edits.append(Edit(node.lineno, node.col_offset, alias_end,
                                      f"from {package} import {alias_text}"))
                continue
            if target or (self_moved and node.level > 0 and resolved):
                new_module = target or resolved
                raw = lines[node.lineno - 1].encode()
                header = re.compile(rb"from\s+[.\w]*\s+import").match(raw, node.col_offset)
                if header:
                    edits.append(Edit(node.lineno, header.start(), header.end(), f"from {new_module} import"))
    text = apply_edits(text, edits)
    for old, new in dotted.items():  # uses like pkg.orders.total()
        text = re.sub(rf"(?<![\w.]){re.escape(old)}(?=\.\w)", new, text)
    return text


# Input: text (str), path_now (str), path_before (str), file_moves (dict[str, str]), exists (Callable)
# Output: str
# Processing: Fix a JS/TS file's relative import paths after files moved.
# Changed by: Sourish
# Date: 2026-10-22
def rewrite_js_imports(text: str, path_now: str, path_before: str, file_moves: dict[str, str], exists) -> str:
    """``exists(path)`` says whether a project file exists (after the move)."""
    self_moved = path_now != path_before

    # Input: match (re.Match)
    # Output: str
    # Processing: The import with its path recomputed, or unchanged.
    # Changed by: Sourish
    # Date: 2026-10-22
    def fix(match: re.Match) -> str:
        spec = match.group(2)
        base = os.path.normpath(os.path.join(os.path.dirname(path_before), spec)).replace(os.sep, "/")
        for ext in JS_EXTENSIONS:
            old_target = base + ext
            if old_target in file_moves:
                new_target = file_moves[old_target]
            elif self_moved and exists(old_target):
                new_target = old_target
            else:
                continue
            rel = os.path.relpath(new_target, os.path.dirname(path_now) or ".").replace(os.sep, "/")
            if ext and rel.endswith(ext):
                rel = rel[: -len(ext)]  # keep the import written the way it was
            rel = rel if rel.startswith(".") else "./" + rel
            return match.group(1) + rel + match.group(3)
        return match.group(0)

    return JS_SPEC.sub(fix, text)
