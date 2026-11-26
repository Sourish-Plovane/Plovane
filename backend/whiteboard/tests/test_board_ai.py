"""Tests for the whiteboard AI: it builds and changes the board, never code, and outside agents ask it."""

from __future__ import annotations

from pathlib import Path

import pytest

from backend.whiteboard import board_ai, llm_agent
from backend.whiteboard.engine import BOARD_AI
from whiteboard_helpers import project

FILES = {
    "src/__init__.py": "",
    "src/billing/__init__.py": "",
    "src/billing/charge.py": "def charge(amount):\n    return amount\n",
}


# Input: monkeypatch (pytest.MonkeyPatch), commands (list[str])
# Output: list[str]
# Processing: Replace the model loop with one that runs fixed commands as the whiteboard AI; returns the tasks it was given.
# Changed by: Claude Code
# Date: 2026-11-25
def fake_model(monkeypatch: pytest.MonkeyPatch, commands: list[str]) -> list[str]:
    tasks: list[str] = []

    # Input: task (str), client (Any), **kwargs (Any)
    # Output: dict
    # Processing: Record the task and system prompt, run the commands, report done.
    # Changed by: Claude Code
    # Date: 2026-11-25
    def run(task, *, client, **kwargs):
        tasks.append(task)
        assert kwargs["system"] == board_ai.SYSTEM
        replies = [client.run_command(c) for c in commands]
        return {"status": "done", "summary": "done", "commands": [{"ok": r["ok"]} for r in replies]}

    monkeypatch.setattr(llm_agent, "run", run)
    return tasks


# Input: tmp_path (Path)
# Output: None
# Processing: The whiteboard AI may change the board but is refused any change to a project file.
# Changed by: Claude Code
# Date: 2026-11-25
def test_board_ai_never_writes_code(tmp_path: Path) -> None:
    ws = project(tmp_path, FILES)
    board_ai.ensure_board_ai(ws)
    refused = ws.command("/file create src/new.py", actor=BOARD_AI)
    assert refused["error"]["code"] == "FORBIDDEN" and "never code" in refused["error"]["message"]
    assert not (tmp_path / "src" / "new.py").exists()
    assert ws.command("/block add service payments", actor=BOARD_AI)["ok"]


# Input: tmp_path (Path), monkeypatch (pytest.MonkeyPatch)
# Output: None
# Processing: Without an AI key, build still imports the project from the code and says organising was skipped.
# Changed by: Claude Code
# Date: 2026-11-25
def test_build_without_a_key_imports_only(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    for name in llm_agent.KEY_NAMES.values():
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(llm_agent, "saved_keys", lambda: {})
    ws = project(tmp_path, FILES)
    result = board_ai.build(ws)
    assert result["status"] == "done" and result["organise"] is None and "skipped" in result["note"]
    assert ws.board.block_for_path("src/billing/charge.py") is not None
    assert ws.store.read_log(actor=BOARD_AI, last=5)[-1]["op"] == "project.import"


# Input: tmp_path (Path), monkeypatch (pytest.MonkeyPatch)
# Output: None
# Processing: A provider that refuses the key leaves the import on the board and explains in the note.
# Changed by: Claude Code
# Date: 2026-11-25
def test_build_keeps_the_import_when_the_model_fails(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "bad-key")

    # Input: *_ (Any), **__ (Any)
    # Output: None
    # Processing: Fail the way a refused API key does.
    # Changed by: Claude Code
    # Date: 2026-11-25
    def refuse(*_, **__):
        raise llm_agent.AgentError("Claude returned an error: invalid x-api-key")

    monkeypatch.setattr(llm_agent, "run", refuse)
    ws = project(tmp_path, FILES)
    result = board_ai.build(ws)
    assert "invalid x-api-key" in result["note"] and ws.board.block_for_path("src/billing/charge.py")


# Input: tmp_path (Path), monkeypatch (pytest.MonkeyPatch)
# Output: None
# Processing: With a key, build imports and then hands the board to the whiteboard AI to organise on Haiku.
# Changed by: Claude Code
# Date: 2026-11-25
def test_build_then_organise(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.delenv("PLOVANE_BOARD_AI_MODEL", raising=False)
    monkeypatch.delenv("PLOVANE_BOARD_AI_PROVIDER", raising=False)
    tasks = fake_model(monkeypatch, ['/sketch service billing; external stripe; billing -> stripe "charges cards"'])
    ws = project(tmp_path, FILES)
    result = board_ai.build(ws)
    assert "Organise" in tasks[0] and result["organise"]["status"] == "done"
    assert "billing" in ws.board.blocks and "stripe" in ws.board.blocks
    assert board_ai.provider_and_model() == ("anthropic", "claude-haiku-5-5")


# Input: tmp_path (Path), monkeypatch (pytest.MonkeyPatch)
# Output: None
# Processing: The person asks over HTTP; an outside agent's direct board change is refused and pointed at /ask.
# Changed by: Claude Code
# Date: 2026-11-25
def test_ask_endpoint_and_outside_agents_are_ask_only(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    pytest.importorskip("flask")
    from backend.whiteboard.server import create_app

    tasks = fake_model(monkeypatch, ["/block add service orders"])
    ws = project(tmp_path, FILES)
    ws.command("/agent add cursor")
    ws.command("/agent grant cursor . --mode read-write", confirm=True)
    client = create_app(ws).test_client()

    reply = client.post("/api/v1/whiteboard/ask", json={"message": "add an orders service"}).get_json()
    assert reply["ok"] and reply["data"]["requester"] == "user" and "the person" in tasks[0]
    assert "orders" in ws.board.blocks

    direct = client.post("/api/v1/whiteboard/op", json={"op": "block.add", "args": {"kind": "service", "name": "x"}},
                         headers={"X-Plovane-Agent": "cursor"})
    assert direct.status_code == 403 and "/api/v1/whiteboard/ask" in direct.get_json()["error"]["message"]
    asked = client.post("/api/v1/whiteboard/ask", json={"message": "add x"}, headers={"X-Plovane-Agent": "cursor"})
    assert asked.get_json()["data"]["requester"] == "cursor" and "'cursor'" in tasks[1]
    built = client.post("/api/v1/whiteboard/build", json={}, headers={"X-Plovane-Agent": "cursor"})
    assert built.status_code == 403


# Input: tmp_path (Path)
# Output: None
# Processing: /project refresh adds new files, follows added and removed imports, and marks deleted files missing.
# Changed by: Claude Code
# Date: 2026-11-25
def test_refresh_follows_outside_edits(tmp_path: Path) -> None:
    ws = project(tmp_path, FILES)
    ws.command("/project import", confirm=True)
    refund = tmp_path / "src" / "billing" / "refund.py"
    refund.write_text("from src.billing.charge import charge\n\n\ndef refund(amount):\n    return charge(-amount)\n")
    first = ws.command("/project refresh --paths src/billing/refund.py")["data"]["result"]
    source = ws.board.block_for_path("src/billing/refund.py")
    assert source is not None and first["import_arrows_added"]
    refund.write_text("def refund(amount):\n    return -amount\n")
    second = ws.command("/project refresh --paths src/billing/refund.py")["data"]["result"]
    assert second["import_arrows_removed"] and not any(
        a.source == source.id and a.type == "import" for a in ws.board.arrows.values())
    refund.unlink()
    third = ws.command("/project refresh --paths src/billing/refund.py")["data"]["result"]
    assert source.id in third["missing_on_disk"]


# Input: tmp_path (Path), monkeypatch (pytest.MonkeyPatch)
# Output: None
# Processing: A file written outside the whiteboard is put on the board after edits settle; only its new kind of signal reaches the AI.
# Changed by: Claude Code
# Date: 2026-11-25
def test_watcher_follows_an_outside_edit(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import threading

    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    tasks = fake_model(monkeypatch, [])
    ws = project(tmp_path, FILES)
    ws.command("/project import", confirm=True)
    watcher = board_ai.ProjectWatcher(ws, threading.RLock(), quiet=1.0)
    (tmp_path / "src" / "billing" / "refund.py").write_text(
        "import requests\n\n\ndef refund(charge_id):\n"
        "    return requests.post('https://api.stripe.com/v1/refunds', data={'charge': charge_id})\n")
    assert watcher.poll(now=10.0) is None  # still settling
    result = watcher.poll(now=11.5)
    assert result["added"] == ["src/billing/refund.py"] and result["refresh"]["ok"]
    assert ws.board.block_for_path("src/billing/refund.py") is not None
    assert any(b.kind == "external" and "stripe" in b.name for b in ws.board.blocks.values())
    assert result["structural"] and "+def refund(charge_id):" in result["diff"]
    # The first outside API is a new kind of signal: the AI is asked once whether an overlay should show it.
    assert result["overlays"]["new"] == ["outside_apis"] and len(tasks) == 1 and "outside APIs" in tasks[0]
    assert watcher.poll(now=20.0) is None  # nothing new


# Input: tmp_path (Path), monkeypatch (pytest.MonkeyPatch)
# Output: None
# Processing: Files the whiteboard itself writes are not treated as outside edits.
# Changed by: Claude Code
# Date: 2026-11-25
def test_watcher_ignores_the_boards_own_writes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import threading

    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    tasks = fake_model(monkeypatch, [])
    ws = project(tmp_path, FILES)
    ws.command("/project import", confirm=True)
    watcher = board_ai.ProjectWatcher(ws, threading.RLock(), quiet=1.0)
    assert ws.command("/file create src/billing/notes.py")["ok"]
    assert watcher.poll(now=10.0) is None and watcher.poll(now=20.0) is None and tasks == []


# Input: tmp_path (Path), monkeypatch (pytest.MonkeyPatch)
# Output: None
# Processing: An edit inside a function refreshes the board and is not counted as a structural change.
# Changed by: Claude Code
# Date: 2026-11-25
def test_watcher_small_edits_are_not_structural(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import threading

    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    tasks = fake_model(monkeypatch, [])
    ws = project(tmp_path, FILES)
    ws.command("/project import", confirm=True)
    watcher = board_ai.ProjectWatcher(ws, threading.RLock(), quiet=1.0)
    (tmp_path / "src" / "billing" / "charge.py").write_text("def charge(amount):\n    return round(amount, 2)\n")
    watcher.poll(now=10.0)
    result = watcher.poll(now=12.0)
    assert result["edited"] == ["src/billing/charge.py"] and not result["structural"] and tasks == []


# Input: tmp_path (Path)
# Output: None
# Processing: A person's move pins the block's place; the whiteboard AI cannot move, rename or delete it, but can still sketch around it.
# Changed by: Claude Code
# Date: 2026-11-26
def test_hand_edits_pin_and_the_ai_leaves_them(tmp_path: Path) -> None:
    ws = project(tmp_path, FILES)
    board_ai.ensure_board_ai(ws)
    assert ws.command("/sketch service payments; service orders; orders -> payments", actor=BOARD_AI)["ok"]
    assert ws.command("/block move payments --at 900,700")["ok"]
    payments = ws.board.blocks["payments"]
    assert set(payments.meta["pinned"]) == {"x", "y"}
    spot = (payments.x, payments.y)

    moved = ws.command("/block move payments --at 10,10", actor=BOARD_AI)
    assert moved["error"]["code"] == "FORBIDDEN" and "pinned" in moved["error"]["message"]
    assert ws.command("/block delete payments", actor=BOARD_AI, confirm=True)["error"]["code"] == "FORBIDDEN"
    # Re-laying out the services skips the pinned block instead of failing.
    assert ws.command("/sketch service refunds; refunds -> payments", actor=BOARD_AI)["ok"]
    assert (ws.board.blocks["payments"].x, ws.board.blocks["payments"].y) == spot
    # Its name was not pinned, so the AI may still rename it.
    assert ws.command("/sketch rename payments \"card payments\"", actor=BOARD_AI)["ok"]


# Input: tmp_path (Path)
# Output: None
# Processing: /pin holds every layout field until /unpin; an unknown field is refused.
# Changed by: Claude Code
# Date: 2026-11-26
def test_pin_and_unpin(tmp_path: Path) -> None:
    ws = project(tmp_path, FILES)
    board_ai.ensure_board_ai(ws)
    ws.command("/sketch service payments", actor=BOARD_AI)
    assert ws.command("/pin payments --fields colour")["error"]["code"] == "INVALID_ARGS"
    assert "name" in ws.command("/pin payments")["data"]["result"]["pinned"]
    assert ws.command("/sketch rename payments billing", actor=BOARD_AI)["error"]["code"] == "FORBIDDEN"
    assert ws.command("/unpin payments")["ok"]
    assert ws.command("/sketch rename payments billing", actor=BOARD_AI)["ok"]
