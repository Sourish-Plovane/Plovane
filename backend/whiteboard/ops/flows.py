"""Flowcharts as whiteboards: draw the diagrams a project already keeps in its docs.

    /flow list README.md                         the Mermaid flowcharts in a file, numbered
    /flow import README.md --diagram 2           draw one as its own whiteboard tab (whiteboards/flows/<name>.plo)

A map of parts (services, databases, outside APIs) is drawn with those kinds;
a process (decisions, start and end) is drawn as steps, with each decision
marked. Labels on the diagram's arrows are kept.
"""

from __future__ import annotations

from typing import Any

from backend.whiteboard import mermaid
from backend.whiteboard.engine import Context
from backend.whiteboard.model import slug
from backend.whiteboard.ops.boardfile import EXTENSION, render
from backend.whiteboard.registry import OpError, flag, integer, operation
from backend.whiteboard.store import MAIN_BOARD

FOLDER = "whiteboards/flows"


# Input: ctx (Context), path (str)
# Output: list[dict[str, Any]]
# Processing: The parsed Mermaid flowcharts in a project file, refusing a missing file or one with none.
# Changed by: Claude Code
# Date: 2026-11-26
def charts_in(ctx: Context, path: str) -> list[dict[str, Any]]:
    rel = str(path or "").strip().strip("/")
    if not rel or not ctx.fs.exists(rel) or ctx.fs.is_dir(rel):
        raise OpError("NOT_FOUND", f"no file {path!r} in the project")
    ctx.require(rel, "read")
    charts = []
    for source in mermaid.diagrams(ctx.fs.read(rel)):
        try:
            charts.append(mermaid.parse(source))
        except ValueError:
            continue
    if not charts:
        raise OpError("NOT_FOUND", f"{rel} has no Mermaid flowchart (```mermaid with flowchart or graph)")
    return charts


# Input: chart (dict[str, Any])
# Output: str
# Processing: A short name for a diagram: its first group's title, else its first two node labels.
# Changed by: Claude Code
# Date: 2026-11-26
def chart_name(chart: dict[str, Any]) -> str:
    if chart["groups"]:
        return next(iter(chart["groups"].values()))
    labels = [n["label"] for n in chart["nodes"].values()][:2]
    return " to ".join(labels) or "flow"


# Input: ctx (Context), path (str)
# Output: dict[str, Any]
# Processing: The Mermaid flowcharts in a file, numbered, with their size and whether each is a process or a map of parts.
# Changed by: Claude Code
# Date: 2026-11-26
@operation("flow.list", command="/flow list", tool="plovane_flow_list", group="plovane_view", mutates=False)
def flow_list(ctx: Context, path: str) -> dict[str, Any]:
    """Lists the Mermaid flowcharts in a file (a README or docs page), numbered for /flow import."""
    return {"file": path, "diagrams": [
        {"diagram": i + 1, "name": chart_name(c), "nodes": len(c["nodes"]), "arrows": len(c["edges"]),
         "kind": "process" if mermaid.is_process(c) else "parts"}
        for i, c in enumerate(charts_in(ctx, path))]}


# Input: ctx (Context), path (str), diagram (Any), to (str | None), refresh (Any)
# Output: dict[str, Any]
# Processing: Draw one of a file's Mermaid flowcharts as its own whiteboard tab.
# Changed by: Claude Code
# Date: 2026-11-26
@operation("flow.import", command="/flow import", tool="plovane_flow_import", group="plovane_view")
def flow_import(ctx: Context, path: str, diagram: Any = 1, to: str | None = None, refresh: Any = False) -> dict[str, Any]:
    """Draws a Mermaid flowchart from the project's docs as its own whiteboard tab."""
    charts = charts_in(ctx, path)
    number = integer(diagram, "diagram")
    if not 1 <= number <= len(charts):
        raise OpError("INVALID_ARGS", f"{path} has {len(charts)} flowchart(s); give --diagram 1 to {len(charts)}")
    chart = charts[number - 1]
    name = chart_name(chart)
    rel = (to or f"{FOLDER}/{slug(name)[:40]}{EXTENSION}").strip().strip("/")
    if not rel.endswith(EXTENSION) or rel == MAIN_BOARD or ".." in rel.split("/"):
        raise OpError("INVALID_ARGS", f"give a whiteboard file other than {MAIN_BOARD}, ending in {EXTENSION}")
    if ctx.fs.exists(rel) and not flag(refresh):
        return {"board": rel, "existing": True, "message": f"{rel} already exists; import again with --refresh"}
    board = mermaid.to_board(chart, title=f"{name} (from {path}, diagram {number})")
    ctx.fs.allow_plo = True
    try:
        ctx.fs.write(rel, render(board, agents=False))
    finally:
        ctx.fs.allow_plo = False
    return {"board": rel, "name": name, "kind": "process" if mermaid.is_process(chart) else "parts",
            "blocks": len(board.blocks), "arrows": len(board.arrows)}
