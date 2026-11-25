"""Tests for /overlay propose: LLM-authored overlays."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from backend.whiteboard.ops import overlay_propose
from whiteboard_helpers import ok, project

CHARGE_PY = '''def charge(amount):
    raise ValueError("bad amount")
'''

FILES = {
    "src/__init__.py": "",
    "src/billing/__init__.py": "",
    "src/billing/charge.py": CHARGE_PY,
}


# Input: monkeypatch (pytest.MonkeyPatch), text (str)
# Output: None
# Processing: Force every LLM call in /overlay propose to return the given text, with a key present.
# Changed by: Claude Code
# Date: 2026-11-25
def stub_llm(monkeypatch: pytest.MonkeyPatch, text: str) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setattr(overlay_propose, "ask_for_json",
                        lambda provider, model, key, prompt: text)


# Input: tmp_path (Path), monkeypatch (pytest.MonkeyPatch)
# Output: None
# Processing: A clean JSON reply with a real block id becomes a saved overlay the board can switch on.
# Changed by: Claude Code
# Date: 2026-11-25
def test_overlay_propose_saves_and_registers(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    ws = project(tmp_path, FILES)
    ok(ws, "/project import", confirm=True)
    charge = next(b for b in ws.main.board.blocks.values() if b.path == "src/billing/charge.py")

    reply = json.dumps({"name": "hot files", "label": "Hot files", "channel": "badge", "priority": 70,
                        "style": {"color": "red", "badge": "hot"},
                        "reasoning": "charge.py is the only file in the project right now.",
                        "items": {charge.id: [{"message": "touched by the goal", "count": 1}]}})
    stub_llm(monkeypatch, reply)

    result = ok(ws, '/overlay propose "find the hottest files"')

    oid = result["overlay"]
    assert oid and oid.startswith("hot_files")
    spec = ws.main.board.overlays[oid]
    assert spec["source"] == "custom" and spec["channel"] == "badge"
    saved = json.loads((tmp_path / ".plovane" / "overlays" / f"{oid}.json").read_text())
    assert saved["items"][charge.id][0]["message"] == "touched by the goal"


# Input: tmp_path (Path), monkeypatch (pytest.MonkeyPatch)
# Output: None
# Processing: After /overlay on, overlay_state returns items for the block the LLM picked.
# Changed by: Claude Code
# Date: 2026-11-25
def test_custom_overlay_items_feed_overlay_state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    ws = project(tmp_path, FILES)
    ok(ws, "/project import", confirm=True)
    charge = next(b for b in ws.main.board.blocks.values() if b.path == "src/billing/charge.py")

    reply = json.dumps({"name": "errors", "label": "LLM errors", "channel": "badge", "priority": 60,
                        "reasoning": "one file", "style": {"color": "red"},
                        "items": {charge.id: [{"message": "it crashed", "count": 2}]}})
    stub_llm(monkeypatch, reply)
    result = ok(ws, '/overlay propose "where do errors live"')
    oid = result["overlay"]

    ok(ws, f"/overlay on {oid}")
    from backend.whiteboard.overlays import overlay_state
    state = overlay_state(ws.main, ws.main.board, "user")
    results = state["results"][oid]
    assert charge.id in results
    assert results[charge.id]["items"][0]["message"] == "it crashed"


# Input: tmp_path (Path), monkeypatch (pytest.MonkeyPatch)
# Output: None
# Processing: An invented block id is dropped; an overlay with no surviving items returns ok=True but no overlay.
# Changed by: Claude Code
# Date: 2026-11-25
def test_overlay_propose_drops_unknown_block_ids(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    ws = project(tmp_path, FILES)
    ok(ws, "/project import", confirm=True)
    reply = json.dumps({"name": "ghosts", "label": "Ghost blocks", "channel": "badge",
                        "reasoning": "none", "items": {"not_a_real_block": [{"message": "x"}]}})
    stub_llm(monkeypatch, reply)
    result = ok(ws, '/overlay propose "ghosts"')
    assert result["overlay"] is None


# Input: tmp_path (Path), monkeypatch (pytest.MonkeyPatch)
# Output: None
# Processing: A reply the model wrapped in ```json fences is still parsed.
# Changed by: Claude Code
# Date: 2026-11-25
def test_overlay_propose_handles_code_fenced_reply(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    ws = project(tmp_path, FILES)
    ok(ws, "/project import", confirm=True)
    charge = next(b for b in ws.main.board.blocks.values() if b.path == "src/billing/charge.py")
    inner = json.dumps({"name": "ok", "label": "OK", "channel": "chip", "style": {"color": "blue"},
                        "reasoning": "fine",
                        "items": {charge.id: [{"message": "has code"}]}})
    stub_llm(monkeypatch, f"```json\n{inner}\n```")
    result = ok(ws, '/overlay propose "wrapped"')
    assert result["overlay"] is not None
