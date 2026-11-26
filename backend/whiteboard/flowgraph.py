"""Flowcharts from code: what a Python function actually does, step by step.

:func:`flow_of` reads a function with ``ast`` and returns the same chart shape
:mod:`backend.whiteboard.mermaid` uses, so it draws with ``mermaid.to_board``
and exports with :func:`to_mermaid`:

* runs of plain statements become one step, named by what they call or set;
* ``if``/``elif``/``else`` become a decision with yes / no arrows;
* ``for`` / ``while`` become a loop that points back to itself;
* ``try`` / ``except`` become the step plus a red error path per exception;
* ``return`` and ``raise`` end their path;
* a call to another function in the project becomes a call step, and with
  ``depth`` it is opened as a group holding that function's own flow.

Nothing runs; it is read from the code, so the flow is exact for what the
code says. Calls through dependency injection, events or reflection are not
followed (a trace from ``/trace run`` shows those).
"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Any

MAX_LABEL = 48
MAX_NODES = 60


# Input: root (Path), files (list[str])
# Output: dict[str, list[tuple[str, ast.AST]]]
# Processing: Every function and method in the project's Python files, by name, with the file it is in.
# Changed by: Claude Code
# Date: 2026-11-26
def index_functions(root: Path, files: list[str]) -> dict[str, list[tuple[str, ast.AST]]]:
    found: dict[str, list[tuple[str, ast.AST]]] = {}
    for rel in files:
        if not rel.endswith(".py"):
            continue
        try:
            tree = ast.parse((root / rel).read_text(encoding="utf-8", errors="replace"))
        except (OSError, SyntaxError):
            continue
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                found.setdefault(node.name, []).append((rel, node))
    return found


# Input: index (dict), entry (str), routes (dict[str, str] | None)
# Output: tuple[str, ast.AST]
# Processing: The function an entry names: "name", "file.py:name", or a route like "POST /checkout" (its handler); ValueError when none or several match.
# Changed by: Claude Code
# Date: 2026-11-26
def find_entry(index: dict[str, list[tuple[str, ast.AST]]], entry: str,
               routes: dict[str, str] | None = None) -> tuple[str, ast.AST]:
    text = entry.strip()
    words = text.split()
    if len(words) == 2 and words[0].upper() in ("GET", "POST", "PUT", "PATCH", "DELETE") and words[1].startswith("/"):
        method, path = words[0].upper(), words[1]
        for candidates in index.values():
            for rel, node in candidates:
                if routes is not None and rel not in routes.values():
                    continue
                for deco in getattr(node, "decorator_list", []):
                    source = ast.unparse(deco)
                    if f"'{path}'" in source or f'"{path}"' in source:
                        if method.lower() in source.lower() or "route" in source.lower():
                            return rel, node
        raise ValueError(f"no handler found for {method} {path}")
    file, _, name = text.rpartition(":")
    candidates = [c for c in index.get(name, []) if not file or c[0] == file]
    if not candidates:
        raise ValueError(f"no function {text!r} in the project's Python files")
    if len(candidates) > 1:
        raise ValueError(f"{name!r} is in {', '.join(sorted(c[0] for c in candidates))}; say which, e.g. {candidates[0][0]}:{name}")
    return candidates[0]


# Input: text (str)
# Output: str
# Processing: Shorten code text to a readable label.
# Changed by: Claude Code
# Date: 2026-11-26
def short(text: str) -> str:
    text = " ".join(text.split())
    return text if len(text) <= MAX_LABEL else text[: MAX_LABEL - 1] + "…"


# Input: statement (ast.stmt)
# Output: str
# Processing: What one plain statement does, in a few words: the call it makes or what it sets.
# Changed by: Claude Code
# Date: 2026-11-26
def describe(statement: ast.stmt) -> str:
    calls = [ast.unparse(n.func) for n in ast.walk(statement) if isinstance(n, ast.Call)]
    if isinstance(statement, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
        targets = statement.targets if isinstance(statement, ast.Assign) else [statement.target]
        names = ", ".join(ast.unparse(t) for t in targets)
        return short(f"{names} = {calls[0]}()" if calls else f"set {names}")
    if calls:
        return short(f"{calls[0]}()")
    return short(ast.unparse(statement))


class FlowBuilder:
    """Turns a function body into chart nodes and edges."""

    # Input: index (dict), depth (int)
    # Output: None
    # Processing: Start an empty chart; depth is how many levels of project calls to open as groups.
    # Changed by: Claude Code
    # Date: 2026-11-26
    def __init__(self, index: dict[str, list[tuple[str, ast.AST]]], depth: int = 0) -> None:
        self.index, self.depth = index, depth
        self.nodes: dict[str, dict[str, Any]] = {}
        self.edges: list[dict[str, Any]] = []
        self.groups: dict[str, str] = {}
        self.count = 0
        self.returns: list[list[tuple[str, str]]] = []  # per opened call: its returns, which flow back to the caller
        self.raises: list[list[str]] = []  # per try block: raises inside it, which flow to its handlers

    # Input: label (str), shape (str), group (str | None)
    # Output: str
    # Processing: Add a node and return its id.
    # Changed by: Claude Code
    # Date: 2026-11-26
    def node(self, label: str, shape: str, group: str | None) -> str:
        self.count += 1
        nid = f"n{self.count}"
        self.nodes[nid] = {"label": label, "shape": shape, "group": group}
        return nid

    # Input: ends (list[tuple[str, str]]), target (str)
    # Output: None
    # Processing: Join every open end (node, arrow label) to a target.
    # Changed by: Claude Code
    # Date: 2026-11-26
    def join(self, ends: list[tuple[str, str]], target: str, style: str = "arrow") -> None:
        for source, label in ends:
            self.edges.append({"from": source, "to": target, "label": label, "style": style, "both": False})

    # Input: statement (ast.stmt)
    # Output: str | None
    # Processing: The name of a project function a plain statement calls, if any.
    # Changed by: Claude Code
    # Date: 2026-11-26
    def project_call(self, statement: ast.stmt) -> str | None:
        for n in ast.walk(statement):
            if isinstance(n, ast.Call):
                name = n.func.attr if isinstance(n.func, ast.Attribute) else getattr(n.func, "id", None)
                if name and len(self.index.get(name, [])) == 1:
                    return name
        return None

    # Input: body (list[ast.stmt]), ends (list[tuple[str, str]]), group (str | None), level (int)
    # Output: list[tuple[str, str]]
    # Processing: Add the flow of a block of statements after the given open ends; return the ends left open.
    # Changed by: Claude Code
    # Date: 2026-11-26
    def block(self, body: list[ast.stmt], ends: list[tuple[str, str]], group: str | None, level: int) -> list[tuple[str, str]]:
        plain: list[ast.stmt] = []

        # Input: none
        # Output: None
        # Processing: Turn the run of plain statements gathered so far into one step (or a call step).
        # Changed by: Claude Code
        # Date: 2026-11-26
        def flush() -> None:
            nonlocal ends, plain
            while plain:
                callee = None
                for i, statement in enumerate(plain):
                    callee = self.project_call(statement)
                    if callee:
                        break
                run = plain[:i] if callee and i else plain if not callee else []
                if run:
                    labels = [describe(s) for s in run[:2]]
                    label = "; ".join(labels) + (f" (+{len(run) - 2})" if len(run) > 2 else "")
                    step = self.node(short(label), "box", group)
                    self.join(ends, step)
                    ends = [(step, "")]
                    plain = plain[len(run):]
                if callee:
                    statement = plain.pop(0)
                    ends = self.call(statement, callee, ends, group, level)
                else:
                    plain = []

        for statement in body:
            if len(self.nodes) >= MAX_NODES:
                break
            if isinstance(statement, (ast.If, ast.For, ast.AsyncFor, ast.While, ast.Try, ast.Return, ast.Raise)):
                flush()
            if isinstance(statement, ast.If):
                test = self.node(short(f"{ast.unparse(statement.test)}?"), "decision", group)
                self.join(ends, test)
                yes = self.block(statement.body, [(test, "yes")], group, level)
                no = self.block(statement.orelse, [(test, "no")], group, level) if statement.orelse else [(test, "no")]
                ends = yes + no
            elif isinstance(statement, (ast.For, ast.AsyncFor, ast.While)):
                head = (f"for each {ast.unparse(statement.target)} in {ast.unparse(statement.iter)}"
                        if not isinstance(statement, ast.While) else f"while {ast.unparse(statement.test)}")
                loop = self.node(short(head), "hexagon", group)
                self.join(ends, loop)
                inner = self.block(statement.body, [(loop, "each")], group, level)
                self.join(inner, loop, style="dotted")
                ends = [(loop, "done")]
            elif isinstance(statement, ast.Try):
                self.raises.append([])
                start = len(self.nodes)
                ends = self.block(statement.body, ends, group, level)
                raised = self.raises.pop()
                inside = [nid for nid in list(self.nodes)[start:]]
                for handler in statement.handlers:
                    kind = ast.unparse(handler.type) if handler.type else "any error"
                    err = self.node(short(f"on {kind}"), "flag", group)
                    # The error comes from a raise inside the try (also inside calls opened there), else from its steps.
                    sources = raised or inside[-1:] or [e[0] for e in ends]
                    self.join([(r, "") for r in sources], err, style="dotted")
                    ends = ends + self.block(handler.body, [(err, "")], group, level)
                ends = self.block(statement.finalbody, ends, group, level) if statement.finalbody else ends
            elif isinstance(statement, ast.Return):
                end = self.node(short(f"return {ast.unparse(statement.value)}" if statement.value else "return"),
                                "round" if self.returns else "stadium", group)
                self.join(ends, end)
                if self.returns:
                    self.returns[-1].append((end, ""))  # inside an opened call: carry on in the caller
                ends = []
            elif isinstance(statement, ast.Raise):
                end = self.node(short(f"raise {ast.unparse(statement.exc)}" if statement.exc else "raise"), "flag", group)
                self.join(ends, end, style="dotted")
                if self.raises:
                    self.raises[-1].append(end)  # caught by the try around it
                ends = []
            elif isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Pass)) or (
                    isinstance(statement, ast.Expr) and isinstance(statement.value, ast.Constant)):
                continue  # nested definitions, pass and docstrings are not steps
            else:
                plain.append(statement)
        flush()
        return ends

    # Input: statement (ast.stmt), callee (str), ends (list), group (str | None), level (int)
    # Output: list[tuple[str, str]]
    # Processing: A call to a project function: a call step, opened as a group with the callee's own flow while depth allows.
    # Changed by: Claude Code
    # Date: 2026-11-26
    def call(self, statement: ast.stmt, callee: str, ends: list[tuple[str, str]], group: str | None,
             level: int) -> list[tuple[str, str]]:
        rel, node = self.index[callee][0]
        step = self.node(short(f"{describe(statement)} ({rel})"), "subroutine", group)
        self.join(ends, step)
        if level >= self.depth:
            return [(step, "")]
        gid = f"g{len(self.groups) + 1}"
        self.groups[gid] = f"{callee}() in {rel}"
        self.returns.append([])
        inner = self.block(node.body, [(step, "")], gid, level + 1)
        inner += self.returns.pop()
        done = self.node(f"back from {callee}()", "round", group)
        self.join(inner, done)
        return [(done, "")]

    # Input: rel (str), function (ast.AST)
    # Output: dict[str, Any]
    # Processing: The chart for one function: start, its body, and an end for paths that fall off the bottom.
    # Changed by: Claude Code
    # Date: 2026-11-26
    def build(self, rel: str, function: ast.AST) -> dict[str, Any]:
        start = self.node(f"{function.name}() in {rel}", "stadium", None)
        ends = self.block(function.body, [(start, "")], None, 0)
        if ends:
            self.join(ends, self.node("done", "stadium", None))
        return {"direction": "TD", "nodes": self.nodes, "edges": self.edges, "groups": self.groups}


# Input: root (Path), files (list[str]), entry (str), depth (int), routes (dict[str, str] | None)
# Output: dict[str, Any]
# Processing: The flowchart of the function an entry names (see find_entry), opening project calls depth levels deep.
# Changed by: Claude Code
# Date: 2026-11-26
def flow_of(root: Path, files: list[str], entry: str, depth: int = 0,
            routes: dict[str, str] | None = None) -> dict[str, Any]:
    index = index_functions(root, files)
    rel, function = find_entry(index, entry, routes)
    return FlowBuilder(index, depth).build(rel, function)


SHAPE_SYNTAX = {"box": ("[", "]"), "decision": ("{", "}"), "hexagon": ("{{", "}}"), "stadium": ("([", "])"),
                "subroutine": ("[[", "]]"), "flag": (">", "]"), "round": ("(", ")"), "database": ("[(", ")]"),
                "circle": ("((", "))"), "parallelogram": ("[/", "/]"), "trapezoid": ("[/", "\\]")}


# Input: chart (dict[str, Any])
# Output: str
# Processing: A chart as Mermaid flowchart text, so a flow can be pasted into a README.
# Changed by: Claude Code
# Date: 2026-11-26
def to_mermaid(chart: dict[str, Any]) -> str:
    lines = [f"flowchart {chart.get('direction', 'TD')}"]

    # Input: nid (str)
    # Output: str
    # Processing: One node in Mermaid syntax, its label quoted.
    # Changed by: Claude Code
    # Date: 2026-11-26
    def node_text(nid: str) -> str:
        node = chart["nodes"][nid]
        opener, closer = SHAPE_SYNTAX.get(node.get("shape"), ("[", "]"))
        return f'{nid}{opener}"{node["label"].replace(chr(34), chr(39))}"{closer}'

    for gid, title in chart.get("groups", {}).items():
        lines.append(f'  subgraph {gid} ["{title}"]')
        lines.extend(f"    {node_text(nid)}" for nid, n in chart["nodes"].items() if n.get("group") == gid)
        lines.append("  end")
    lines.extend(f"  {node_text(nid)}" for nid, n in chart["nodes"].items() if not n.get("group"))
    for e in chart["edges"]:
        arrow = "-.->" if e.get("style") == "dotted" else "-->"
        lines.append(f"  {e['from']} {arrow}" + (f"|{e['label']}|" if e.get("label") else "") + f" {e['to']}")
    return "\n".join(lines)
