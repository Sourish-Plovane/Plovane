"""Mermaid flowcharts: read the diagrams projects already keep in their docs.

Many READMEs draw their architecture or a flow as a Mermaid ``flowchart`` (or
``graph``) block. :func:`diagrams` finds them in Markdown, :func:`parse` reads
one into nodes, edges and subgraphs, :func:`to_board` lays it out as a
Plovane board (services, databases, queues, outside systems, steps and
decisions, joined by labelled arrows), and :func:`to_sketch` writes the same
picture as ``/sketch`` lines. ``/flow import`` uses this to draw a project's
own diagrams, and the training-data harvester uses it to turn public
projects' diagrams into examples.

The parser covers the flowchart syntax people actually write: node shapes
(``[ ]``, ``( )``, ``([ ])``, ``[[ ]]``, ``[( )]``, ``(( ))``, ``{ }``,
``{{ }}``, ``>  ]``, ``[/ /]``), quoted labels, edges ``--> --- -.-> ==>
--o --x <-->`` with ``|label|`` or ``-- label -->`` labels, chains
(``a --> b --> c``), ``&`` lists, ``subgraph … end`` and ``%%`` comments.
Styling lines (classDef, class, style, click, linkStyle) are skipped.
"""

from __future__ import annotations

import re
from typing import Any

FENCE = re.compile(r"```\s*mermaid\s*\n(.*?)```", re.DOTALL | re.IGNORECASE)
HEADER = re.compile(r"^\s*(?:flowchart|graph)\b\s*([A-Za-z]{2})?", re.IGNORECASE)
SKIP = re.compile(r"^\s*(?:classDef|class|style|click|linkStyle|direction)\b", re.IGNORECASE)
SHAPES = (  # longest openers first
    ("([", "])", "stadium"), ("[[", "]]", "subroutine"), ("[(", ")]", "database"), ("((", "))", "circle"),
    ("{{", "}}", "hexagon"), ("[/", "/]", "parallelogram"), ("[\\", "\\]", "parallelogram"),
    ("[/", "\\]", "trapezoid"), (">", "]", "flag"), ("[", "]", "box"), ("(", ")", "round"), ("{", "}", "decision"),
)
NODE_ID = r"[A-Za-z0-9_][\w.\-]*"
ARROW = re.compile(
    r"\s*(?:"
    r"(?P<lab1>--|-\.|==)\s*(?P<text>[^|>]*?)\s*(?P<tail1>-->|--[ox]|-\.->|\.->|==>|---)"  # -- text -->
    r"|(?P<plain><-->|<==>|-\.->|-->|--[ox]|==>|---|-\.-|===|~~~)(?:\s*\|(?P<piped>[^|]*)\|)?"  # --> |text|
    r")\s*")
DATABASE = re.compile(r"\b(db|database|postgres\w*|mysql|sqlite|sql|mongo\w*|redis|dynamo\w*|elastic\w*|"
                      r"storage|store|s3|bucket|cache|warehouse|bigquery|supabase)\b", re.IGNORECASE)
QUEUE = re.compile(r"\b(queue|kafka|rabbit\w*|sqs|sns|pub/?sub|nats|celery|event bus|stream)\b", re.IGNORECASE)
EXTERNAL = re.compile(r"\b(stripe|openai|anthropic|claude|github|google|aws|azure|gcp|slack|twilio|sendgrid|"
                      r"paypal|auth0|firebase|external|third[- ]party|api\.\w+)\b", re.IGNORECASE)
PERSON = re.compile(r"\b(user|users|customer|client|browser|admin|person|people)\b", re.IGNORECASE)


# Input: text (str)
# Output: list[str]
# Processing: Every Mermaid flowchart in a Markdown text (or the text itself when it is one diagram), in order.
# Changed by: Claude Code
# Date: 2026-11-26
def diagrams(text: str) -> list[str]:
    found = [block for block in FENCE.findall(text) if HEADER.match(block.strip().splitlines()[0] if block.strip() else "")]
    if not found and HEADER.match(text.strip().splitlines()[0] if text.strip() else ""):
        found = [text]
    return found


# Input: raw (str)
# Output: str
# Processing: A node label without quotes, HTML line breaks or markdown emphasis.
# Changed by: Claude Code
# Date: 2026-11-26
def clean_label(raw: str) -> str:
    text = raw.strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in "\"'`":
        text = text[1:-1]
    text = re.sub(r"<br\s*/?>", " ", text, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>|[*_`]|fa:fa-[\w-]+", "", text)
    return " ".join(text.split())


# Input: text (str), pos (int)
# Output: tuple[dict[str, str] | None, int]
# Processing: Read one node at pos (id plus optional shape and label); returns it and where reading stopped.
# Changed by: Claude Code
# Date: 2026-11-26
def read_node(text: str, pos: int) -> tuple[dict[str, str] | None, int]:
    m = re.compile(r"\s*(" + NODE_ID + r")").match(text, pos)
    if not m:
        return None, pos
    node = {"id": m.group(1)}
    pos = m.end()
    for opener, closer, shape in SHAPES:
        if text.startswith(opener, pos):
            inner = pos + len(opener)
            if text.startswith('"', inner):  # a quoted label may hold brackets: n1("calls charge()")
                quote_end = text.find('"', inner + 1)
                end = text.find(closer, quote_end + 1) if quote_end != -1 else -1
            else:
                end = text.find(closer, inner)
            if end == -1:
                continue
            node["label"] = clean_label(text[pos + len(opener):end])
            node["shape"] = shape
            pos = end + len(closer)
            break
    m = re.compile(r":::[\w-]+").match(text, pos)  # a:::class
    return node, (m.end() if m else pos)


# Input: text (str), pos (int)
# Output: tuple[list[dict[str, str]], int]
# Processing: Read "a & b & c" at pos.
# Changed by: Claude Code
# Date: 2026-11-26
def read_group(text: str, pos: int) -> tuple[list[dict[str, str]], int]:
    nodes = []
    while True:
        node, pos = read_node(text, pos)
        if node is None:
            break
        nodes.append(node)
        m = re.compile(r"\s*&").match(text, pos)
        if not m:
            break
        pos = m.end()
    return nodes, pos


# Input: source (str)
# Output: dict[str, Any]
# Processing: One Mermaid flowchart as {"direction", "nodes": {id: {label, shape, group}}, "edges": [...], "groups": {id: title}}.
# Changed by: Claude Code
# Date: 2026-11-26
def parse(source: str) -> dict[str, Any]:
    lines = source.strip().splitlines()
    header = HEADER.match(lines[0]) if lines else None
    if not header:
        raise ValueError("not a Mermaid flowchart (it should start with 'flowchart' or 'graph')")
    direction = (header.group(1) or "TD").upper()
    nodes: dict[str, dict[str, Any]] = {}
    edges: list[dict[str, Any]] = []
    groups: dict[str, str] = {}
    stack: list[str] = []

    # Input: node (dict[str, str])
    # Output: None
    # Processing: Add a node the first time it appears; a later mention with a shape gives it its label.
    # Changed by: Claude Code
    # Date: 2026-11-26
    def remember(node: dict[str, str]) -> None:
        entry = nodes.setdefault(node["id"], {"label": node["id"], "shape": "box", "group": stack[-1] if stack else None})
        if stack and entry["group"] is None:
            entry["group"] = stack[-1]  # listing a node inside a subgraph moves it there
        if "label" in node:
            entry["label"] = node["label"] or node["id"]
            entry["shape"] = node["shape"]

    statements = []
    for line in lines[1:]:
        line = line.split("%%", 1)[0].rstrip()
        statements.extend(part for part in re.split(r";\s*(?=\S)", line) if part.strip())
    for statement in statements:
        text = statement.strip().rstrip(";")
        if not text or SKIP.match(text):
            continue
        sub = re.match(r"subgraph\s+(.*)$", text, re.IGNORECASE)
        if sub:
            spec = sub.group(1).strip()
            m = re.match(r"(" + NODE_ID + r")\s*\[(.*)\]\s*$", spec)
            gid, title = (m.group(1), clean_label(m.group(2))) if m else (re.sub(r"\W+", "_", clean_label(spec)) or f"group{len(groups)}", clean_label(spec))
            groups[gid] = title or gid
            stack.append(gid)
            continue
        if text.lower() == "end":
            if stack:
                stack.pop()
            continue
        left, pos = read_group(text, 0)
        if not left:
            continue
        for node in left:
            remember(node)
        while pos < len(text):
            arrow = ARROW.match(text, pos)
            if not arrow:
                break
            label = clean_label(arrow.group("text") or arrow.group("piped") or "")
            symbol = arrow.group("plain") or arrow.group("tail1") or ""
            right, pos = read_group(text, arrow.end())
            if not right:
                break
            for node in right:
                remember(node)
            for a in left:
                for b in right:
                    edges.append({"from": a["id"], "to": b["id"], "label": label,
                                  "style": "dotted" if "." in symbol else "thick" if "=" in symbol else
                                  "line" if symbol in ("---", "===", "~~~") else "arrow",
                                  "both": symbol.startswith("<")})
            left = right
    for gid in groups:
        nodes.pop(gid, None)  # a subgraph id used in an edge is not a node of its own
    edges = [e for e in edges if e["from"] in nodes and e["to"] in nodes]
    return {"direction": direction, "nodes": nodes, "edges": edges, "groups": groups}


# Input: node (dict[str, Any])
# Output: str
# Processing: The Plovane block kind a diagram node stands for, from its shape and words.
# Changed by: Claude Code
# Date: 2026-11-26
def kind_of(node: dict[str, Any]) -> str:
    label, shape = node.get("label") or "", node.get("shape")
    if shape == "database" or DATABASE.search(label):
        return "database"
    if QUEUE.search(label):
        return "queue"
    if EXTERNAL.search(label):
        return "external"
    if shape in ("decision", "hexagon", "parallelogram", "trapezoid", "flag"):
        return "step"
    return "service"


# Input: chart (dict[str, Any])
# Output: bool
# Processing: Whether a diagram reads as a process (decisions, mostly verbs) rather than a map of parts.
# Changed by: Claude Code
# Date: 2026-11-26
def is_process(chart: dict[str, Any]) -> bool:
    nodes = list(chart["nodes"].values())
    decisions = sum(n.get("shape") == "decision" for n in nodes)
    return bool(nodes) and (decisions >= 1 and decisions * 4 >= len(nodes) or
                            sum(n.get("shape") in ("stadium", "circle") for n in nodes) >= 2)


# Input: chart (dict[str, Any])
# Output: dict[str, int]
# Processing: Each node's column: the longest path to it from a node nothing points at (cycles are cut).
# Changed by: Claude Code
# Date: 2026-11-26
def ranks(chart: dict[str, Any]) -> dict[str, int]:
    rank = {n: 0 for n in chart["nodes"]}
    limit = len(rank)
    for _ in range(limit):
        changed = False
        for e in chart["edges"]:
            if rank[e["to"]] < rank[e["from"]] + 1 <= limit:
                rank[e["to"]], changed = rank[e["from"]] + 1, True
        if not changed:
            break
    return rank


# Input: chart (dict[str, Any]), title (str), steps_only (bool)
# Output: Board
# Processing: A Plovane board drawing the diagram: blocks by kind (all steps with steps_only, for code flows), subgraphs as groups, labelled arrows, laid out in ranks along its direction.
# Changed by: Claude Code
# Date: 2026-11-26
def to_board(chart: dict[str, Any], title: str = "", steps_only: bool = False) -> Any:
    from backend.whiteboard.model import DEFAULT_SIZE, Arrow, Block, default_board, slug

    board = default_board()
    process = is_process(chart)
    ids: dict[str, str] = {}

    # Input: base (str)
    # Output: str
    # Processing: A block id not used on the board yet.
    # Changed by: Claude Code
    # Date: 2026-11-26
    def unique(base: str) -> str:
        candidate, n = base, 2
        while candidate in board.blocks:
            candidate, n = f"{base}-{n}", n + 1
        return candidate

    for gid, name in chart["groups"].items():
        block_id = unique("group-" + slug(name))
        ids["group:" + gid] = block_id
        board.blocks[block_id] = Block(id=block_id, kind="group", name=name, layer=1, w=320, h=220,
                                       meta={"from": "mermaid"})
    rank = ranks(chart)
    across = chart["direction"] in ("LR", "RL")
    columns: dict[int, int] = {}
    appearance = {nid: i for i, nid in enumerate(chart["nodes"])}
    # The board lists blocks by meta.lines, so a step's place in the flow (rank, then first appearance) is its "line".
    order = {nid: n + 1 for n, nid in enumerate(sorted(chart["nodes"], key=lambda k: (rank[k], appearance[k])))}
    for nid, node in chart["nodes"].items():
        kind = "step" if steps_only or (process and kind_of(node) == "service") else kind_of(node)
        w, h = DEFAULT_SIZE.get(kind, (200, 100))
        w, h = max(w, 180), max(h, 70)
        block_id = unique(slug(node["label"]) or slug(nid))
        ids[nid] = block_id
        column, row = rank[nid], columns.get(rank[nid], 0)
        columns[column] = row + 1
        x, y = (60 + column * 300, 60 + row * 160) if across else (60 + row * 300, 60 + column * 160)
        meta = {"from": "mermaid", "mermaid_id": nid, "lines": [order[nid], order[nid]], "flow_order": order[nid]}
        if node.get("shape") in ("decision", "hexagon") and (steps_only or process):
            meta["decision"] = True
        if node.get("shape") == "flag" and (steps_only or process):
            meta["error_path"] = True
        parent = ids.get("group:" + node["group"]) if node.get("group") else None
        board.blocks[block_id] = Block(id=block_id, kind=kind, name=node["label"], layer=1, x=x, y=y, w=w, h=h,
                                       parent=parent, meta=meta)
    if steps_only or process:
        # Say on each step where it leads ("yes → Charge card · no → Show error"): the card reads even where arrows are faint.
        for nid in chart["nodes"]:
            leads = [(f"{e['label']} → " if e["label"] else "→ ") + chart["nodes"][e["to"]]["label"]
                     for e in chart["edges"] if e["from"] == nid]
            board.blocks[ids[nid]].meta["summary"] = " · ".join(leads)[:160] if leads else "end"
    for n, edge in enumerate(chart["edges"]):
        source, target = board.blocks[ids[edge["from"]]], board.blocks[ids[edge["to"]]]
        kind = {"database": "database", "external": "external", "queue": "event"}.get(target.kind, "calls")
        style = {"label": edge["label"]} if edge["label"] else {}
        if edge["style"] == "dotted":
            style["dash"] = 4
        board.arrows[f"a{n + 1}"] = Arrow(id=f"a{n + 1}", source=source.id, target=target.id, type=kind, style=style,
                                          meta={"from": "mermaid"})
    if title:
        board.view["about"] = title
    return board


# Input: chart (dict[str, Any])
# Output: str
# Processing: The diagram as /sketch lines (services, databases, queues, outside systems and labelled arrows).
# Changed by: Claude Code
# Date: 2026-11-26
def to_sketch(chart: dict[str, Any]) -> str:
    sketch_kind = {"service": "service", "step": "service", "database": "database", "queue": "queue",
                   "external": "external"}
    lines = [f'{sketch_kind[kind_of(n)]} "{n["label"]}"' for n in chart["nodes"].values()]
    for e in chart["edges"]:
        a, b = chart["nodes"][e["from"]]["label"], chart["nodes"][e["to"]]["label"]
        lines.append(f'"{a}" -> "{b}"' + (f' "{e["label"]}"' if e["label"] else ""))
    return "\n".join(lines)
