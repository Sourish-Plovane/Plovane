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
