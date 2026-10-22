"""The whiteboard engine: parsing, transactions, undo, confirmation, batch,
agent permissions and persistence."""

from __future__ import annotations

import json

import pytest

from backend.whiteboard.commands import COMMANDS, parse
from backend.whiteboard.engine import Workspace
from backend.whiteboard.registry import OPERATIONS, OpError
from whiteboard_helpers import fails, ok, project


# Input: none
# Output: None
# Processing: Check that every sheet command is parsed and registered.
# Changed by: Sourish
# Date: 2026-10-22
def test_every_sheet_command_is_parsed_and_registered():
    import backend.whiteboard.ops  # noqa: F401

    assert len(COMMANDS) == 90
    assert {c.op for c in COMMANDS} == set(OPERATIONS)
    tools = {spec.tool for spec in OPERATIONS.values()}
    assert len(tools) == 90  # the sheet's 89 MCP tools plus /project import


# Input: none
# Output: None
# Processing: Check that parse positionals flags and quotes.
# Changed by: Sourish
# Date: 2026-10-22
def test_parse_positionals_flags_and_quotes():
    assert parse('/block add service "auth" --layer 1') == ("block.add", {"kind": "service", "name": "auth", "layer": "1"})
    assert parse("/zoom layer 2") == ("view.zoom", {"layer": "2"})
    assert parse("/zoom 15") == ("view.zoom", {"level": "15"})
    assert parse("/align a,b,c --top") == ("align", {"blocks": "a,b,c", "edge": "top"})
    assert parse("/git sync --push") == ("git.sync", {"direction": "push"})
    assert parse('/block group auth,users --as "identity"') == ("block.group", {"blocks": "auth,users", "name": "identity"})
    assert parse("/health --run-tests") == ("health.check", {"run_tests": True})


# Input: none
# Output: None
# Processing: Check that parse arrows and endpoints.
# Changed by: Sourish
# Date: 2026-10-22
def test_parse_arrows_and_endpoints():
    assert parse("/connect checkout -> payments --type api --endpoint 'POST /charge'") == (
        "connect", {"source": "checkout", "target": "payments", "type": "api", "endpoint": "POST /charge"})
    assert parse("/arrow style checkout->payments --width 2")[1] == {"source": "checkout", "target": "payments",
                                                                      "width": "2"}
    assert parse("/endpoint link checkout -> payments:POST /charge")[1] == {"source": "checkout",
                                                                            "endpoint": "payments:POST /charge"}
    assert parse("/endpoint info payments:POST /charge")[1] == {"endpoint": "payments:POST /charge"}


# Input: none
# Output: None
# Processing: Check that parse extras rest and batch.
# Changed by: Sourish
# Date: 2026-10-22
def test_parse_extras_rest_and_batch():
    op, args = parse("/block replace step-3 --with retry_request --times 3")
    assert (op, args) == ("block.replace", {"block": "step-3", "entry": "retry_request", "params": {"times": "3"}})
    assert parse('/rank retry failed HTTP requests --k 10')[1] == {"query": "retry failed HTTP requests", "k": "10"}
    assert parse("/template create svc --from layer 1")[1] == {"name": "svc", "from_layer": "1"}
    assert parse("/batch\n/layer list\n/zoom out") == ("batch", {"commands": ["/layer list", "/zoom out"]})


# Input: none
# Output: None
# Processing: Check that parse rejects unknown and malformed.
# Changed by: Sourish
# Date: 2026-10-22
def test_parse_rejects_unknown_and_malformed():
    with pytest.raises(OpError) as unknown:
        parse("/blok add x y")
    assert unknown.value.code == "UNKNOWN_COMMAND"
    with pytest.raises(OpError):
        parse("/connect checkout payments")
    with pytest.raises(OpError):
        parse('/block add service "unterminated')


# Input: tmp_path
# Output: None
# Processing: Check that a failed operation leaves nothing behind.
# Changed by: Sourish
# Date: 2026-10-22
def test_a_failed_operation_leaves_nothing_behind(tmp_path):
    ws = project(tmp_path)
    ok(ws, "/folder create src")
    before = json.dumps(ws.board.to_dict(), sort_keys=True)
    # The second file write succeeds inside the batch, then the import fails.
    reply = ws.command("/batch\n/file create src/a.py\n/connect src/a.py -> nothing --type import")
    assert not reply["ok"] and "item 2" in reply["error"]["message"]
    assert not (tmp_path / "src/a.py").exists()
    assert json.dumps(ws.board.to_dict(), sort_keys=True) == before


# Input: tmp_path
# Output: None
# Processing: Check that undo and redo restore files and board.
# Changed by: Sourish
# Date: 2026-10-22
def test_undo_and_redo_restore_files_and_board(tmp_path):
    ws = project(tmp_path)
    ok(ws, "/file create app.py")
    assert (tmp_path / "app.py").exists()
    ok(ws, "/undo")
    assert not (tmp_path / "app.py").exists() and ws.board.block_for_path("app.py") is None
    ok(ws, "/redo")
    assert (tmp_path / "app.py").exists() and ws.board.block_for_path("app.py") is not None
    assert fails(ws, "/redo") == "NOTHING_TO_REDO"


# Input: tmp_path
# Output: None
# Processing: Check that undo refuses to discard later edits.
# Changed by: Sourish
# Date: 2026-10-22
def test_undo_refuses_to_discard_later_edits(tmp_path):
    ws = project(tmp_path)
    ok(ws, "/file create app.py")
    (tmp_path / "app.py").write_text("edited by hand\n")
    assert fails(ws, "/undo") == "CONFLICT"
    assert (tmp_path / "app.py").read_text() == "edited by hand\n"


# Input: tmp_path
# Output: None
# Processing: Check that destructive commands ask first and preview.
# Changed by: Sourish
# Date: 2026-10-22
def test_destructive_commands_ask_first_and_preview(tmp_path):
    ws = project(tmp_path, {"old/x.py": "X = 1\n"})
    ok(ws, "/project import")
    reply = ws.command("/folder delete old")
    assert reply["ok"] and reply["data"]["status"] == "needs_confirmation"
    assert reply["data"]["preview"]["result"]["files"] == ["old/x.py"]
    assert (tmp_path / "old/x.py").exists()
    ok(ws, "/folder delete old", confirm=True)
    assert not (tmp_path / "old").exists()


# Input: tmp_path
# Output: None
# Processing: Check that dry run changes nothing.
# Changed by: Sourish
# Date: 2026-10-22
def test_dry_run_changes_nothing(tmp_path):
    ws = project(tmp_path)
    reply = ws.command("/file create a.py", dry_run=True)
    assert reply["ok"] and reply["data"]["dry_run"] and reply["data"]["changes"]["files"] == ["a.py"]
    assert not (tmp_path / "a.py").exists() and ws.board.block_for_path("a.py") is None


# Input: tmp_path
# Output: None
# Processing: Check that the board persists across workspaces.
# Changed by: Sourish
# Date: 2026-10-22
def test_the_board_persists_across_workspaces(tmp_path):
    ws = project(tmp_path)
    ok(ws, '/block add service "billing"')
    reopened = Workspace(tmp_path)
    assert reopened.board.block("billing").kind == "service"
    assert (tmp_path / ".plovane/.gitignore").read_text().count("history.json") == 1


# Input: tmp_path
# Output: None
# Processing: Check that paths cannot escape or touch git.
# Changed by: Sourish
# Date: 2026-10-22
def test_paths_cannot_escape_or_touch_git(tmp_path):
    ws = project(tmp_path)
    assert fails(ws, "/file create ../outside.py") == "INVALID_PATH"
    assert fails(ws, "/file create .git/hooks/x.py") == "INVALID_PATH"


# Input: tmp_path
# Output: None
# Processing: Check that agents are held to their grants.
# Changed by: Sourish
# Date: 2026-10-22
def test_agents_are_held_to_their_grants(tmp_path):
    ws = project(tmp_path, {"src/a.py": "A = 1\n", "tests/test_a.py": "def test_a():\n    pass\n"})
    ok(ws, "/project import")
    ok(ws, "/agent add writer")
    ok(ws, "/agent grant writer tests --mode read-write", confirm=True)
    assert ok(ws, "/file create tests/test_b.py", actor="writer")["path"] == "tests/test_b.py"
    assert fails(ws, "/file create src/b.py", actor="writer") == "FORBIDDEN"
    assert fails(ws, "/agent grant writer src --mode read-write", confirm=True, actor="writer") == "FORBIDDEN"
    assert fails(ws, "/layer list", actor="stranger") == "FORBIDDEN"
    ok(ws, "/agent revoke writer tests", confirm=True)
    assert fails(ws, "/file create tests/test_c.py", actor="writer") == "FORBIDDEN"


# Input: tmp_path
# Output: None
# Processing: Check that a narrower none grant carves out a folder.
# Changed by: Sourish
# Date: 2026-10-22
def test_a_narrower_none_grant_carves_out_a_folder(tmp_path):
    ws = project(tmp_path, {"src/a.py": "A = 1\n", "src/secret/k.py": "K = 1\n"})
    ok(ws, "/project import")
    ok(ws, "/agent add bot")
    ok(ws, "/agent grant bot . --mode read-write", confirm=True)
    ok(ws, "/agent revoke bot src/secret", confirm=True)
    shown = ok(ws, "/agent show bot")
    assert "src-secret" not in shown["can_change"] and "a-py" in shown["can_change"]


# Input: tmp_path
# Output: None
# Processing: Check that region grants cover blocks inside the rectangle.
# Changed by: Sourish
# Date: 2026-10-22
def test_region_grants_cover_blocks_inside_the_rectangle(tmp_path):
    ws = project(tmp_path)
    ok(ws, '/block add service "inside" --at 100,100')
    ok(ws, '/block add service "outside" --at 900,900')
    ok(ws, "/agent add designer")
    ok(ws, "/agent region designer --area 0,0,500,500 --mode read-write --layer 1", confirm=True)
    ok(ws, "/place inside --at 120,140", actor="designer")
    assert fails(ws, "/place outside --at 10,10", actor="designer") == "FORBIDDEN"
    assert [e["op"] for e in ok(ws, "/agent log designer")["entries"]] == ["place", "place"]


# Input: tmp_path
# Output: None
# Processing: Check that unknown arguments are reported with the parameters.
# Changed by: Sourish
# Date: 2026-10-22
def test_unknown_arguments_are_reported_with_the_parameters(tmp_path):
    ws = project(tmp_path)
    reply = ws.execute("layer.rename", {"number": 1, "label": "x"})
    assert reply["error"]["code"] == "INVALID_ARGS" and "parameters" in reply["error"]["message"]
