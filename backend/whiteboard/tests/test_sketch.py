"""/sketch: the short lines an AI draws with. No ghost blocks, no code, clean layout."""

from __future__ import annotations

import os

from whiteboard_helpers import fails, ok, project

DELIVERY = """/sketch service mobile-app, api-gateway, orders, payments
database postgres
queue order-events
external stripe
mobile-app -> api-gateway -> orders
orders -> payments "POST /charge"
payments -> stripe
orders, payments -> postgres
orders -> order-events"""


# Input: tmp_path
# Output: None
# Processing: A sketch draws blocks and typed arrows on layer 1 and writes no files.
# Changed by: Claude
# Date: 2026-11-10
def test_sketch_draws_without_writing_code(tmp_path):
    ws = project(tmp_path)
    result = ok(ws, DELIVERY)
    blocks = ws.board.blocks
    assert {"mobile-app", "api-gateway", "orders", "payments", "postgres", "order-events", "stripe"} == set(blocks)
    assert {b.layer for b in blocks.values()} == {1}
    types = {(a.source, a.target): a.type for a in ws.board.arrows.values()}
    assert types[("orders", "postgres")] == "database"
    assert types[("orders", "order-events")] == "event"
    assert types[("payments", "stripe")] == "external"
    assert types[("api-gateway", "orders")] == "calls"
    assert "orders -> payments (POST /charge)" in result["arrows"]
    assert [p.name for p in tmp_path.iterdir()] == [".plovane"]  # no handler files, no folders


# Input: tmp_path
# Output: None
# Processing: Repeating a sketch changes nothing, so no orders-2 duplicates appear.
# Changed by: Claude
# Date: 2026-11-10
def test_repeating_a_sketch_adds_no_duplicates(tmp_path):
    ws = project(tmp_path)
    ok(ws, DELIVERY)
    before = (set(ws.board.blocks), set(ws.board.arrows))
    assert ok(ws, DELIVERY)["changes"] == ["nothing to change"]
    assert ok(ws, "/sketch service Orders")["changes"] == ["nothing to change"]  # names match case-blind
    assert (set(ws.board.blocks), set(ws.board.arrows)) == before


# Input: tmp_path
# Output: None
# Processing: An unknown name is refused with the closest match and nothing in the sketch applies.
# Changed by: Claude
# Date: 2026-11-10
def test_unknown_names_are_refused_not_invented(tmp_path):
    ws = project(tmp_path)
    ok(ws, DELIVERY)
    reply = ws.command("/sketch service billing; orders -> paymnts")
    assert not reply["ok"] and "did you mean payments" in reply["error"]["message"]
    assert "billing" not in ws.board.blocks and "paymnts" not in ws.board.blocks
    assert fails(ws, "/sketch frobnicate x") == "UNKNOWN_COMMAND"
    assert fails(ws, "/sketch database orders") == "CONFLICT"  # orders is a service


# Input: tmp_path
# Output: None
# Processing: The layout puts each arrow's target in a later column and never overlaps blocks.
# Changed by: Claude
# Date: 2026-11-10
def test_layout_is_left_to_right_without_overlap(tmp_path):
    ws = project(tmp_path)
    ok(ws, DELIVERY)
    blocks = ws.board.blocks
    for arrow in ws.board.arrows.values():
        assert blocks[arrow.source].x < blocks[arrow.target].x
    boxes = [(b.x, b.y, b.x + b.w, b.y + b.h) for b in blocks.values()]
    for i, a in enumerate(boxes):
        for b in boxes[i + 1:]:
            assert a[2] <= b[0] or b[2] <= a[0] or a[3] <= b[1] or b[3] <= a[1], (a, b)


# Input: tmp_path
# Output: None
# Processing: rename, unlink and remove work, and one undo reverts a whole sketch.
# Changed by: Claude
# Date: 2026-11-10
def test_rename_unlink_remove_and_undo(tmp_path):
    ws = project(tmp_path)
    ok(ws, DELIVERY)
    ok(ws, '/sketch rename orders "order service"; unlink payments -> stripe; remove order-events')
    assert ws.board.blocks["orders"].name == "order service"
    assert "order-events" not in ws.board.blocks
    assert not any(a.target in ("stripe", "order-events") for a in ws.board.arrows.values())
    ok(ws, "/sketch order service -> stripe")  # the new name works too
    ok(ws, "/undo")
    ok(ws, "/undo")
    assert "order-events" in ws.board.blocks and ws.board.blocks["orders"].name == "orders"


# Input: tmp_path, monkeypatch
# Output: None
# Processing: PLOVANE_MCP_TOOLS=sketch gives an agent only plovane_sketch and plovane_undo.
# Changed by: Claude
# Date: 2026-11-10
def test_mcp_sketch_mode(tmp_path, monkeypatch):
    from backend.whiteboard import mcp_tools

    # A stand-in for the MCP server that records registered tools.
    class Server:
        # Input: none
        # Output: None
        # Processing: Start with no tools.
        # Changed by: Claude
        # Date: 2026-11-10
        def __init__(self) -> None:
            self.tools = {}

        # Input: name (str | None), description (str | None)
        # Output: Callable
        # Processing: Record the decorated function under its tool name.
        # Changed by: Claude
        # Date: 2026-11-10
        def tool(self, name=None, description=None):
            return lambda f: self.tools.setdefault(name or f.__name__, f)

    monkeypatch.setenv("PLOVANE_MCP_TOOLS", "sketch")
    monkeypatch.delenv("PLOVANE_AGENT", raising=False)
    server = Server()
    assert mcp_tools.register(server) == ["plovane_sketch", "plovane_undo"]
    assert set(server.tools) == {"plovane_sketch", "plovane_undo"}
    ws = project(tmp_path)
    ws.command("/agent add claude")  # the MCP server acts as the agent "claude", never the person
    ws.command("/agent grant claude . --mode read-write", confirm=True)
    mcp_tools.set_workspace(ws)
    try:
        assert server.tools["plovane_sketch"]("service a; service b; a -> b")["ok"]
        assert server.tools["plovane_undo"]()["ok"]
        assert "a" not in mcp_tools.workspace().board.blocks
    finally:
        mcp_tools.set_workspace(None)
    assert os.environ["PLOVANE_MCP_TOOLS"] == "sketch"
