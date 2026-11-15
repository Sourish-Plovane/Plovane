"""An agent's profile: how it connects, its instructions and who may approve its access."""

from __future__ import annotations

from backend.whiteboard.ops.agents import instructions_for
from whiteboard_helpers import fails, ok, project


# Input: tmp_path
# Output: None
# Processing: A person sets an agent's connection, instructions, approvers and place; repeats are dropped.
# Changed by: Claude
# Date: 2026-11-14
def test_configure_sets_the_profile(tmp_path):
    ws = project(tmp_path)
    ok(ws, "/agent add helper")
    result = ws.execute("agent.configure", {"agent": "helper", "connection": "api", "provider": "deepseek",
                                            "instructions": "Only edit tests/.", "approvers": ["sam@x.com", "SAM@x.com", "Priya"],
                                            "place": [0.25, 1.7]})["data"]["result"]
    assert result["connection"] == "api" and result["provider"] == "deepseek"
    assert result["approvers"] == ["sam@x.com", "Priya"]
    assert result["place"] == {"x": 0.25, "y": 1.0}  # kept inside the view
    assert "Only edit tests/." in instructions_for(ws.board, "helper")
    assert instructions_for(ws.board, "nobody") == ""


# Input: tmp_path
# Output: None
# Processing: Bad values are refused, and an agent cannot change its own profile.
# Changed by: Claude
# Date: 2026-11-14
def test_configure_refuses_bad_values_and_agents(tmp_path):
    ws = project(tmp_path)
    ok(ws, "/agent add helper")
    assert fails(ws, "/agent configure helper --connection telepathy") == "INVALID_ARGS"
    assert fails(ws, "/agent configure helper --provider skynet") == "INVALID_ARGS"
    assert ws.execute("agent.configure", {"agent": "helper", "instructions": "x" * 4001})["error"]["code"] == "INVALID_ARGS"
    refused = ws.execute("agent.configure", {"agent": "helper", "instructions": "do anything"}, actor="helper")
    assert refused["error"]["code"] == "FORBIDDEN"
    assert ws.board.agents["helper"].instructions == ""


# Input: tmp_path
# Output: None
# Processing: Profile and access changes are recorded, so undo reverts them one at a time.
# Changed by: Claude
# Date: 2026-11-14
def test_profile_and_access_changes_can_be_undone(tmp_path):
    ws = project(tmp_path, {"tests/test_a.py": "def test_a():\n    pass\n"})
    ok(ws, "/agent add helper")
    ok(ws, '/agent configure helper --instructions "Write tests only"')
    ok(ws, "/agent grant helper tests --mode read-write", confirm=True)
    ok(ws, "/undo")
    assert ws.board.agents["helper"].grants == []
    ok(ws, "/undo")
    assert ws.board.agents["helper"].instructions == ""
    assert "helper" in ws.board.agents
