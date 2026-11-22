"""Overlays: which are on for whom, what each premade analyzer finds, and how results roll up."""

from __future__ import annotations

import pytest

from backend.whiteboard import overlays
from backend.whiteboard.model import Board
from whiteboard_helpers import ok, project

CHARGE_PY = '''import os

API_KEY = "sk-abcdefghijklmnopqrstuvwxyz123456"


def charge(amount):
    return amount


def cleanup():
    os.system("rm -rf /tmp/cache")
'''
PLACE_PY = "from payments.charge import charge\n\n\ndef place(order):\n    return charge(order)\n"
TEST_PLACE_PY = "from orders import place\n\n\ndef test_place():\n    assert place\n"


# Input: tmp_path
# Output: Workspace
# Processing: A project with a leaked key, a shell call, an endpoint and one tested and one untested file.
# Changed by: Claude Code
# Date: 2026-11-22
def shop(tmp_path):
    ws = project(tmp_path, {"src/payments/charge.py": CHARGE_PY, "src/orders/place.py": PLACE_PY,
                            "tests/test_place.py": TEST_PLACE_PY})
    ok(ws, "/project import")
    ok(ws, '/block add service "payments" --at 120,80')
    ok(ws, "/endpoint add payments POST /charge")
    return ws


# Input: ws (Workspace), actor (str)
# Output: dict
# Processing: The overlay state the board reply would carry.
# Changed by: Claude Code
# Date: 2026-11-22
def state(ws, actor: str = "user") -> dict:
    return overlays.overlay_state(ws, ws.board, actor)


# Input: tmp_path
# Output: None
# Processing: Check the default role, and that switching role changes which overlays are on.
# Changed by: Claude Code
# Date: 2026-11-22
def test_roles_decide_which_overlays_are_on(tmp_path):
    ws = shop(tmp_path)
    assert state(ws)["active"] == ["api_endpoints", "test_health", "agent_access", "library_code"]
    ok(ws, "/role use cyber")
    assert state(ws)["active"] == ["code_vulnerabilities", "leaked_secrets", "unauthenticated_endpoints",
                                   "api_endpoints"]
    assert ok(ws, "/role list")["current"] == "cyber"


# Input: tmp_path
# Output: None
# Processing: Check leaked secrets: found on the file, rolled up to folders, and the value is never included.
# Changed by: Claude Code
# Date: 2026-11-22
def test_leaked_secrets_roll_up_without_the_value(tmp_path):
    ws = shop(tmp_path)
    ok(ws, "/role use cyber")
    secrets = state(ws)["results"]["leaked_secrets"]
    charge = ws.board.block_for_path("src/payments/charge.py").id
    assert secrets[charge]["own"] and secrets[charge]["items"][0]["line"] == 3
    assert secrets["src-payments"] == {"count": 1, "items": [], "own": False}
    assert secrets["src"]["count"] == 1
    assert "sk-" not in str(secrets)


# Input: tmp_path
# Output: None
# Processing: Check risky code is found and a finding inside a function also marks that function's block.
# Changed by: Claude Code
# Date: 2026-11-22
def test_code_vulnerabilities_reach_the_function_holding_the_line(tmp_path):
    ws = shop(tmp_path)
    ok(ws, "/role use cyber")
    charge = ws.board.block_for_path("src/payments/charge.py")
    ok(ws, f"/blocks view {charge.path} --detail 1")
    vulns = state(ws)["results"]["code_vulnerabilities"]
    assert "os.system runs a shell command" in vulns[charge.id]["items"][0]["message"]
    inner = overlays.block_at_line(ws.board, charge.path, 11)
    assert inner is not None and vulns[inner]["own"]
    assert vulns["src-payments"]["count"] == 1, "a finding on a file and its function counts once on the folder"


# Input: tmp_path
# Output: None
# Processing: Check an endpoint with no login check is flagged, and adding one clears it.
# Changed by: Claude Code
# Date: 2026-11-22
def test_endpoints_with_no_login_check(tmp_path):
    ws = shop(tmp_path)
    ok(ws, "/role use cyber")
    flagged = state(ws)["results"]["unauthenticated_endpoints"]
    assert "POST /charge has no login check" in flagged["payments"]["items"][0]["message"]
    handler = ws.board.endpoints["payments:POST /charge"].handler
    path = tmp_path / handler
    path.write_text("from auth import login_required\n" + path.read_text())
    assert "unauthenticated_endpoints" not in state(ws)["results"] or \
        "payments" not in state(ws)["results"]["unauthenticated_endpoints"]


# Input: tmp_path
# Output: None
# Processing: Check the API endpoints overlay lists each service's endpoints.
# Changed by: Claude Code
# Date: 2026-11-22
def test_api_endpoints_list_each_service(tmp_path):
    ws = shop(tmp_path)
    endpoints = state(ws)["results"]["api_endpoints"]
    assert [i["message"] for i in endpoints["payments"]["items"]] == ["POST /charge"]


# Input: tmp_path
# Output: None
# Processing: Check test health: tested and untested files, and folders showing the worst state inside them.
# Changed by: Claude Code
# Date: 2026-11-22
def test_test_health_marks_files_and_folders(tmp_path):
    ws = shop(tmp_path)
    health = state(ws)["results"]["test_health"]
    place = ws.board.block_for_path("src/orders/place.py").id
    charge = ws.board.block_for_path("src/payments/charge.py").id
    assert health[place]["status"] == "tested"
    assert health[charge]["status"] == "untested"
    assert health["src-payments"]["status"] == "untested" and health["src-payments"]["count"] == 1
    assert health["src-orders"].get("status") == "tested"
    ws.board.blocks[charge].meta["health"] = "tests failing in tests/test_charge.py"
    assert state(ws)["results"]["test_health"]["src"]["status"] == "failing"


# Input: tmp_path
# Output: None
# Processing: Check switching overlays and groups on and off, the mixed state, and undo.
# Changed by: Claude Code
# Date: 2026-11-22
def test_switching_overlays_groups_and_undo(tmp_path):
    ws = shop(tmp_path)
    ok(ws, "/overlay off test_health")
    assert "test_health" not in state(ws)["active"]
    assert ok(ws, "/overlay on cyber_security")["state"] == "on"
    assert {"code_vulnerabilities", "leaked_secrets", "unauthenticated_endpoints"} <= set(state(ws)["active"])
    assert ok(ws, "/overlay off leaked_secrets")["state"] == "off"
    rows = {r["overlay"]: r["state"] for r in ok(ws, "/overlay list")["overlays"]}
    assert rows["cyber_security"] == "mixed" and rows["leaked_secrets"] == "off"
    ok(ws, "/undo")
    rows = {r["overlay"]: r["state"] for r in ok(ws, "/overlay list")["overlays"]}
    assert rows["cyber_security"] == "on"
    assert ws.command("/overlay on nothing")["error"]["code"] == "NOT_FOUND"


# Input: tmp_path
# Output: None
# Processing: Check each actor has its own view, so an agent switching overlays leaves the person's view alone.
# Changed by: Claude Code
# Date: 2026-11-22
def test_each_actor_has_its_own_view(tmp_path):
    ws = shop(tmp_path)
    ok(ws, "/agent add bot")
    ok(ws, "/agent grant bot . --mode read", confirm=True)
    ok(ws, "/role use cyber", actor="bot")
    assert state(ws, "bot")["view"]["base_role"] == "cyber"
    assert state(ws)["view"]["base_role"] == "developer"


# Input: tmp_path, monkeypatch
# Output: None
# Processing: Check file scans are cached until the file changes.
# Changed by: Claude Code
# Date: 2026-11-22
def test_scans_are_cached_until_a_file_changes(tmp_path, monkeypatch):
    from backend.whiteboard.ops import health

    ws = shop(tmp_path)
    ok(ws, "/role use cyber")
    calls = []
    real = health.scan_text
    monkeypatch.setattr(health, "scan_text", lambda path, text: calls.append(path) or real(path, text))
    ws.overlay_cache = overlays.ScanCache()
    state(ws)
    first = len(calls)
    state(ws)
    assert len(calls) == first, "an unchanged project is not scanned again"
    (tmp_path / "src/payments/charge.py").write_text(CHARGE_PY + "\n# edited\n")
    state(ws)
    assert calls[first:] == ["src/payments/charge.py"]


# Input: tmp_path
# Output: None
# Processing: Check a custom overlay is reported as not yet runnable, and old boards get the default overlays.
# Changed by: Claude Code
# Date: 2026-11-22
def test_custom_overlays_wait_and_old_boards_get_defaults(tmp_path):
    ws = shop(tmp_path)
    ws.board.overlays["db_writes"] = {"label": "DB writes", "source": "custom", "created_by": "ai",
                                      "author": "usr", "match": {"node_type": "endpoint"},
                                      "channel": "badge", "appearance": {"3": "mark"}}
    ok(ws, "/overlay on db_writes")
    assert "rule engine" in state(ws)["unavailable"]["db_writes"]
    old = Board.from_dict({"blocks": {}})
    assert "cyber_security" in old.overlays and "developer" in old.roles and old.views == {}


# Input: tmp_path
# Output: None
# Processing: Check the board reply carries the overlay state for whoever is asking.
# Changed by: Claude Code
# Date: 2026-11-22
def test_board_reply_carries_overlays(tmp_path):
    pytest.importorskip("flask")
    from backend.whiteboard.server import create_app

    ws = shop(tmp_path)
    client = create_app(ws).test_client()
    client.post("/api/v1/whiteboard/command", json={"command": "/role use cyber"})
    data = client.get("/api/v1/whiteboard/board").get_json()["data"]["overlays"]
    assert data["view"]["base_role"] == "cyber" and "leaked_secrets" in data["results"]
    assert set(data["defs"]) >= {"cyber_security", "test_health"} and "cyber" in data["roles"]


# Input: tmp_path
# Output: None
# Processing: Check overlays and roles travel in the .plo file (only when changed), and views stay personal.
# Changed by: Claude Code
# Date: 2026-11-22
def test_overlays_and_roles_travel_in_the_plo_file(tmp_path):
    import json

    ws = shop(tmp_path)
    ok(ws, "/board save")
    plain = json.loads((tmp_path / "whiteboard.plo").read_text())["commands"]
    assert not any(c.startswith(("/overlay", "/role")) for c in plain), "defaults are not written"
    custom = {"label": "DB writes", "source": "custom", "created_by": "ai", "author": "usr",
              "parent": "cyber_security", "match": {"node_type": "endpoint"}, "channel": "badge",
              "appearance": {"3": "mark"}}
    ok(ws, f"/overlay put db_writes --data '{json.dumps(custom)}'")
    ok(ws, '/role put qa --data \'{"label": "QA", "overlays": ["test_health", "db_writes"], "default_level": 2}\'')
    ok(ws, "/overlay remove library_code")
    ok(ws, "/role use qa")
    ok(ws, "/board save")
    lines = json.loads((tmp_path / "whiteboard.plo").read_text())["commands"]
    assert "/overlay remove library_code" in lines
    assert any(c.startswith("/overlay put db_writes") for c in lines)
    assert any(c.startswith("/role put qa") for c in lines)
    assert not any("/view" in c or "base_role" in c for c in lines), "personal views stay out of the shared file"
    ok(ws, "/board save keep")
    ok(ws, "/overlay remove db_writes")
    assert not any("db_writes" in c for c in json.loads((tmp_path / "whiteboard.plo").read_text())["commands"])
    ok(ws, "/board load keep.plo", confirm=True)
    assert ws.board.overlays["db_writes"] == custom and "library_code" not in ws.board.overlays
    assert ws.board.roles["qa"]["overlays"] == ["test_health", "db_writes"]
    assert overlays.view_of(ws.board, "user")["base_role"] == "qa"


# Input: tmp_path
# Output: None
# Processing: Check bad overlay and role definitions are refused, and agents cannot define them.
# Changed by: Claude Code
# Date: 2026-11-22
def test_bad_definitions_are_refused(tmp_path):
    ws = shop(tmp_path)
    bad = {"label": "x", "source": "premade", "channel": "glow", "appearance": {}}
    import json

    assert ws.command(f"/overlay put x --data '{json.dumps(bad)}'")["error"]["code"] == "INVALID_ARGS"
    orphan = {"label": "x", "source": "group", "parent": "nothing"}
    assert ws.command(f"/overlay put x --data '{json.dumps(orphan)}'")["error"]["code"] == "INVALID_ARGS"
    assert ws.command('/role put r --data \'{"label": "R", "overlays": ["ghost"]}\'')["error"]["code"] == "INVALID_ARGS"
    ok(ws, "/agent add bot")
    reply = ws.command('/overlay put y --data \'{"label": "y", "source": "group"}\'', actor="bot")
    assert reply["error"]["code"] == "FORBIDDEN"
