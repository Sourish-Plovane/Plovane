"""One whiteboard AI job at a time per project, first come first served.

Several agents may ask the whiteboard AI at once. Each command it runs is
atomic under the board lock, but two jobs running side by side interleave:
one reads the board, the other changes it, and the first acts on a stale
picture. So jobs take turns. A job that has to wait shows on the temporary
display (tasks.py) as "waiting: N ahead" and becomes "working" when its turn
comes; the person and every agent can see the line.

A job already holding the turn may start nested work (a build runs an ask to
check its drawing) without queueing behind itself.

This covers one server process. A separate process (the MCP server in local
mode) has its own line.
"""

from __future__ import annotations

import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable, Iterator

MAX_WAIT_SECONDS = 20 * 60


class Busy(Exception):
    """The whiteboard AI stayed busy longer than a caller is willing to wait."""


class AIQueue:
    """A first-in, first-out line of jobs for one project's whiteboard AI."""

    # Input: none
    # Output: None
    # Processing: Start with an empty line and nobody holding the turn.
    # Changed by: Claude Code
    # Date: 2026-11-27
    def __init__(self) -> None:
        self._cond = threading.Condition()
        self._line: list[dict[str, Any]] = []
        self._holder: int | None = None

    # Input: none
    # Output: bool
    # Processing: Whether the calling thread holds the turn (so nested work must not queue).
    # Changed by: Claude Code
    # Date: 2026-11-27
    def mine(self) -> bool:
        with self._cond:
            return self._holder == threading.get_ident()

    # Input: none
    # Output: list[str]
    # Processing: Who is in line, the one working first.
    # Changed by: Claude Code
    # Date: 2026-11-27
    def waiting(self) -> list[str]:
        with self._cond:
            return [entry["who"] for entry in self._line]

    # Input: who (str), on_wait (Callable[[int], Any] | None), max_wait (float)
    # Output: Iterator[None]
    # Processing: Wait in line until first, calling on_wait(jobs ahead) whenever that number changes; hold the turn for the block; give it up afterwards. Raises Busy after max_wait.
    # Changed by: Claude Code
    # Date: 2026-11-27
    @contextmanager
    def turn(self, who: str, on_wait: Callable[[int], Any] | None = None,
             max_wait: float = MAX_WAIT_SECONDS) -> Iterator[None]:
        me = threading.get_ident()
        if self.mine():
            yield
            return
        entry = {"who": who}
        reported = -1
        deadline = time.monotonic() + max_wait
        with self._cond:
            self._line.append(entry)
        try:
            while True:
                with self._cond:
                    ahead = self._line.index(entry)
                    if ahead == 0:
                        self._holder = me
                        break
                if ahead != reported and on_wait is not None:
                    reported = ahead
                    on_wait(ahead)  # outside the queue's lock: it writes the task file
                with self._cond:
                    if self._line.index(entry) == ahead:
                        left = deadline - time.monotonic()
                        if left <= 0:
                            raise Busy(f"the whiteboard AI is still busy with {ahead} job(s) ahead; try again later")
                        self._cond.wait(min(5.0, left))
        except BaseException:
            with self._cond:
                if entry in self._line:
                    self._line.remove(entry)
                self._cond.notify_all()
            raise
        try:
            yield
        finally:
            with self._cond:
                self._line.remove(entry)
                self._holder = None
                self._cond.notify_all()


_QUEUES: dict[str, AIQueue] = {}
_QUEUES_LOCK = threading.Lock()


# Input: root (Path | str)
# Output: AIQueue
# Processing: The one line for a project folder, made on first use.
# Changed by: Claude Code
# Date: 2026-11-27
def queue_for(root: Path | str) -> AIQueue:
    key = str(Path(root).resolve())
    with _QUEUES_LOCK:
        if key not in _QUEUES:
            _QUEUES[key] = AIQueue()
        return _QUEUES[key]


# Input: ahead (int)
# Output: str
# Processing: The waiting message shown on a queued job's card.
# Changed by: Claude Code
# Date: 2026-11-27
def waiting_text(ahead: int) -> str:
    return f"waiting: {ahead} job{'s' if ahead != 1 else ''} ahead"
