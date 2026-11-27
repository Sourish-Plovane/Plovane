"""Whiteboard AI jobs take turns, first come first served, and show on the temporary display."""

from __future__ import annotations

import threading
import time
from pathlib import Path

import pytest

from backend.whiteboard import board_ai, llm_agent, tasks
from backend.whiteboard.ai_queue import AIQueue, Busy, queue_for, waiting_text
from whiteboard_helpers import project


# Input: none
# Output: None
# Processing: Jobs run one at a time in arrival order, each waiter hears how many are ahead as that shrinks, and nested work by the holder does not queue.
# Changed by: Claude Code
# Date: 2026-11-27
def test_one_at_a_time_in_order() -> None:
    queue = AIQueue()
    order: list[str] = []
    heard: dict[str, list[int]] = {}
    release = threading.Event()

    # Input: name (str)
    # Output: None
    # Processing: One job: wait its turn, note it ran, and hold the turn until released (the first one only).
    # Changed by: Claude Code
    # Date: 2026-11-27
    def job(name: str) -> None:
        with queue.turn(name, on_wait=lambda ahead: heard.setdefault(name, []).append(ahead)):
            order.append(name)
            with queue.turn(name):  # nested: passes straight through
                pass
            if name == "a":
                release.wait(5)

    threads = []
    for name in "abc":
        threads.append(threading.Thread(target=job, args=(name,)))
        threads[-1].start()
        deadline = time.time() + 5
        while len(queue.waiting()) < len(threads) and time.time() < deadline:
            time.sleep(0.01)
    assert queue.waiting() == ["a", "b", "c"]
    release.set()
    for thread in threads:
        thread.join(5)
    assert order == ["a", "b", "c"]
    assert heard["b"] == [1] and heard["c"][0] == 2 and heard["c"] == sorted(heard["c"], reverse=True)
    assert queue.waiting() == []
    assert waiting_text(1) == "waiting: 1 job ahead" and waiting_text(2) == "waiting: 2 jobs ahead"


# Input: none
# Output: None
# Processing: A waiter that gives up leaves the line, and the next job still runs.
# Changed by: Claude Code
# Date: 2026-11-27
def test_gives_up_after_max_wait() -> None:
    queue = AIQueue()
    holding = threading.Event()
    release = threading.Event()

    # Input: none
    # Output: None
    # Processing: Hold the turn until released.
    # Changed by: Claude Code
    # Date: 2026-11-27
    def hold() -> None:
        with queue.turn("a"):
            holding.set()
            release.wait(5)

    thread = threading.Thread(target=hold)
    thread.start()
    holding.wait(5)
    with pytest.raises(Busy):
        with queue.turn("b", max_wait=0.1):
            pass
    assert queue.waiting() == ["a"]
    release.set()
    thread.join(5)
    with queue.turn("c", max_wait=1):
        assert queue.waiting() == ["c"]


# Input: tmp_path (Path), monkeypatch (pytest.MonkeyPatch)
# Output: None
# Processing: Two asks at once: they never overlap, the second shows "waiting: 1 job ahead" while the first works, and both cards end done with the asker as owner.
# Changed by: Claude Code
# Date: 2026-11-27
def test_asks_wait_their_turn_on_the_display(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    ws = project(tmp_path, {"src/app.py": "def run():\n    return 1\n"})
    started = threading.Event()
    release = threading.Event()
    running = [0, 0]  # now, most

    # Input: task (str), client (Any), **kwargs (Any)
    # Output: dict
    # Processing: A stand-in whiteboard AI; the first job holds on until released.
    # Changed by: Claude Code
    # Date: 2026-11-27
    def fake_run(task, *, client, **kwargs):
        running[0] += 1
        running[1] = max(running)
        started.set()
        if "first" in task:
            release.wait(5)
        running[0] -= 1
        return {"status": "done", "summary": f"did {task.splitlines()[-1]}", "commands": []}

    monkeypatch.setattr(llm_agent, "run", fake_run)
    monkeypatch.setattr(llm_agent, "api_key", lambda *a, **k: "fake")
    replies: dict[str, dict] = {}
    first = threading.Thread(target=lambda: replies.update(a=board_ai.ask(ws, "first job", requester="alpha")))
    first.start()
    started.wait(5)
    second = threading.Thread(target=lambda: replies.update(b=board_ai.ask(ws, "second job", requester="beta")))
    second.start()
    deadline = time.time() + 5
    while len(queue_for(ws.root).waiting()) < 2 and time.time() < deadline:
        time.sleep(0.01)
    time.sleep(0.1)
    cards = {t["by"]: t for t in tasks.TaskBoard(ws.root).listing()}
    assert cards["alpha"]["message"] == "working"
    assert cards["beta"]["message"] == "waiting: 1 job ahead"
    assert cards["beta"]["name"] == "Whiteboard AI: second job" and cards["beta"]["kind"] == "board-ai"
    assert cards["beta"]["waiting"] is True and cards["beta"]["left_text"] == "in line"
    time.sleep(1.2)  # time in line must not count toward beta's duration
    release.set()
    first.join(5)
    second.join(5)
    assert running[1] == 1
    done = {t["by"]: t for t in tasks.TaskBoard(ws.root).listing()}
    assert done["alpha"]["status"] == done["beta"]["status"] == "done"
    assert done["beta"]["message"] == "did second job"
    assert "waiting" not in done["beta"] and done["beta"]["queued"] < done["beta"]["started"]
    assert done["beta"]["finished"] - done["beta"]["started"] < 1.0 <= done["alpha"]["finished"] - done["alpha"]["started"]
    assert replies["a"]["task"] == cards["alpha"]["id"]
