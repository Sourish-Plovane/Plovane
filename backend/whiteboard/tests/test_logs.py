"""Tests for /logs scan and the log_errors overlay."""

from __future__ import annotations

import json
from pathlib import Path

from whiteboard_helpers import ok, project

CHARGE_PY = '''def charge(amount):
    raise ValueError("bad amount")
'''

LOG = '''2026-11-24 10:01:03 INFO starting up
2026-11-24 10:01:04 ERROR request failed
Traceback (most recent call last):
  File "src/billing/charge.py", line 2, in charge
    raise ValueError("bad amount")
ValueError: bad amount
2026-11-24 10:02:11 ERROR charge.py crashed again at line 2
2026-11-24 10:05:00 ERROR something unrelated
  at web/client.js:12:4
'''

FILES = {
    "src/__init__.py": "",
    "src/billing/__init__.py": "",
    "src/billing/charge.py": CHARGE_PY,
    "web/client.js": "console.log('ok')\n",
    "server.log": LOG,
}


# Input: tmp_path (Path)
# Output: None
# Processing: /logs scan reads a log file, maps errors to project files, and saves counts to .plovane/log_errors.json.
# Changed by: Claude Code
# Date: 2026-11-24
def test_logs_scan_groups_errors_by_project_file(tmp_path: Path) -> None:
    ws = project(tmp_path, FILES)
    ok(ws, "/project import", confirm=True)
    result = ok(ws, "/logs scan server.log")

    assert result["source"] == "server.log"
    assert result["total_errors"] >= 3
    assert "src/billing/charge.py" in result["files"]
    assert "web/client.js" in result["files"]
    saved = json.loads((tmp_path / ".plovane" / "log_errors.json").read_text())
    # charge.py: one traceback line + one bare "charge.py crashed again at line 2" ⇒ two hits, both for line 2.
    assert saved["files"]["src/billing/charge.py"]["count"] >= 2
    assert saved["files"]["src/billing/charge.py"]["lines"]["2"] >= 1


# Input: tmp_path (Path)
# Output: None
# Processing: The log_errors overlay reads the saved file and places a count on the file's block.
# Changed by: Claude Code
# Date: 2026-11-24
def test_log_errors_overlay_badges_blocks(tmp_path: Path) -> None:
    ws = project(tmp_path, FILES)
    ok(ws, "/project import", confirm=True)
    ok(ws, "/logs scan server.log")
    ok(ws, "/overlay on log_errors")
    from backend.whiteboard.overlays import overlay_state
    state = overlay_state(ws.main, ws.main.board, "user")
    results = state["results"]["log_errors"]
    charge = next(b for b in ws.main.board.blocks.values() if b.path == "src/billing/charge.py")
    assert charge.id in results
    assert results[charge.id]["own"] is True
    assert results[charge.id]["items"][0]["count"] >= 2


# Input: tmp_path (Path)
# Output: None
# Processing: /logs show returns the saved record.
# Changed by: Claude Code
# Date: 2026-11-24
def test_logs_show_returns_saved_record(tmp_path: Path) -> None:
    ws = project(tmp_path, FILES)
    ok(ws, "/project import", confirm=True)
    ok(ws, "/logs scan server.log")
    saved = ok(ws, "/logs show")
    assert saved["source"] == "server.log"
    assert "src/billing/charge.py" in saved["files"]


# Input: tmp_path (Path)
# Output: None
# Processing: /logs show is empty before any scan.
# Changed by: Claude Code
# Date: 2026-11-24
def test_logs_show_is_empty_before_any_scan(tmp_path: Path) -> None:
    ws = project(tmp_path, FILES)
    empty = ok(ws, "/logs show")
    assert empty["files"] == {}
