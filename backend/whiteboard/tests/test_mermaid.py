"""Tests for reading Mermaid flowcharts and drawing them as whiteboards (/flow list, /flow import)."""

from __future__ import annotations

from pathlib import Path

import pytest

from backend.whiteboard import board_ai
from backend.whiteboard.engine import BOARD_AI, Workspace
from backend.whiteboard.mermaid import diagrams, is_process, kind_of, parse, to_board, to_sketch
from whiteboard_helpers import project

ARCH = '''flowchart LR
    U((User)) -->|HTTPS| W[Web app<br/>Next.js]
    W --> A["API (FastAPI)"]
    A -- reads/writes --> DB[(Postgres)]
    A -.->|async| Q[[Celery queue]]
    Q --> Wk(Worker) & N[Notifier]
    A --> S{{Stripe}}
    subgraph backend [Backend services]
      A
      Wk
    end
    classDef hot fill:#f00;
    %% a comment
'''
PROCESS = '''graph TD
  start([Start]) --> check{Card valid?}
  check -->|yes| charge[Charge card] --> done([Done])
  check -->|no| err[Show error]
'''
README = f"# Shop\n\nHow it fits together:\n\n```mermaid\n{ARCH}```\n\nCheckout:\n\n```mermaid\n{PROCESS}```\n\n```mermaid\nsequenceDiagram\n  A->>B: hi\n```\n"


# Input: none
# Output: None
# Processing: Nodes, shapes, labels, edge labels and styles, & lists, chains and subgraphs are read; styling lines and comments are skipped.
# Changed by: Claude Code
# Date: 2026-11-26
def test_parse_architecture() -> None:
    chart = parse(ARCH)
    assert chart["direction"] == "LR"
    assert chart["nodes"]["W"]["label"] == "Web app Next.js" and chart["nodes"]["DB"]["shape"] == "database"
    assert chart["nodes"]["A"]["group"] == "backend" and chart["groups"] == {"backend": "Backend services"}
    edges = {(e["from"], e["to"]): e for e in chart["edges"]}
    assert edges[("U", "W")]["label"] == "HTTPS" and edges[("A", "DB")]["label"] == "reads/writes"
    assert edges[("A", "Q")]["style"] == "dotted" and ("Q", "N") in edges and ("Q", "Wk") in edges
    kinds = {nid: kind_of(n) for nid, n in chart["nodes"].items()}
    assert kinds == {"U": "service", "W": "service", "A": "service", "DB": "database", "Q": "queue",
                     "Wk": "service", "N": "service", "S": "external"}
    assert not is_process(chart)


# Input: none
# Output: None
# Processing: A flowchart with decisions reads as a process; chained edges keep their labels.
# Changed by: Claude Code
# Date: 2026-11-26
def test_parse_process_and_find_diagrams() -> None:
    assert len(diagrams(README)) == 2  # the sequence diagram is not a flowchart
    chart = parse(PROCESS)
    assert is_process(chart) and chart["nodes"]["check"]["shape"] == "decision"
    assert [(e["from"], e["to"], e["label"]) for e in chart["edges"]] == [
        ("start", "check", ""), ("check", "charge", "yes"), ("charge", "done", ""), ("check", "err", "no")]
    board = to_board(chart)
    assert {b.kind for b in board.blocks.values()} == {"step"}
    assert board.blocks["card-valid"].meta["decision"] is True
    with pytest.raises(ValueError):
        parse("sequenceDiagram\n A->>B: hi")


# Input: tmp_path (Path)
# Output: None
# Processing: The sketch form of a diagram draws on a board exactly: every node, every arrow, every label.
# Changed by: Claude Code
# Date: 2026-11-26
def test_sketch_form_draws_the_diagram(tmp_path: Path) -> None:
    ws = project(tmp_path, {"a.py": ""})
    reply = ws.command("/sketch " + to_sketch(parse(ARCH)).replace("\n", "; "))
    assert reply["ok"], reply
    arrows = {(a.source, a.target): a.style.get("label") for a in ws.board.arrows.values()}
    assert arrows[("user", "web-app-next-js")] == "HTTPS" and ("web-app-next-js", "api-fastapi") in arrows
    assert ws.board.blocks["postgres"].kind == "database" and ws.board.blocks["stripe"].kind == "external"


# Input: tmp_path (Path)
# Output: None
# Processing: /flow list numbers a README's flowcharts; /flow import draws one as its own whiteboard tab, which the whiteboard AI may do too.
# Changed by: Claude Code
# Date: 2026-11-26
def test_flow_list_and_import(tmp_path: Path) -> None:
    ws = project(tmp_path, {"README.md": README, "app.py": ""})
    listed = ws.command("/flow list README.md")["data"]["result"]["diagrams"]
    assert [(d["diagram"], d["kind"]) for d in listed] == [(1, "parts"), (2, "process")]
    made = ws.command("/flow import README.md --diagram 2")["data"]["result"]
    assert made["kind"] == "process" and made["board"].startswith("whiteboards/flows/")
    tab = Workspace(tmp_path, board=made["board"])
    assert {b.name for b in tab.board.blocks.values()} >= {"Card valid?", "Charge card", "Show error"}
    assert ws.command("/flow import README.md --diagram 9")["error"]["code"] == "INVALID_ARGS"
    assert ws.command("/flow import missing.md")["error"]["code"] == "NOT_FOUND"
    board_ai.ensure_board_ai(ws)
    by_ai = ws.command("/flow import README.md --diagram 1", actor=BOARD_AI)
    assert by_ai["ok"], by_ai  # writing a whiteboard file is board work, not code
    assert ws.command("/file create notes.py", actor=BOARD_AI)["error"]["code"] == "FORBIDDEN"
