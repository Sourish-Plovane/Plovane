"""Tests for the temporary display: long tasks, their time left, and live task events."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from backend.whiteboard import tasks
from backend.whiteboard.tasks import TaskBoard, TaskError, fraction, human, seconds
from whiteboard_helpers import project

FILES = {"ml/__init__.py": "", "ml/train.py": "def train():\n    return 1\n"}


class Clock:
    """A clock the test moves by hand."""

    # Input: start (float)
    # Output: None
    # Processing: Start the clock at a fixed time.
    # Changed by: Claude Code
    # Date: 2026-11-26
    def __init__(self, start: float = 1_800_000_000.0) -> None:
        self.now = start

    # Input: none
    # Output: float
    # Processing: The current fake time.
    # Changed by: Claude Code
    # Date: 2026-11-26
    def __call__(self) -> float:
        return self.now


# Input: none
# Output: None
# Processing: Durations and progress are read the ways people write them.
# Changed by: Claude Code
# Date: 2026-11-26
def test_reads_durations_and_progress() -> None:
    assert seconds("45m") == 2700 and seconds("1h30m") == 5400 and seconds("90") == 90 and seconds(None) is None
    assert fraction("40%") == 0.4 and fraction("0.4") == 0.4 and fraction(40) == 0.4 and fraction("150%") == 1.0
    assert human(45) == "45 s" and human(720) == "12 min" and human(4800) == "1 h 20 min"
    with pytest.raises(TaskError):
        seconds("soon")
    with pytest.raises(TaskError):
        fraction("half")


# Input: tmp_path (Path)
# Output: None
# Processing: Time left counts down from the estimate, then leans on progress as it grows; overdue tasks say so.
# Changed by: Claude Code
# Date: 2026-11-26
def test_time_left_from_estimate_and_progress(tmp_path: Path) -> None:
    clock = Clock()
    board = TaskBoard(tmp_path, clock=clock)
    task = board.start("Train", by="user", estimate="40m")
    assert task["left"] == 2400 and task["basis"] == "given"
    clock.now += 600
    assert board.listing()[0]["left"] == 1800
    clock.now += 600  # 20 min in, half done: progress and estimate agree on 20 min
    updated = board.update(task["id"], by="user", progress="50%", message="epoch 2 of 3")
    assert updated["left"] == 1200 and updated["basis"] == "progress and estimate" and updated["message"] == "epoch 2 of 3"
    slow = board.start("Slow", by="user", estimate="1m")
    clock.now += 120
    late = next(t for t in board.listing() if t["id"] == slow["id"])
    assert late["overdue"] and late["left"] is None and late["left_text"] == "overdue"


# Input: tmp_path (Path)
# Output: None
# Processing: A finished task teaches the next one of its kind an estimate; failures teach nothing; finished tasks leave after a while.
# Changed by: Claude Code
# Date: 2026-11-26
def test_past_runs_estimate_and_finished_tasks_expire(tmp_path: Path) -> None:
    clock = Clock()
    board = TaskBoard(tmp_path, clock=clock)
    first = board.start("Build board", by="plovane", kind="board-build")
    assert first["left"] is None and first["estimate_from"] is None
    clock.now += 90
    assert board.finish(first["id"], by="plovane")["left_text"] == "done"
    broken = board.start("Build board", by="plovane", kind="board-build")
    clock.now += 5
    assert board.finish(broken["id"], by="plovane", failed=True)["left_text"] == "failed"
    second = board.start("Build board", by="plovane", kind="board-build")
    assert second["estimate"] == 90 and second["estimate_from"] == "past runs"
    with pytest.raises(TaskError):
        board.finish(first["id"], by="plovane")
    clock.now += tasks.KEEP_FINISHED_SECONDS + 1
    assert [t["id"] for t in board.listing()] == [second["id"]]
    assert json.loads(board.path.read_text())["durations"] == {"board-build": [90.0]}


# Input: tmp_path (Path)
# Output: None
# Processing: The /task commands work for anyone, name a block to go to, and never touch the drawing or its undo history.
# Changed by: Claude Code
# Date: 2026-11-26
def test_task_commands_leave_the_board_alone(tmp_path: Path) -> None:
    ws = project(tmp_path, FILES)
    assert ws.command("/project import", confirm=True)["ok"]
    before = ws.board.to_dict()
    seen: list[dict] = []
    tasks.LISTENERS.append(lambda event, path: seen.append(event))
    try:
        started = ws.command('/task start "Train whiteboard model" --estimate 45m --at ml')
        assert started["ok"], started
        task = started["data"]["result"]["task"]
        assert task["at"] == "ml" and "45 min" in started["data"]["result"]["message"]
        assert ws.command('/task update "Train whiteboard model" --progress 40% --message "epoch 2"')["ok"]
        assert ws.command('/task done "Train whiteboard model" --failed')["data"]["result"]["task"]["status"] == "failed"
        missing = ws.command("/task start x --at nowhere")
        assert not missing["ok"] and "nowhere" in missing["error"]["message"]
    finally:
        tasks.LISTENERS.pop()
    assert [(e["type"], e["go_to"]) for e in seen] == [("started", "ml"), ("update", None), ("finished", "ml")]
    assert ws.board.to_dict() == before
    assert not ws.store.load_history()["undo"][-1]["op"].startswith("task.")


# Input: tmp_path (Path)
# Output: None
# Processing: An outside agent (ask-only for the drawing) may still report tasks, and browsers get them as live events.
# Changed by: Claude Code
# Date: 2026-11-26
def test_agents_report_tasks_and_events_reach_the_browser(tmp_path: Path) -> None:
    pytest.importorskip("flask")
    from backend.whiteboard.server import create_app

    ws = project(tmp_path, FILES)
    ws.command("/agent add cursor")
    client = create_app(ws).test_client()
    seq = client.get("/api/v1/whiteboard/events/recent?after=999999999").get_json()["data"]["seq"]
    reply = client.post("/api/v1/whiteboard/command", json={"command": '/task start "Run tests" --estimate 2m'},
                        headers={"X-Plovane-Agent": "cursor"}).get_json()
    assert reply["ok"], reply
    events = client.get(f"/api/v1/whiteboard/events/recent?after={seq}").get_json()["data"]["events"]
    task_events = [e for e in events if e["kind"] == "task"]
    assert len(task_events) == 1 and task_events[0]["task"]["name"] == "Run tests" and task_events[0]["actor"] == "cursor"


# Input: tmp_path (Path)
# Output: None
# Processing: report() is silent and returns None when no whiteboard server is running.
# Changed by: Claude Code
# Date: 2026-11-26
def test_report_without_a_server(tmp_path: Path) -> None:
    assert tasks.report(tmp_path, "/task list") is None
