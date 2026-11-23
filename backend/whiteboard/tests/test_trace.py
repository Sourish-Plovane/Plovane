"""Tests for /trace run: run a pytest file under coverage.py and record what ran."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from whiteboard_helpers import ok, project

coverage = pytest.importorskip("coverage")


CHARGE_PY = '''def charge(amount):
    if amount <= 0:
        return {"error": "bad amount"}
    tax = round(amount * 0.08, 2)
    return {"total": amount + tax, "tax": tax}
'''

TEST_CHARGE = '''import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from src.billing.charge import charge


def test_positive():
    assert charge(10)["total"] == 10.8
'''

FILES = {
    "src/__init__.py": "",
    "src/billing/__init__.py": "",
    "src/billing/charge.py": CHARGE_PY,
    "tests/__init__.py": "",
    "tests/test_charge.py": TEST_CHARGE,
}


# Input: tmp_path (Path)
# Output: None
# Processing: /trace run on a passing test records which lines of charge.py ran and which didn't; the saved file is reloadable.
# Changed by: Claude Code
# Date: 2026-11-23
def test_trace_run_records_ran_and_missed_lines(tmp_path: Path) -> None:
    ws = project(tmp_path, FILES)
    result = ok(ws, "/trace run tests/test_charge.py")
    assert result["exit_code"] == 0
    assert result["counts"]["ran"] > 0
    assert result["counts"]["missed"] > 0
    assert "src/billing/charge.py" in result["files"]

    trace = json.loads((tmp_path / ".plovane" / "trace.json").read_text())
    rows = trace["files"]["src/billing/charge.py"]
    # def (1), the if (2), tax (4) and the happy return (5) ran; the bad-amount return (3) was missed.
    assert set(rows["ran"]) == {1, 2, 4, 5}
    assert rows["missed"] == [3]


# Input: tmp_path (Path)
# Output: None
# Processing: /trace show returns the saved trace so the panel can colour lines without a new run.
# Changed by: Claude Code
# Date: 2026-11-23
def test_trace_show_returns_the_saved_record(tmp_path: Path) -> None:
    ws = project(tmp_path, FILES)
    ok(ws, "/trace run tests/test_charge.py")
    saved = ok(ws, "/trace show")
    assert saved["target"] == "tests/test_charge.py"
    assert "src/billing/charge.py" in saved["files"]


# Input: tmp_path (Path)
# Output: None
# Processing: /trace run on a file outside the project is refused by name, not by letting coverage run first.
# Changed by: Claude Code
# Date: 2026-11-23
def test_trace_refuses_a_path_outside_the_project(tmp_path: Path) -> None:
    ws = project(tmp_path, FILES)
    reply = ws.command("/trace run ../other.py", actor="user")
    assert not reply["ok"]
    assert reply["error"]["code"] == "INVALID_ARGS"


# Input: tmp_path (Path)
# Output: None
# Processing: /trace show returns an empty record when no trace has been saved.
# Changed by: Claude Code
# Date: 2026-11-23
def test_trace_show_is_empty_before_any_run(tmp_path: Path) -> None:
    ws = project(tmp_path, FILES)
    saved = ok(ws, "/trace show")
    assert saved["files"] == {}
    assert saved["target"] is None
