"""Agents working through their token show as online, not "Not connected"."""

from __future__ import annotations

import threading
from pathlib import Path

import pytest

from backend.whiteboard import presence as presence_module
from backend.whiteboard.presence import Presence, describe
from whiteboard_helpers import project

PERSON = "person-token-for-test"


# Input: none
# Output: None
# Processing: Online while a request is in flight and for ACTIVE_SECONDS after; the first sighting is reported once.
# Changed by: Claude Code
# Date: 2026-11-27
def test_online_while_busy_and_for_a_while_after() -> None:
    clock = [1000.0]
    p = Presence(clock=lambda: clock[0])
    assert p.snapshot() == {}
    assert p.begin("helper", "asking: draw billing") is True
    assert p.begin("helper") is False  # a second request in parallel is not a new arrival
    clock[0] += 600
    assert p.snapshot()["helper"]["online"] is True  # a long ask keeps it online
    p.end("helper")
    p.end("helper")
    clock[0] += presence_module.ACTIVE_SECONDS - 1
    assert p.snapshot()["helper"] == {"online": True, "busy": False, "ago": presence_module.ACTIVE_SECONDS - 1,
                                      "doing": "asking: draw billing"}
    clock[0] += 2
    assert p.snapshot()["helper"]["online"] is False
    assert p.seen("helper") is True  # back after a gap: reported again


# Input: none
# Output: None
# Processing: The tooltip phrase names the ask, command or op.
# Changed by: Claude Code
# Date: 2026-11-27
def test_describe() -> None:
    base = "/api/v1/whiteboard/"
    assert describe("POST", base + "ask", {"message": "draw it"}) == "asking: draw it"
    assert describe("POST", base + "command", {"command": "/task list"}) == "/task list"
    assert describe("POST", base + "op", {"op": "block.add"}) == "block.add"
    assert describe("GET", base + "board", None) == "reading the board"


# Input: tmp_path (Path)
# Output: None
# Processing: Over HTTP: an agent's request makes it online in the presence endpoint and the board reply, one "online" event is sent, and the person never shows up.
# Changed by: Claude Code
# Date: 2026-11-27
def test_server_reports_agents_working_through_tokens(tmp_path: Path) -> None:
    pytest.importorskip("flask")
    from backend.whiteboard.server import create_app

    ws = project(tmp_path, {"src/app.py": "def run():\n    return 1\n"})
    app = create_app(ws, person_token=PERSON, lock=threading.RLock())
    client = app.test_client()
    person = {"Authorization": f"Bearer {PERSON}"}
    token = client.post("/api/v1/whiteboard/agents/helper/token", json={"access": "read", "path": "."},
                        headers=person).get_json()["data"]["token"]
    bus = app.config["plovane_bus"]
    start = bus.seq

    empty = client.get("/api/v1/whiteboard/agents/presence", headers=person).get_json()["data"]
    assert empty["presence"] == {}

    agent = {"Authorization": f"Bearer {token}"}
    assert client.get("/api/v1/whiteboard/board", headers=agent).status_code == 200
    assert client.post("/api/v1/whiteboard/command", json={"command": "/task list"}, headers=agent).status_code == 200

    live = client.get("/api/v1/whiteboard/agents/presence", headers=person).get_json()["data"]["presence"]
    assert set(live) == {"helper"}
    assert live["helper"]["online"] is True and live["helper"]["busy"] is False
    assert live["helper"]["doing"] == "/task list"
    board = client.get("/api/v1/whiteboard/board", headers=person).get_json()["data"]
    assert board["presence"]["helper"]["online"] is True
    online = [e for e in bus.since(start) if e.get("type") == "online"]
    assert [e["agent"] for e in online] == ["helper"]
