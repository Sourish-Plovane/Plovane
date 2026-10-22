"""The whiteboard's two front doors: the local HTTP API (for the desktop app
and CLI) and the grouped MCP tools (for AI agents)."""

from __future__ import annotations

import asyncio
import json

import pytest

pytest.importorskip("flask")
pytest.importorskip("mcp")

from whiteboard import mcp_tools
from whiteboard.server import create_app
from whiteboard_helpers import project


# Input: tmp_path
# Output: FlaskClient
# Processing: A test client over a fresh project.
# Changed by: Claude Code
# Date: 2026-10-22
@pytest.fixture
def client(tmp_path):
    return create_app(project(tmp_path)).test_client()


# Input: client, tmp_path
# Output: None
# Processing: Check that http command op board and undo.
# Changed by: Claude Code
# Date: 2026-10-22
def test_http_command_op_board_and_undo(client, tmp_path):
    reply = client.post("/api/v1/whiteboard/command", json={"command": "/file create app.py"})
    assert reply.status_code == 200 and reply.get_json()["ok"]
    assert (tmp_path / "app.py").exists()
    reply = client.post("/api/v1/whiteboard/op", json={"op": "block.add", "args": {"kind": "service", "name": "api"}})
    assert reply.get_json()["data"]["result"]["block"] == "api"
    board = client.get("/api/v1/whiteboard/board").get_json()["data"]
    assert set(board["board"]["blocks"]) == {"app-py", "api"}
    assert board["block_styles"]["api"]["icon"] == "server"
    assert client.post("/api/v1/whiteboard/undo").get_json()["data"]["result"]["op"] == "block.add"
    assert client.post("/api/v1/whiteboard/redo").status_code == 200
    assert [e["op"] for e in client.get("/api/v1/whiteboard/log").get_json()["data"]["entries"]][:2] == \
        ["file.create", "block.add"]


# Input: client
# Output: None
# Processing: Check that http errors use the envelope and status.
# Changed by: Claude Code
# Date: 2026-10-22
def test_http_errors_use_the_envelope_and_status(client):
    reply = client.post("/api/v1/whiteboard/command", json={"command": "/block info nothing"})
    assert reply.status_code == 404
    assert reply.get_json() == {"ok": False, "data": None,
                                "error": {"code": "NOT_FOUND", "message": "no block 'nothing'"}}
    assert client.post("/api/v1/whiteboard/command", json={}).status_code == 400
    assert client.post("/api/v1/whiteboard/command", json={"command": "/layer list"},
                       headers={"X-Plovane-Agent": "ghost"}).status_code == 403


# Input: client
# Output: None
# Processing: Check that http lists every command for the frontend.
# Changed by: Claude Code
# Date: 2026-10-22
def test_http_lists_every_command_for_the_frontend(client):
    listing = client.get("/api/v1/whiteboard/commands").get_json()["data"]["commands"]
    assert len(listing) == 90
    grant = next(c for c in listing if c["command"] == "/agent grant")
    assert grant["confirms"] and grant["people_only"]


# Input: none
# Output: None
# Processing: Check that mcp groups hide people only commands.
# Changed by: Claude Code
# Date: 2026-10-22
def test_mcp_groups_hide_people_only_commands():
    groups = mcp_tools.groups()
    assert len(groups) == 15
    assert set(groups["plovane_agent"]) == {"show", "log"}
    assert groups["plovane_block"]["add"] == "block.add"
    assert groups["plovane_style"]["preset_define"] == "preset.define"


# Input: tmp_path, monkeypatch
# Output: None
# Processing: Check that mcp tools run operations.
# Changed by: Claude Code
# Date: 2026-10-22
def test_mcp_tools_run_operations(tmp_path, monkeypatch):
    from mcp_server.server import mcp

    mcp_tools.set_workspace(project(tmp_path))
    monkeypatch.delenv("PLOVANE_AGENT", raising=False)
    try:
        result = asyncio.run(mcp.call_tool("plovane_project", {"action": "file_create", "args": {"path": "x.py"}}))
        assert (tmp_path / "x.py").exists()
        text = asyncio.run(mcp.call_tool("plovane_command", {"command": "/agent grant me . --mode read"}))
        assert "FORBIDDEN" in json.dumps(str(text))
        wrong = asyncio.run(mcp.call_tool("plovane_block", {"action": "fly"}))
        assert "UNKNOWN_ACTION" in json.dumps(str(wrong))
    finally:
        mcp_tools.set_workspace(None)
    assert result is not None


# Input: none
# Output: None
# Processing: Check that the README's command reference matches the code.
# Changed by: Claude Code
# Date: 2026-10-22
def test_readme_lists_every_command():
    from pathlib import Path

    from whiteboard.commands import markdown

    readme = (Path(__file__).resolve().parent.parent / "whiteboard" / "README.md").read_text()
    assert markdown() in readme, "run: python -m whiteboard.commands --markdown and paste it into whiteboard/README.md"
