"""Which agents can reach each whiteboard block, as the board reply reports it."""

from __future__ import annotations

import pytest

from whiteboard_helpers import ok, project


# Input: tmp_path
# Output: None
# Processing: Check that path grants, read-only grants and carve-outs show up per block.
# Changed by: Claude Code
# Date: 2026-11-22
def test_access_map_follows_path_grants_and_carve_outs(tmp_path):
    ws = project(tmp_path, {"src/a.py": "A = 1\n", "src/secret/k.py": "K = 1\n", "docs/util.py": "U = 1\n"})
    ok(ws, "/project import")
    ok(ws, "/agent add bot")
    ok(ws, "/agent add viewer")
    ok(ws, "/agent grant bot src --mode read-write", confirm=True)
    ok(ws, "/agent revoke bot src/secret", confirm=True)
    ok(ws, "/agent grant viewer . --mode read", confirm=True)
    reach = ws.access_map()
    assert reach["a-py"] == {"bot": "read-write", "viewer": "read"}
    assert reach["k-py"] == {"viewer": "read"}
    assert reach["util-py"] == {"viewer": "read"}


# Input: tmp_path
# Output: None
# Processing: Check that region grants count only for blocks inside the rectangle.
# Changed by: Claude Code
# Date: 2026-11-22
def test_access_map_follows_region_grants(tmp_path):
    ws = project(tmp_path)
    ok(ws, '/block add service "inside" --at 100,100')
    ok(ws, '/block add service "outside" --at 900,900')
    ok(ws, "/agent add designer")
    ok(ws, "/agent region designer --area 0,0,500,500 --mode read-write --layer 1", confirm=True)
    reach = ws.access_map()
    assert reach["inside"] == {"designer": "read-write"}
    assert "outside" not in reach


# Input: tmp_path
# Output: None
# Processing: Check that the map matches what the engine enforces for every block.
# Changed by: Claude Code
# Date: 2026-11-22
def test_access_map_matches_agent_show(tmp_path):
    ws = project(tmp_path, {"src/a.py": "A = 1\n", "src/b.py": "B = 1\n", "lib/c.py": "C = 1\n"})
    ok(ws, "/project import")
    ok(ws, "/agent add bot")
    ok(ws, "/agent grant bot src --mode read-write", confirm=True)
    ok(ws, "/agent grant bot lib --mode read", confirm=True)
    shown = ok(ws, "/agent show bot")
    reach = ws.access_map()
    assert sorted(b for b, agents in reach.items() if agents.get("bot") == "read-write") == shown["can_change"]
    assert sorted(b for b, agents in reach.items() if agents.get("bot") == "read") == shown["can_read"]


# Input: tmp_path
# Output: None
# Processing: Check that the board reply carries the map and that a grant is a live change event.
# Changed by: Claude Code
# Date: 2026-11-22
def test_board_reply_includes_access_and_grants_are_announced(tmp_path):
    pytest.importorskip("flask")
    from backend.whiteboard.server import create_app

    ws = project(tmp_path, {"src/a.py": "A = 1\n"})
    ok(ws, "/project import")
    ok(ws, "/agent add bot")
    events = []
    ws.listeners.append(events.append)
    client = create_app(ws).test_client()
    assert client.get("/api/v1/whiteboard/board").get_json()["data"]["access"] == {}
    reply = client.post("/api/v1/whiteboard/command",
                        json={"command": "/agent grant bot src --mode read", "confirm": True})
    assert reply.get_json()["ok"]
    assert events[-1]["changes"]["agents"]["changed"] == ["bot"]
    access = client.get("/api/v1/whiteboard/board").get_json()["data"]["access"]
    assert access["a-py"] == {"bot": "read"}
    ok(ws, "/undo")
    assert client.get("/api/v1/whiteboard/board").get_json()["data"]["access"] == {}
