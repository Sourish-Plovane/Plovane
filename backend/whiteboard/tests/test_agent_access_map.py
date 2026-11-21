"""Which agents reach each block (#71): the map the whiteboard draws as chips."""

from __future__ import annotations

import pytest

from whiteboard_helpers import ok, project


# Input: tmp_path
# Output: None
# Processing: Check path grants, a "none" carve-out and a region grant all show up in the access map.
# Changed by: Claude Code
# Date: 2026-11-14
def test_access_map_follows_path_grants_carve_outs_and_regions(tmp_path):
    ws = project(tmp_path, {"src/app.py": "x = 1\n", "src/secret/keys.py": "k = 1\n", "docs/build.py": "y = 2\n"})
    ok(ws, "/project import")
    ok(ws, '/block add service "inside" --at 100,100')
    ok(ws, "/agent add claude")
    ok(ws, "/agent grant claude src --mode read-write", confirm=True)
    ok(ws, "/agent grant claude src/secret --mode none", confirm=True)
    ok(ws, "/agent add reviewer")
    ok(ws, "/agent grant reviewer . --mode read", confirm=True)
    ok(ws, "/agent add designer")
    ok(ws, "/agent region designer --area 0,0,500,500 --mode read-write --layer 1", confirm=True)
    access = ws.access_map()
    app = ws.board.block_for_path("src/app.py").id
    keys = ws.board.block_for_path("src/secret/keys.py").id
    docs = ws.board.block_for_path("docs/build.py").id
    assert access[app] == {"claude": "read-write", "reviewer": "read"}
    assert access[keys] == {"reviewer": "read"}  # the carve-out hides src/secret from claude
    assert access[docs] == {"reviewer": "read"}
    assert access["inside"]["designer"] == "read-write"
    ok(ws, "/agent revoke reviewer .", confirm=True)
    assert docs not in ws.access_map()


# Input: tmp_path
# Output: None
# Processing: Check the board the whiteboard loads carries the access map.
# Changed by: Claude Code
# Date: 2026-11-14
def test_board_reply_carries_access(tmp_path):
    pytest.importorskip("flask")
    from backend.whiteboard.server import create_app

    ws = project(tmp_path, {"src/app.py": "x = 1\n"})
    ok(ws, "/project import")
    ok(ws, "/agent add claude")
    ok(ws, "/agent grant claude . --mode read", confirm=True)
    data = create_app(ws).test_client().get("/api/v1/whiteboard/board").get_json()["data"]
    assert data["access"][ws.board.block_for_path("src/app.py").id] == {"claude": "read"}


# Input: tmp_path
# Output: None
# Processing: Check a grant or revoke announces a change (so open whiteboards redraw) and can be undone.
# Changed by: Claude Code
# Date: 2026-11-14
def test_grants_announce_a_change_and_undo(tmp_path):
    ws = project(tmp_path, {"src/app.py": "x = 1\n"})
    ok(ws, "/project import")
    ok(ws, "/agent add claude")
    events = []
    ws.listeners.append(events.append)
    ok(ws, "/agent grant claude src --mode read-write", confirm=True)
    assert events and events[-1]["changes"]["agents"]["changed"] == ["claude"]
    assert ws.access_map()
    ok(ws, "/undo")
    assert not ws.access_map()
