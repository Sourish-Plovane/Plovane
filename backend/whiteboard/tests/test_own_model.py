"""The project's own trained model: it draws the whiteboard, and nothing goes to an outside AI."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from backend.whiteboard import board_ai, llm_agent
from whiteboard_helpers import project


# Input: tmp_path (Path), monkeypatch (pytest.MonkeyPatch)
# Output: None
# Processing: With the own model served, a build draws with it (repeated lines dropped), never runs the checking loop, and agent runs refuse it.
# Changed by: Claude Code
# Date: 2026-11-27
def test_build_draws_with_the_own_model_only(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    served = tmp_path / "model.json"
    served.write_text(json.dumps({"url": "http://1.2.3.4:8000/v1", "key": "k", "model": "plovane-whiteboard", "instance": "7"}))
    monkeypatch.setattr(llm_agent, "MODEL_PATH", served)
    for name in ("ANTHROPIC_API_KEY", "DEEPSEEK_API_KEY", "OPENAI_API_KEY", "PLOVANE_BOARD_AI_PROVIDER"):
        monkeypatch.delenv(name, raising=False)
    calls: list[tuple[str, str]] = []

    # Input: provider (str), model (str), system (str), prompt (str), **_ (Any)
    # Output: tuple[str, dict]
    # Processing: The served model's answer, repeating one line the way a small model can.
    # Changed by: Claude Code
    # Date: 2026-11-27
    def fake_complete(provider, model, system, prompt, **_):
        calls.append((provider, model))
        return ('service "API"\ndatabase "Postgres"\n"API" -> "Postgres" "stores data"\n"API" -> "Postgres" "stores data"\n',
                {"input_tokens": 1, "output_tokens": 1})

    real_run = llm_agent.run
    monkeypatch.setattr(llm_agent, "complete", fake_complete)
    monkeypatch.setattr(llm_agent, "run", lambda *a, **k: pytest.fail("the checking loop must not run for the own model"))
    ws = project(tmp_path / "p", {"src/app.py": "def run():\n    return 1\n", "README.md": "# Demo\nA demo app.\n"})
    status = llm_agent.own_status()
    assert status["running"] and status["base_url"] == "http://1.2.3.4:8000/v1" and "key" not in status
    result = board_ai.build(ws)
    assert calls == [("plovane", "plovane-whiteboard")]
    assert result["draw"]["lines"] == 3 and result["draw"]["applied"] == 3 and "checked" in result
    assert {b.name for b in ws.board.blocks.values() if b.kind in ("service", "database")} == {"API", "Postgres"}
    with pytest.raises(llm_agent.AgentError, match="draws diagrams"):
        real_run("x", provider="plovane", client=None)
