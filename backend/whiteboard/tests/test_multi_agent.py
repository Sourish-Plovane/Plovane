"""Several AI agents at once against a real threaded server: nothing lost, nothing misattributed."""

from __future__ import annotations

import json
import random
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from backend.whiteboard import llm_agent, tasks
from whiteboard_helpers import project

AGENTS, ROUNDS = 6, 2
PERSON = "person-token-for-test"


# Input: tmp_path (Path), monkeypatch (pytest.MonkeyPatch)
# Output: None
# Processing: Six agents ask the whiteboard AI, report tasks, read the board and try direct changes in parallel; every ask lands and is credited to its agent, every direct change is refused, every task ends with its owner, and events stay in order.
# Changed by: Claude Code
# Date: 2026-11-26
def test_many_agents_at_once(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    pytest.importorskip("flask")
    from werkzeug.serving import make_server

    from backend.whiteboard.server import create_app

    # Input: task (str), client (Any), **kwargs (Any)
    # Output: dict
    # Processing: A stand-in whiteboard AI that takes a moment, then adds the block the agent asked for.
    # Changed by: Claude Code
    # Date: 2026-11-26
    def fake_run(task, *, client, **kwargs):
        time.sleep(random.uniform(0.01, 0.1))
        reply = client.run_command(f"/block add service {task.split('ADD:')[-1].strip()}")
        return {"status": "done" if reply["ok"] else "error", "summary": "", "commands": [{"ok": reply["ok"]}]}

    monkeypatch.setattr(llm_agent, "run", fake_run)
    monkeypatch.setattr(llm_agent, "api_key", lambda *a, **k: "fake")
    ws = project(tmp_path, {"src/__init__.py": "", "src/app.py": "def run():\n    return 1\n"})
    assert ws.command("/project import", confirm=True)["ok"]
    app = create_app(ws, person_token=PERSON, lock=threading.RLock())
    server = make_server("127.0.0.1", 0, app, threaded=True)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{server.server_port}/api/v1/whiteboard"

    # Input: path (str), token (str), body (dict | None)
    # Output: tuple[int, dict]
    # Processing: One HTTP call as the given token; the status and JSON reply.
    # Changed by: Claude Code
    # Date: 2026-11-26
    def call(path: str, token: str, body: dict | None = None) -> tuple[int, dict]:
        request = urllib.request.Request(f"{url}/{path}", data=json.dumps(body).encode() if body is not None else None,
                                         headers={"content-type": "application/json", "authorization": f"Bearer {token}"})
        try:
            with urllib.request.urlopen(request, timeout=30) as reply:
                return reply.status, json.loads(reply.read())
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read() or b"{}")

    names = [f"agent{i}" for i in range(AGENTS)]
    tokens = {n: call(f"agents/{n}/token", PERSON, {"access": "read-write", "path": "."})[1]["data"]["token"] for n in names}
    start = app.config["plovane_bus"].seq
    problems: list[str] = []

    # Input: name (str)
    # Output: None
    # Processing: One agent's rounds of work; anything unexpected is recorded.
    # Changed by: Claude Code
    # Date: 2026-11-26
    def agent(name: str) -> None:
        token = tokens[name]
        for r in range(ROUNDS):
            status, started = call("command", token, {"command": f'/task start "{name} job {r}" --estimate 30s'})
            if status != 200 or not started["ok"]:
                problems.append(f"{name} start {status}")
                continue
            task_id = started["data"]["result"]["task"]["id"]
            status, asked = call("ask", token, {"message": f"ADD: svc-{name}-{r}"})
            if status != 200 or asked["data"]["requester"] != name or asked["data"]["status"] != "done":
                problems.append(f"{name} ask {status} {asked}")
            if call("command", token, {"command": f"/task update {task_id} --progress 50%"})[0] != 200:
                problems.append(f"{name} update")
            if call("board", token)[0] != 200:
                problems.append(f"{name} read")
            if call("op", token, {"op": "block.add", "args": {"kind": "service", "name": f"direct-{name}"}})[0] != 403:
                problems.append(f"{name} direct change allowed")
            status, done = call("command", token, {"command": f"/task done {task_id}"})
            if status != 200 or done["data"]["result"]["task"]["by"] != name:
                problems.append(f"{name} done {status}")

    threads = [threading.Thread(target=agent, args=(n,)) for n in names]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    events = app.config["plovane_bus"].since(start)
    server.shutdown()

    assert problems == []
    names_on_board = {b.name for b in ws.board.blocks.values()}
    assert {f"svc-{n}-{r}" for n in names for r in range(ROUNDS)} <= names_on_board
    assert not any(b.startswith("direct-") for b in names_on_board)
    listing = tasks.TaskBoard(ws.root).listing()
    assert len(listing) == AGENTS * ROUNDS and all(t["status"] == "done" and t["name"].startswith(t["by"] + " ") for t in listing)
    seqs = [e["seq"] for e in events]
    assert seqs == sorted(set(seqs)) and sum(e["kind"] == "task" for e in events) == AGENTS * ROUNDS * 3
