"""Saving the whole whiteboard to whiteboard.json and opening it again."""

from __future__ import annotations

import json

import pytest

from backend.whiteboard.ops.boardfile import document
from whiteboard_helpers import ok, project


# Input: ws (Workspace), command (str), confirm (bool), actor (str)
# Output: str
# Processing: Run a command that must fail and return its error code.
# Changed by: Claude Code
# Date: 2026-11-22
def fails(ws, command: str, *, confirm: bool = False, actor: str = "user") -> str:
    reply = ws.command(command, confirm=confirm, actor=actor)
    assert not reply["ok"], f"{command} should have failed"
    return reply["error"]["code"]


# Input: tmp_path
# Output: Workspace
# Processing: A project with files, a service, an arrow, a note, a style and an agent with access.
# Changed by: Claude Code
# Date: 2026-11-22
def busy(tmp_path):
    ws = project(tmp_path, {"src/a.py": "A = 1\n", "src/b.py": "import a\n"})
    ok(ws, "/project import")
    ok(ws, '/block add service "payments" --at 120,80')
    ok(ws, '/block add service "orders" --at 420,80')
    ok(ws, "/connect orders -> payments --type api")
    ok(ws, "/style payments --fill #E8F0FE")
    ok(ws, "/agent add bot")
    ok(ws, "/agent grant bot src --mode read-write", confirm=True)
    return ws


# Input: tmp_path
# Output: None
# Processing: Check that saving then loading on an empty board gives back exactly the same board.
# Changed by: Claude Code
# Date: 2026-11-22
def test_save_then_load_restores_the_exact_board(tmp_path):
    ws = busy(tmp_path)
    saved = ok(ws, "/board save")
    assert saved["file"] == "whiteboard.json" and saved["saved"] is True
    original = document(ws.board)
    ok(ws, "/batch /block delete payments\n/block delete orders")
    ok(ws, "/agent revoke bot src", confirm=True)
    assert document(ws.board) != original
    ok(ws, "/board load", confirm=True)
    assert document(ws.board) == original


# Input: tmp_path
# Output: None
# Processing: Check that the file is stable JSON with sorted keys and no machine-local fields.
# Changed by: Claude Code
# Date: 2026-11-22
def test_file_is_stable_sorted_json_without_local_fields(tmp_path):
    ws = busy(tmp_path)
    ok(ws, "/board save")
    text = (tmp_path / "whiteboard.json").read_text()
    data = json.loads(text)
    assert data["format"] == "plovane-whiteboard" and data["version"] == 1
    assert "compiles" not in data["board"] and "alerts" not in data["board"]
    assert text == json.dumps(data, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    assert ok(ws, "/board save")["saved"] is False
    assert (tmp_path / "whiteboard.json").read_text() == text


# Input: tmp_path
# Output: None
# Processing: Check named files, the added extension, and refusing paths outside the project.
# Changed by: Claude Code
# Date: 2026-11-22
def test_named_files_get_json_and_must_stay_in_the_project(tmp_path):
    ws = busy(tmp_path)
    assert ok(ws, "/board save designs/v2")["file"] == "designs/v2.json"
    assert (tmp_path / "designs" / "v2.json").is_file()
    assert fails(ws, "/board save ../outside") == "INVALID_PATH"
    assert fails(ws, "/board save notes.txt") == "INVALID_PATH"
    assert fails(ws, "/board save .plovane/board") == "INVALID_PATH"


# Input: tmp_path
# Output: None
# Processing: Check that loading asks first, previews access changes, and changes no project files.
# Changed by: Claude Code
# Date: 2026-11-22
def test_load_asks_first_and_previews_access_changes(tmp_path):
    ws = busy(tmp_path)
    ok(ws, "/board save")
    ok(ws, "/agent revoke bot src", confirm=True)
    before_files = {p.relative_to(tmp_path) for p in tmp_path.rglob("*") if ".plovane" not in p.parts}
    reply = ws.command("/board load")
    assert reply["ok"] and reply["data"]["status"] == "needs_confirmation"
    preview = reply["data"]["preview"]["result"]
    assert [c["agent"] for c in preview["access_changes"]] == ["bot"]
    assert preview["access_changes"][0]["after"]["grants"] == [{"path": "src", "mode": "read-write"}]
    assert ws.board.agents["bot"].grants == []
    loaded = ws.command("/board load", confirm=True)
    assert loaded["ok"] and loaded["data"]["changes"].get("files") is None
    after_files = {p.relative_to(tmp_path) for p in tmp_path.rglob("*") if ".plovane" not in p.parts}
    assert after_files == before_files
    assert ws.board.agents["bot"].grants == [{"path": "src", "mode": "read-write"}]


# Input: tmp_path
# Output: None
# Processing: Check that a load is one undo step.
# Changed by: Claude Code
# Date: 2026-11-22
def test_load_is_one_undo_step(tmp_path):
    ws = busy(tmp_path)
    ok(ws, "/board save")
    ok(ws, "/batch /block delete payments\n/block delete orders")
    without = document(ws.board)
    ok(ws, "/board load", confirm=True)
    assert "payments" in ws.board.blocks
    ok(ws, "/undo")
    assert document(ws.board) == without


# Input: tmp_path
# Output: None
# Processing: Check that agents can neither save nor load a whiteboard file.
# Changed by: Claude Code
# Date: 2026-11-22
def test_agents_may_not_save_or_load(tmp_path):
    ws = busy(tmp_path)
    ok(ws, "/board save")
    assert fails(ws, "/board save", actor="bot") == "FORBIDDEN"
    assert fails(ws, "/board load", actor="bot", confirm=True) == "FORBIDDEN"


# Input: tmp_path
# Output: None
# Processing: Check that broken, foreign, future and inconsistent files are refused and the board is untouched.
# Changed by: Claude Code
# Date: 2026-11-22
@pytest.mark.parametrize("content, wanted", [
    ("{not json", "not valid JSON"),
    (json.dumps({"blocks": {}}), "not a Plovane whiteboard file"),
    (json.dumps({"format": "plovane-whiteboard", "version": 99, "board": {}}), "version 99"),
    (json.dumps({"format": "plovane-whiteboard", "version": 1, "board": {"blocks": {"x": {"id": "x"}}}}),
     "malformed"),
    (json.dumps({"format": "plovane-whiteboard", "version": 1, "board": {"arrows": {
        "a": {"id": "a", "source": "ghost", "target": "ghost2"}}}}), "arrows point at blocks"),
])
def test_bad_files_are_refused_and_the_board_is_untouched(tmp_path, content, wanted):
    ws = busy(tmp_path)
    original = document(ws.board)
    (tmp_path / "whiteboard.json").write_text(content)
    reply = ws.command("/board load", confirm=True)
    assert not reply["ok"] and reply["error"]["code"] == "INVALID_FILE"
    assert wanted in reply["error"]["message"]
    assert document(ws.board) == original


# Input: tmp_path
# Output: None
# Processing: Check that blocks for files this project lacks are reported, and a missing file is NOT_FOUND.
# Changed by: Claude Code
# Date: 2026-11-22
def test_missing_paths_are_reported_and_missing_file_is_not_found(tmp_path):
    ws = busy(tmp_path)
    assert fails(ws, "/board load", confirm=True) == "NOT_FOUND"
    ok(ws, "/board save")
    (tmp_path / "src" / "b.py").unlink()
    result = ws.command("/board load", confirm=True)["data"]
    assert result["result"]["missing_paths"] == ["src/b.py"]
    assert any("don't have" in note or "doesn't have" in note for note in result["notes"])


# Input: tmp_path
# Output: None
# Processing: Check the HTTP download (person only) and opening uploaded text through the op endpoint.
# Changed by: Claude Code
# Date: 2026-11-22
def test_http_download_and_upload(tmp_path):
    pytest.importorskip("flask")
    from backend.whiteboard.server import create_app

    ws = busy(tmp_path)
    client = create_app(ws).test_client()
    reply = client.get("/api/v1/whiteboard/board/file")
    assert reply.status_code == 200
    assert 'filename="whiteboard.json"' in reply.headers["Content-Disposition"]
    text = reply.get_data(as_text=True)
    assert json.loads(text)["format"] == "plovane-whiteboard"
    assert client.get("/api/v1/whiteboard/board/file", headers={"X-Plovane-Agent": "bot"}).status_code == 403
    ok(ws, "/batch /block delete payments\n/block delete orders")
    asked = client.post("/api/v1/whiteboard/op", json={"op": "board.load", "args": {"content": text}}).get_json()
    assert asked["data"]["status"] == "needs_confirmation"
    done = client.post("/api/v1/whiteboard/op",
                       json={"op": "board.load", "args": {"content": text}, "confirm": True}).get_json()
    assert done["ok"] and "payments" in ws.board.blocks
