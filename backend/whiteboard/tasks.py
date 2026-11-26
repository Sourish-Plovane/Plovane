"""Long-running tasks shown on the whiteboard's temporary display.

A task is anything that takes a while: a GPU training run, the whiteboard AI
building the board, a test suite, an import. Whoever runs it (a person, the
whiteboard AI, Claude Code over MCP, a script) starts it, sends updates and
marks it done. Plovane works out how long is left and the browser shows each
task in the Tasks area beside the project; an update can name the block it is
about, so the browser can take the person there.

Tasks are not part of the drawing: they live in ``.plovane/tasks.json``, never
in the board, so they change nothing the whiteboard AI owns and leave no undo
history. Finished tasks stay visible for a few minutes, then go; how long each
kind took is kept, so the next task of that kind gets an estimate on its own.
"""

from __future__ import annotations

import json
import re
import statistics
import threading
import time
import urllib.request
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

FILE = "tasks.json"
KEEP_FINISHED_SECONDS = 600
"""How long a finished task stays on the display."""
KEEP_DURATIONS = 20
"""Past durations kept per kind of task, for estimates."""
KEEP_UPDATES = 20
"""Updates kept per task."""

LISTENERS: list[Callable[[dict[str, Any], Path], Any]] = []
"""Called with every task event and the task file it came from; the server adds its live event bus."""

_LOCKS: dict[Path, threading.Lock] = {}
_LOCKS_GUARD = threading.Lock()


class TaskError(ValueError):
    """A task request that cannot be carried out (unknown task, bad duration...)."""


# Input: text (Any)
# Output: float | None
# Processing: Seconds from "45m", "1h30m", "90s", "2h" or a plain number of seconds; None for nothing.
# Changed by: Claude Code
# Date: 2026-11-26
def seconds(text: Any) -> float | None:
    if text is None or text == "":
        return None
    if isinstance(text, (int, float)):
        return float(text)
    raw = str(text).strip().lower().replace(" ", "")
    if re.fullmatch(r"\d+(\.\d+)?", raw):
        return float(raw)
    parts = re.findall(r"(\d+(?:\.\d+)?)(h|m|s)", raw)
    if not parts or "".join(n + u for n, u in parts) != raw:
        raise TaskError(f"cannot read {text!r} as a duration; use e.g. 45m, 1h30m or 90s")
    return sum(float(n) * {"h": 3600, "m": 60, "s": 1}[u] for n, u in parts)


# Input: value (Any)
# Output: float | None
# Processing: Progress as a fraction 0..1 from "40%", "0.4", 40 or None.
# Changed by: Claude Code
# Date: 2026-11-26
def fraction(value: Any) -> float | None:
    if value is None or value == "":
        return None
    raw = str(value).strip()
    try:
        number = float(raw.rstrip("%"))
    except ValueError:
        raise TaskError(f"cannot read {value!r} as progress; use e.g. 40% or 0.4") from None
    if raw.endswith("%") or number > 1:
        number /= 100
    return min(max(number, 0.0), 1.0)


# Input: total (float | None)
# Output: str
# Processing: A short human duration: "45 s", "12 min", "1 h 20 min".
# Changed by: Claude Code
# Date: 2026-11-26
def human(total: float | None) -> str:
    if total is None:
        return "unknown"
    total = int(round(total))
    if total < 60:
        return f"{total} s"
    if total < 3600:
        return f"{round(total / 60)} min"
    hours, rest = divmod(total, 3600)
    return f"{hours} h {round(rest / 60)} min" if rest >= 60 else f"{hours} h"


# Input: when (float)
# Output: str
# Processing: An epoch time as ISO 8601 UTC.
# Changed by: Claude Code
# Date: 2026-11-26
def iso(when: float) -> str:
    return datetime.fromtimestamp(when, timezone.utc).isoformat(timespec="seconds")


class TaskBoard:
    """The tasks of one project, kept in its ``.plovane/tasks.json``."""

    # Input: root (Path | str), clock (Callable[[], float])
    # Output: None
    # Processing: Bind to a project's task file; the clock is swappable for tests.
    # Changed by: Claude Code
    # Date: 2026-11-26
    def __init__(self, root: Path | str, clock: Callable[[], float] = time.time) -> None:
        self.path = Path(root).resolve() / ".plovane" / FILE
        self.clock = clock
        with _LOCKS_GUARD:
            self.lock = _LOCKS.setdefault(self.path, threading.Lock())

    # Input: none
    # Output: dict[str, Any]
    # Processing: The stored tasks and past durations, empty when there is no file yet.
    # Changed by: Claude Code
    # Date: 2026-11-26
    def load(self) -> dict[str, Any]:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = {}
        return {"tasks": data.get("tasks") or {}, "durations": data.get("durations") or {}}

    # Input: data (dict[str, Any])
    # Output: None
    # Processing: Write the tasks back, dropping finished ones past their time on the display.
    # Changed by: Claude Code
    # Date: 2026-11-26
    def save(self, data: dict[str, Any]) -> None:
        now = self.clock()
        data["tasks"] = {i: t for i, t in data["tasks"].items()
                         if t["status"] == "running" or now - (t.get("finished") or now) < KEEP_FINISHED_SECONDS}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp = self.path.with_suffix(".tmp")
        temp.write_text(json.dumps(data, indent=1) + "\n", encoding="utf-8")
        temp.replace(self.path)

    # Input: data (dict[str, Any]), kind (str | None)
    # Output: float | None
    # Processing: The median time past tasks of this kind took, or None without at least one.
    # Changed by: Claude Code
    # Date: 2026-11-26
    @staticmethod
    def past_estimate(data: dict[str, Any], kind: str | None) -> float | None:
        past = data["durations"].get(kind or "") or []
        return float(statistics.median(past)) if past else None

    # Input: data (dict[str, Any]), ref (str)
    # Output: dict[str, Any]
    # Processing: A task by id, or by name (the newest running one first); TaskError when none matches.
    # Changed by: Claude Code
    # Date: 2026-11-26
    @staticmethod
    def find(data: dict[str, Any], ref: str) -> dict[str, Any]:
        ref = str(ref or "").strip()
        if ref in data["tasks"]:
            return data["tasks"][ref]
        named = [t for t in data["tasks"].values() if t["name"].lower() == ref.lower()]
        named.sort(key=lambda t: (t["status"] == "running", t["started"]), reverse=True)
        if not named:
            known = ", ".join(f"{t['id']} ({t['name']})" for t in data["tasks"].values()) or "none"
            raise TaskError(f"no task {ref!r}; tasks: {known}")
        return named[0]

    # Input: task (dict[str, Any])
    # Output: dict[str, Any]
    # Processing: A task as shown: elapsed, time left (from progress, the estimate, or both), ETA and how it was worked out.
    # Changed by: Claude Code
    # Date: 2026-11-26
    def view(self, task: dict[str, Any]) -> dict[str, Any]:
        now = self.clock()
        end = task.get("finished") or now
        elapsed = max(end - task["started"], 0.0)
        progress, estimate = task.get("progress"), task.get("estimate")
        left, basis = None, None
        if task["status"] != "running":
            left, basis = 0.0, "finished"
        else:
            from_progress = elapsed * (1 - progress) / progress if progress and progress >= 0.05 else None
            from_estimate = max(estimate - elapsed, 0.0) if estimate is not None else None
            if from_progress is not None and from_estimate is not None:
                # Trust progress more the further along it is.
                left, basis = progress * from_progress + (1 - progress) * from_estimate, "progress and estimate"
            elif from_progress is not None:
                left, basis = from_progress, "progress so far"
            elif from_estimate is not None and estimate is not None:
                left, basis = from_estimate, task.get("estimate_from") or "estimate"
        overdue = task["status"] == "running" and bool(estimate) and elapsed > estimate and not progress
        shown = {k: v for k, v in task.items() if k != "updates"}
        shown.update({
            "elapsed": round(elapsed), "elapsed_text": human(elapsed),
            "left": None if left is None or overdue else round(left),
            "left_text": "overdue" if overdue else (task["status"] if task["status"] != "running" else human(left)),
            "eta": iso(now + left) if left is not None and task["status"] == "running" and not overdue else None,
            "basis": basis, "overdue": overdue, "updates": task.get("updates", [])[-5:],
        })
        return shown

    # Input: event_type (str), task (dict[str, Any]), by (str), go_to (str | None)
    # Output: dict[str, Any]
    # Processing: Tell every listener about a task change; a listener failing never fails the change.
    # Changed by: Claude Code
    # Date: 2026-11-26
    def emit(self, event_type: str, task: dict[str, Any], by: str, go_to: str | None) -> dict[str, Any]:
        event = {"at": iso(self.clock()), "kind": "task", "type": event_type, "actor": by,
                 "task": self.view(task), "go_to": go_to}
        for listener in list(LISTENERS):
            try:
                listener(event, self.path)
            except Exception:  # noqa: BLE001  (a closed browser stream must not stop the task)
                pass
        return event

    # Input: name (str), by (str), kind (str | None), estimate (Any), at (str | None), message (str | None)
    # Output: dict[str, Any]
    # Processing: Start a task; without an estimate, use how long past tasks of the same kind took.
    # Changed by: Claude Code
    # Date: 2026-11-26
    def start(self, name: str, *, by: str, kind: str | None = None, estimate: Any = None,
              at: str | None = None, message: str | None = None) -> dict[str, Any]:
        name = str(name or "").strip()
        if not name:
            raise TaskError("a task needs a name")
        given = seconds(estimate)
        with self.lock:
            data = self.load()
            kind = (kind or re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-"))[:60]
            past = self.past_estimate(data, kind)
            task = {"id": uuid.uuid4().hex[:8], "name": name, "kind": kind, "by": by, "at": at,
                    "started": self.clock(), "estimate": given if given is not None else past,
                    "estimate_from": "given" if given is not None else ("past runs" if past is not None else None),
                    "progress": None, "message": message or "", "status": "running", "finished": None,
                    "updates": [{"t": iso(self.clock()), "message": message or "started", "at": at}]}
            data["tasks"][task["id"]] = task
            self.save(data)
        self.emit("started", task, by, at)
        return self.view(task)

    # Input: ref (str), by (str), progress (Any), message (str | None), at (str | None), estimate (Any)
    # Output: dict[str, Any]
    # Processing: Record progress, a message, a new estimate or the block the task is now about.
    # Changed by: Claude Code
    # Date: 2026-11-26
    def update(self, ref: str, *, by: str, progress: Any = None, message: str | None = None,
               at: str | None = None, estimate: Any = None) -> dict[str, Any]:
        share, given = fraction(progress), seconds(estimate)
        with self.lock:
            data = self.load()
            task = self.find(data, ref)
            if task["status"] != "running":
                raise TaskError(f"task {task['id']} ({task['name']}) already {task['status']}")
            if share is not None:
                task["progress"] = share
            if given is not None:
                task["estimate"], task["estimate_from"] = given, "given"
            if at:
                task["at"] = at
            if message:
                task["message"] = message
            task["updates"] = (task.get("updates", []) + [{"t": iso(self.clock()), "message": message or (
                f"{round(share * 100)}%" if share is not None else "updated"), "at": at}])[-KEEP_UPDATES:]
            self.save(data)
        self.emit("update", task, by, at)
        return self.view(task)

    # Input: ref (str), by (str), failed (bool), message (str | None)
    # Output: dict[str, Any]
    # Processing: Mark a task done or failed; a success adds its time to that kind's past durations.
    # Changed by: Claude Code
    # Date: 2026-11-26
    def finish(self, ref: str, *, by: str, failed: bool = False, message: str | None = None) -> dict[str, Any]:
        with self.lock:
            data = self.load()
            task = self.find(data, ref)
            if task["status"] != "running":
                raise TaskError(f"task {task['id']} ({task['name']}) already {task['status']}")
            task["status"], task["finished"] = ("failed" if failed else "done"), self.clock()
            if not failed:
                task["progress"] = 1.0
                past = data["durations"].setdefault(task["kind"], [])
                past.append(round(task["finished"] - task["started"], 1))
                del past[:-KEEP_DURATIONS]
            if message:
                task["message"] = message
            task["updates"] = (task.get("updates", []) + [{"t": iso(self.clock()), "message": message or task["status"],
                                                           "at": task.get("at")}])[-KEEP_UPDATES:]
            self.save(data)
        self.emit("finished", task, by, task.get("at"))
        return self.view(task)

    # Input: none
    # Output: list[dict[str, Any]]
    # Processing: Every task on the display, running ones first, newest first.
    # Changed by: Claude Code
    # Date: 2026-11-26
    def listing(self) -> list[dict[str, Any]]:
        with self.lock:
            data = self.load()
            self.save(data)
        tasks = sorted(data["tasks"].values(), key=lambda t: (t["status"] != "running", -t["started"]))
        return [self.view(t) for t in tasks]


# Input: root (Path | str), command (str)
# Output: dict[str, Any] | None
# Processing: Send a /task command to the project's running whiteboard server so browsers see it live; None when no server is running.
# Changed by: Claude Code
# Date: 2026-11-26
def report(root: Path | str, command: str) -> dict[str, Any] | None:
    """For scripts outside the server (a GPU runner, a test harness). Never raises."""
    try:
        info = json.loads((Path(root) / ".plovane" / "server.json").read_text(encoding="utf-8"))
        request = urllib.request.Request(
            f"{info['url']}/api/v1/whiteboard/command", data=json.dumps({"command": command}).encode(),
            headers={"content-type": "application/json", "authorization": f"Bearer {info.get('token', '')}"})
        return json.loads(urllib.request.urlopen(request, timeout=3).read())
    except Exception:  # noqa: BLE001  (the whiteboard being closed must never stop the script)
        return None
