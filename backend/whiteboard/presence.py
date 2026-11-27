"""Which outside agents are working on the board right now.

An agent that works through its API token (directly or through the MCP server
with PLOVANE_URL set) never sets ``connection`` on its board entry, so the page
used to call it "Not connected" while it was busy. The server now notes every
request an agent makes, and every event it causes, here. Nothing is written to
the board: presence is live state and must not land in undo history.

An agent counts as online while a request of its is in flight (an ask can take
a minute) or for ``ACTIVE_SECONDS`` after its last one.
"""

from __future__ import annotations

import threading
import time
from typing import Any, Callable

ACTIVE_SECONDS = 90.0


class Presence:
    """Last-seen times and in-flight requests per agent, safe across threads."""

    # Input: clock (Callable[[], float])
    # Output: None
    # Processing: Start with nobody seen; tests pass a fake clock.
    # Changed by: Claude Code
    # Date: 2026-11-27
    def __init__(self, clock: Callable[[], float] = time.time) -> None:
        self.clock = clock
        self._lock = threading.Lock()
        self._seen: dict[str, float] = {}
        self._busy: dict[str, int] = {}
        self._doing: dict[str, str] = {}

    # Input: name (str)
    # Output: bool
    # Processing: Whether an agent is online now; the caller holds the lock.
    # Changed by: Claude Code
    # Date: 2026-11-27
    def _online(self, name: str) -> bool:
        seen = self._seen.get(name)
        return self._busy.get(name, 0) > 0 or (seen is not None and self.clock() - seen < ACTIVE_SECONDS)

    # Input: name (str), doing (str)
    # Output: bool
    # Processing: Note that an agent did something; True when it was offline until now.
    # Changed by: Claude Code
    # Date: 2026-11-27
    def seen(self, name: str, doing: str = "") -> bool:
        with self._lock:
            was = self._online(name)
            self._seen[name] = self.clock()
            if doing:
                self._doing[name] = doing
            return not was

    # Input: name (str), doing (str)
    # Output: bool
    # Processing: An agent's request has started; True when it was offline until now.
    # Changed by: Claude Code
    # Date: 2026-11-27
    def begin(self, name: str, doing: str = "") -> bool:
        came = self.seen(name, doing)
        with self._lock:
            self._busy[name] = self._busy.get(name, 0) + 1
        return came

    # Input: name (str)
    # Output: None
    # Processing: An agent's request has finished; it stays online for ACTIVE_SECONDS more.
    # Changed by: Claude Code
    # Date: 2026-11-27
    def end(self, name: str) -> None:
        with self._lock:
            left = self._busy.get(name, 0) - 1
            if left > 0:
                self._busy[name] = left
            else:
                self._busy.pop(name, None)
            self._seen[name] = self.clock()

    # Input: none
    # Output: dict[str, dict[str, Any]]
    # Processing: Every agent seen since the server started: online, busy, seconds since last seen, and what it last did.
    # Changed by: Claude Code
    # Date: 2026-11-27
    def snapshot(self) -> dict[str, dict[str, Any]]:
        with self._lock:
            now = self.clock()
            return {name: {"online": self._online(name), "busy": self._busy.get(name, 0) > 0,
                           "ago": round(max(0.0, now - seen), 1), "doing": self._doing.get(name, "")}
                    for name, seen in self._seen.items()}


# Input: method (str), path (str), body (dict[str, Any] | None)
# Output: str
# Processing: A short phrase for what an agent's request does, for the bubble's tooltip.
# Changed by: Claude Code
# Date: 2026-11-27
def describe(method: str, path: str, body: dict[str, Any] | None) -> str:
    body = body or {}
    tail = path.rsplit("/api/v1/whiteboard/", 1)[-1]
    if tail == "ask":
        return f"asking: {str(body.get('message', ''))[:80]}"
    if tail == "command":
        return str(body.get("command", ""))[:80]
    if tail == "op":
        return str(body.get("op", ""))
    if method == "GET":
        return "reading the board"
    return tail
