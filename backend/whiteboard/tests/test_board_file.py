"""Whiteboard files (#72): every whiteboard is a .plo file full of commands, one tab each."""

from __future__ import annotations

import json
import shutil

import pytest

from backend.whiteboard.engine import Workspace
from whiteboard_helpers import fails, ok, project

FILES = {"src/app.py": "import os\n\ndef run():\n    return 1\n", "src/util.py": "x = 1\n", "lib/b.py": "y = 2\n"}


# Input: tmp_path
# Output: Workspace
# Processing: A project whose board uses most of what a file must carry.
# Changed by: Claude Code
# Date: 2026-11-15
def busy_board(tmp_path):
    ws = project(tmp_path / "a", FILES)
    ok(ws, "/project import")
    assert ws.execute("block.add", {"kind": "service", "name": "pay -> api", "at": "100,100"})["ok"]
    for command in ['/block add service "auth" --at 400,100', '/block rename auth "Auth service"',
                    "/style pay-api --fill #112233 --shadow strong", "/size pay-api 250x130", "/theme dark",
                    "/grid 10 --snap off", "/preset define fancy --fill #ABCDEF --radius 3",
                    "/style auth --preset fancy", '/layer rename 2 "Dirs"', "/layer edit 5 --zoom 90-95",
                    '/layer define 7 --zoom 95-100 --name "Seven"', "/endpoint add pay-api POST /charge",
                    "/connect auth -> pay-api --type api --endpoint 'POST /charge'",
                    '/template create "pair" --blocks auth,pay-api',
                    "/agent add claude", "/agent grant claude src --mode read-write",
                    "/agent revoke claude src/util.py", "/agent region claude --area 0,0,500,500 --mode read --layer 1"]:
        ok(ws, command, confirm=True)
    # Text the command parser would misread travels JSON-encoded.
    assert ws.execute("arrow.style", {"source": "auth", "target": "pay-api", "label": "a -> b"})["ok"]
    assert ws.execute("layer.rename", {"number": 7, "name": '--weird "q"'})["ok"]
    return ws


# Input: before (Workspace), after (Workspace)
# Output: None
# Processing: Check two whiteboards hold the same drawing, agents and access.
# Changed by: Claude Code
# Date: 2026-11-15
def assert_same(before, after):
    a, b = before.board.to_dict(), after.board.to_dict()
    for key in ("layers", "blocks", "arrows", "endpoints", "presets", "agents", "templates"):
        assert a[key] == b[key], key
    assert a["view"]["theme"] == b["view"]["theme"] and a["view"]["grid"] == b["view"]["grid"]
    assert before.access_map() == after.access_map()


# Input: tmp_path
# Output: None
# Processing: Every change rewrites whiteboard.plo, and a clone with only that file shows the same board.
# Changed by: Claude Code
# Date: 2026-11-15
def test_whiteboard_plo_is_the_board(tmp_path):
    ws = busy_board(tmp_path)
    content = json.loads((tmp_path / "a/whiteboard.plo").read_text())
    assert content["format"] == "plovane-whiteboard" and "saved_at" not in content
    assert any(c.startswith("/agent put claude ") for c in content["commands"])
    assert any(c.startswith("/template put pair ") for c in content["commands"])

    # A teammate clones the repository: the code and whiteboard.plo, no .plovane/.
    shutil.copytree(tmp_path / "a", tmp_path / "b", ignore=shutil.ignore_patterns(".plovane"))
    clone = Workspace(tmp_path / "b", library=ws._library, llm=ws._llm)
    assert clone.plo_error is None
    assert_same(ws, clone)
    ok(clone, "/size auth 300x100")  # saving rewrites the file only where the board changed
    lines = json.loads((tmp_path / "b/whiteboard.plo").read_text())["commands"]
    assert sorted(set(lines) ^ set(content["commands"])) == sorted(
        [c for c in content["commands"] if c.startswith("/block put auth ")]
        + [c for c in lines if c.startswith("/block put auth ")])


# Input: tmp_path
# Output: None
# Processing: A changed .plo (git pull, hand edit) redraws the board; a broken one is left alone until fixed.
# Changed by: Claude Code
# Date: 2026-11-15
def test_changed_and_broken_files(tmp_path):
    ws = project(tmp_path, FILES)
    ok(ws, '/block add service "auth"')
    plo = tmp_path / "whiteboard.plo"
    content = json.loads(plo.read_text())
    content["commands"].append('/block put db --kind database --name "Orders DB" --layer 1 --at 300,0 --size 140x100')
    plo.write_text(json.dumps(content))
    events = []
    ws.listeners.append(events.append)
    assert ws.sync() and "db" in ws.board.blocks and events[-1]["kind"] == "external"

    plo.write_text("<<<<<<< HEAD\n{}\n>>>>>>> theirs\n")  # a merge conflict
    ws.sync()
    assert ws.plo_error and "db" in ws.board.blocks  # last good state
    ok(ws, '/block add service "cache"')
    assert plo.read_text().startswith("<<<<<<<")  # never overwritten while broken
    ok(ws, "/board save")  # a person chooses to write the board out again
    assert ws.plo_error is None
    assert any(c.startswith("/block put cache ") for c in json.loads(plo.read_text())["commands"])


# Input: tmp_path
# Output: None
# Processing: Several whiteboards: separate drawings, one set of project agents.
# Changed by: Claude Code
# Date: 2026-11-15
def test_many_whiteboards_share_the_projects_agents(tmp_path):
    main = project(tmp_path, FILES)
    ok(main, '/block add service "auth"')
    ok(main, "/agent add claude")
    assert ok(main, "/board new designs/payments")["board"] == "designs/payments.plo"
    assert fails(main, "/board new designs/payments") == "CONFLICT"
    pay = Workspace(tmp_path, library=main._library, llm=main._llm, board="designs/payments.plo", main=main)
    ok(pay, '/block add service "payments"')
    assert set(pay.board.blocks) == {"payments"} and "payments" not in main.board.blocks
    assert ok(main, "/boards")["boards"] == ["whiteboard.plo", "designs/payments.plo"]

    # Granting from the payments tab changes the project's agents, and every tab sees it.
    ok(pay, "/agent grant claude src --mode read", confirm=True)
    assert main.board.agents["claude"].grants == [{"path": "src", "mode": "read"}]
    assert pay.board.agents["claude"].grants == main.board.agents["claude"].grants
    reply = pay.execute("batch", {"commands": ["/block add note n1", "/agent revoke claude src"]}, confirm=True)
    assert reply["error"]["code"] == "FORBIDDEN" and "n1" not in pay.board.blocks

    lines = json.loads((tmp_path / "designs/payments.plo").read_text())["commands"]
    assert lines and not any(c.startswith("/agent put") for c in lines)  # agents live in whiteboard.plo only
    ok(pay, "/undo")
    assert "payments" not in pay.board.blocks and main.board.agents["claude"].grants  # separate undo

    # A file with agents opened as a second whiteboard keeps the project's agents.
    ok(main, "/board save designs/copy")
    copy = Workspace(tmp_path, library=main._library, llm=main._llm, board="designs/copy.plo", main=main)
    assert "auth" in copy.board.blocks and copy.board.agents == main.board.agents


# Input: tmp_path
# Output: None
# Processing: Whiteboard files change only through /board commands, and loading needs a person and confirmation.
# Changed by: Claude Code
# Date: 2026-11-15
def test_files_are_protected_and_loads_confirm(tmp_path):
    ws = project(tmp_path, FILES)
    ok(ws, '/block add service "auth"')
    ok(ws, "/agent add claude")
    ok(ws, "/agent grant claude . --mode read-write", confirm=True)
    assert fails(ws, '/file save whiteboard.plo --content "{}"') == "INVALID_PATH"
    assert fails(ws, '/file save whiteboard.plo --content "{}"', actor="claude") == "INVALID_PATH"
    assert ws.command("/board load", actor="claude", confirm=True)["error"]["code"] == "FORBIDDEN"
    assert ws.command("/board save x", actor="claude")["error"]["code"] == "FORBIDDEN"
    assert ws.command("/block put x --kind note --name x --layer 1", actor="claude")["error"]["code"] == "FORBIDDEN"
    ok(ws, "/board save designs/v1")
    ok(ws, "/block delete auth", confirm=True)
    reply = ws.command("/board load designs/v1.plo")
    assert reply["data"]["status"] == "needs_confirmation" and "auth" not in ws.board.blocks
    ok(ws, "/board load designs/v1.plo", confirm=True)
    assert "auth" in ws.board.blocks
    ok(ws, "/undo")  # one load is one undo step
    assert "auth" not in ws.board.blocks


# Input: tmp_path
# Output: None
# Processing: Bad files are refused whole: wrong format, file-changing commands, bad lines, escapes.
# Changed by: Claude Code
# Date: 2026-11-15
def test_bad_files_change_nothing(tmp_path):
    ws = project(tmp_path, FILES)
    ok(ws, '/block add service "keep"')
    (tmp_path / "other.json").write_text(json.dumps({"hello": 1}))
    (tmp_path / "writes.json").write_text(json.dumps({"format": "plovane-whiteboard", "version": 1, "commands": [
        "/block put a --kind note --name a --layer 1", "/file save src/app.py --content gone"]}))
    (tmp_path / "broken.json").write_text(json.dumps({"format": "plovane-whiteboard", "version": 1, "commands": [
        "/block put a --kind note --name a --layer 1", "/arrow put a -> nowhere"]}))
    assert fails(ws, "/board load other.json", confirm=True) == "INVALID_ARGS"
    assert fails(ws, "/board load writes.json", confirm=True) == "FORBIDDEN"
    assert fails(ws, "/board load broken.json", confirm=True) == "NOT_FOUND"
    assert fails(ws, "/board save ../outside") in ("INVALID_ARGS", "INVALID_PATH")
    assert fails(ws, "/board new ../outside") in ("INVALID_ARGS", "INVALID_PATH")
    assert list(ws.board.blocks) == ["keep"] and (tmp_path / "src/app.py").read_text() == FILES["src/app.py"]


# Input: tmp_path
# Output: None
# Processing: A hand-written file opens as a new whiteboard without touching project files.
# Changed by: Claude Code
# Date: 2026-11-15
def test_hand_written_file_opens_as_a_new_whiteboard(tmp_path):
    main = project(tmp_path, FILES)
    content = {"format": "plovane-whiteboard", "version": 1, "commands": [
        "# a sketch",
        '/block put api --kind service --name "API" --layer 1 --at 0,0 --size 200x100',
        '/block put db --kind database --name "Orders DB" --layer 1 --at 300,0 --size 140x100',
        "/arrow put api -> db --type database",
        "/style db --fill #E6F4EA",
    ]}
    assert main.execute("board.new", {"path": "sketch", "content": json.dumps(content)})["ok"]
    sketch = Workspace(tmp_path, library=main._library, llm=main._llm, board="sketch.plo", main=main)
    assert set(sketch.board.blocks) == {"api", "db"} and len(sketch.board.arrows) == 1
    assert sketch.board.blocks["db"].style == {"fill": "#E6F4EA"}
    assert (tmp_path / "src/app.py").read_text() == FILES["src/app.py"]


# Input: tmp_path
# Output: None
# Processing: The server serves each whiteboard by name, and the download is the person's only.
# Changed by: Claude Code
# Date: 2026-11-15
def test_server_serves_each_whiteboard(tmp_path):
    pytest.importorskip("flask")
    from backend.whiteboard.server import create_app

    ws = project(tmp_path, FILES)
    ok(ws, '/block add service "auth"')
    ok(ws, "/board new designs/payments")
    client = create_app(ws).test_client()
    other = {"X-Plovane-Board": "designs/payments.plo"}
    assert client.post("/api/v1/whiteboard/command", json={"command": '/block add service "pay"'},
                       headers=other).get_json()["ok"]
    main_board = client.get("/api/v1/whiteboard/board").get_json()["data"]
    pay_board = client.get("/api/v1/whiteboard/board?board=designs/payments.plo").get_json()["data"]
    assert set(main_board["board"]["blocks"]) == {"auth"} and set(pay_board["board"]["blocks"]) == {"pay"}
    assert pay_board["file"] == "designs/payments.plo"
    listing = client.get("/api/v1/whiteboard/boards").get_json()["data"]
    assert listing["boards"] == ["whiteboard.plo", "designs/payments.plo"]
    missing = client.get("/api/v1/whiteboard/board", headers={"X-Plovane-Board": "nope.plo"}).get_json()
    assert missing["error"]["code"] == "NOT_FOUND"
    data = client.get("/api/v1/whiteboard/board/file", headers=other).get_json()["data"]
    assert data["name"] == "payments.plo" and any(c.startswith("/block put pay ") for c in data["file"]["commands"])
    refused = client.get("/api/v1/whiteboard/board/file", headers={"X-Plovane-Agent": "claude"}).get_json()
    assert refused["error"]["code"] == "FORBIDDEN"


# Input: tmp_path
# Output: None
# Processing: The server's watcher redraws any open whiteboard whose .plo changes (git pull), without a request.
# Changed by: Claude Code
# Date: 2026-11-15
def test_watcher_picks_up_a_pulled_plo(tmp_path):
    import threading
    import time

    from backend.whiteboard.server import start_watcher

    main = project(tmp_path, FILES)
    ok(main, "/board new designs/payments")
    pay = Workspace(tmp_path, library=main._library, llm=main._llm, board="designs/payments.plo", main=main)
    events = []
    pay.listeners.append(events.append)
    start_watcher(main, threading.RLock(), interval=0.05, boards={"whiteboard.plo": main, "designs/payments.plo": pay})
    plo = tmp_path / "designs/payments.plo"
    content = json.loads(plo.read_text())
    content["commands"].append('/block put pay --kind service --name pay --layer 1 --at 0,0 --size 200x100')
    plo.write_text(json.dumps(content))
    for _ in range(40):
        if "pay" in pay.board.blocks and events:  # the event follows the board change by a moment
            break
        time.sleep(0.05)
    assert "pay" in pay.board.blocks and events and events[-1]["board"] == "designs/payments.plo"


# Input: tmp_path, monkeypatch
# Output: None
# Processing: PLOVANE_BOARD (plovane mcp --board) points an agent at another whiteboard, in-process and through the server.
# Changed by: Claude Code
# Date: 2026-11-15
def test_mcp_board_setting(tmp_path, monkeypatch):
    from backend.whiteboard import llm_agent, mcp_tools

    main = project(tmp_path, FILES)
    ok(main, "/board new designs/payments")
    monkeypatch.setenv("PLOVANE_PROJECT", str(tmp_path))
    monkeypatch.setenv("PLOVANE_BOARD", "designs/payments.plo")
    mcp_tools.set_workspace(None)
    try:
        assert mcp_tools.workspace().board_file == "designs/payments.plo"
    finally:
        mcp_tools.set_workspace(None)
    sent = {}
    monkeypatch.setenv("PLOVANE_URL", "http://127.0.0.1:1")
    monkeypatch.setenv("PLOVANE_AGENT_TOKEN", "plv_x")
    monkeypatch.setattr(llm_agent, "http_json", lambda url, headers, payload, timeout: sent.update(payload) or {"ok": True})
    mcp_tools.remote("command", {"command": "/board"})
    assert sent == {"command": "/board", "board": "designs/payments.plo"}
