"""Two processes on one project: the whiteboard server and the MCP server each
hold a Workspace, and each must see what the other saved to board.json."""

from __future__ import annotations

import pytest

from backend.whiteboard.engine import Workspace
from whiteboard_helpers import ok, project


# Input: tmp_path
# Output: None
# Processing: A change saved by one workspace shows up in the other before its next operation.
# Changed by: Claude
# Date: 2026-11-10
def test_a_second_workspace_sees_saved_changes(tmp_path):
    server = project(tmp_path)
    mcp = Workspace(tmp_path)  # a second process opening the same project
    ok(mcp, '/block add service "payments"')
    assert "payments" not in server.board.blocks  # not re-read yet
    ok(server, '/block add database "postgres"')  # its next operation reloads first...
    assert {"payments", "postgres"} <= set(server.board.blocks)  # ...so neither change is lost
    assert mcp.sync() and {"payments", "postgres"} <= set(mcp.board.blocks)
    assert not mcp.sync()  # nothing new since


# Input: tmp_path
# Output: None
# Processing: The HTTP board read reloads, so the page shows MCP changes without a restart.
# Changed by: Claude
# Date: 2026-11-10
def test_the_board_endpoint_shows_changes_from_another_process(tmp_path):
    pytest.importorskip("flask")
    from backend.whiteboard.server import create_app

    http = create_app(project(tmp_path)).test_client()
    ok(Workspace(tmp_path), '/block add service "from-mcp"')
    blocks = http.get("/api/v1/whiteboard/board").get_json()["data"]["board"]["blocks"]
    assert "from-mcp" in blocks


# Input: none
# Output: None
# Processing: The planner accepts a JSON reply wrapped in a Markdown fence.
# Changed by: Claude
# Date: 2026-11-10
def test_strip_fence():
    pytest.importorskip("flask")
    from backend.whiteboard.server import strip_fence

    assert strip_fence('```json\n{"a": 1}\n```') == '{"a": 1}'
    assert strip_fence('{"a": 1}') == '{"a": 1}'
    assert strip_fence('  ```\n{"a": 1}```  ') == '{"a": 1}'


# Input: tmp_path
# Output: None
# Processing: The planner sees the real command list and gets one retry after inventing a command.
# Changed by: Claude
# Date: 2026-11-10
def test_planner_retries_after_an_invented_command(tmp_path):
    pytest.importorskip("flask")
    import json

    from backend.whiteboard.server import create_app
    from whiteboard_helpers import ScriptedLLM

    llm = ScriptedLLM(json.dumps({"summary": "x", "commands": ["/tree"]}),
                      json.dumps({"summary": "Add payments", "commands": ['/block add service "payments"']}))
    ws = project(tmp_path, llm=llm)
    real = ws.execute
    ws.execute = lambda op, args=None, **kw: (  # the library search needs the ML models; skip it here
        {"ok": True, "data": {"result": {"recommendations": []}}, "error": None}
        if op == "library.rank" else real(op, args, **kw))
    reply = create_app(ws).test_client().post("/api/v1/whiteboard/prompt/plan", json={"prompt": "add payments"})
    data = reply.get_json()
    assert reply.status_code == 200, data
    assert data["data"]["commands"] == ['/block add service "payments"']
    first, second = (json.loads(p) for p in llm.prompts)
    assert any(c["command"] == "/block add" for c in first["command_reference"])
    assert "/tree" in second["previous_plan_was_rejected"]
    assert "payments" not in ws.board.blocks  # a plan is only a preview


# Input: none
# Output: None
# Processing: check_plan names every invalid command instead of stopping at the first.
# Changed by: Claude
# Date: 2026-11-10
def test_check_plan_reports_all_bad_commands():
    pytest.importorskip("flask")
    from backend.whiteboard.server import check_plan

    summary, commands, problem = check_plan('{"summary": "s", "commands": ["/tree", "/frobnicate x"]}', [])
    assert commands == [] and "/tree" in problem and "/frobnicate" in problem
    assert check_plan("not json", [])[2] == "the reply was not JSON"
    assert check_plan('{"summary": "s", "commands": ["/undo"]}', [])[1:] == (["/undo"], "")
