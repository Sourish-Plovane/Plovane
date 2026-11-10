"""Live change events for the whiteboard, sent to browsers as Server-Sent Events.

Every change the engine makes (a person's click, an AI's MCP call, a CLI
command, a change another process wrote to board.json) becomes one event:

    {"seq": 12, "at": "...", "kind": "change", "actor": "claude",
     "op": "block.add", "command": "/block add service payments",
     "changes": {"blocks": {"added": ["payments"], ...}}}

``kind`` is ``change``, ``external`` (board.json changed under the server),
``agent`` (an AI agent run reporting what it is doing) or ``hello`` (sent
first on every connection). The last 500 events are kept, so a client that
reconnects with ``Last-Event-ID`` misses nothing.
"""

from __future__ import annotations

import json
import threading
from collections import deque
from typing import Any, Iterator

KEEP = 500
HEARTBEAT_SECONDS = 15.0


class EventBus:
    """Fan-out of change events to any number of waiting streams."""

    # Input: none
    # Output: None
    # Processing: Start with no events and sequence number zero.
    # Changed by: Claude Code
    # Date: 2026-11-09
    def __init__(self) -> None:
        self._events: deque[dict[str, Any]] = deque(maxlen=KEEP)
        self._seq = 0
        self._cond = threading.Condition()

    # Input: event (dict[str, Any])
    # Output: dict[str, Any]
    # Processing: Number an event, keep it and wake every stream.
    # Changed by: Claude Code
    # Date: 2026-11-09
    def publish(self, event: dict[str, Any]) -> dict[str, Any]:
        with self._cond:
            self._seq += 1
            stored = {"seq": self._seq, **event}
            self._events.append(stored)
            self._cond.notify_all()
        return stored

    # Input: after (int)
    # Output: list[dict[str, Any]]
    # Processing: Kept events newer than a sequence number.
    # Changed by: Claude Code
    # Date: 2026-11-09
    def since(self, after: int) -> list[dict[str, Any]]:
        with self._cond:
            return [e for e in self._events if e["seq"] > after]

    # Input: none
    # Output: int
    # Processing: The newest sequence number handed out.
    # Changed by: Claude Code
    # Date: 2026-11-09
    @property
    def seq(self) -> int:
        with self._cond:
            return self._seq

    # Input: after (int), stop (threading.Event | None), timeout (float)
    # Output: Iterator[str]
    # Processing: SSE text: every event after ``after`` as it arrives, with a comment line as heartbeat.
    # Changed by: Claude Code
    # Date: 2026-11-09
    def stream(self, after: int, stop: threading.Event | None = None,
               timeout: float = HEARTBEAT_SECONDS) -> Iterator[str]:
        yield "retry: 2000\n\n"
        yield format_event({"seq": after, "kind": "hello"}, with_id=False)
        last = after
        while stop is None or not stop.is_set():
            with self._cond:
                if not any(e["seq"] > last for e in self._events):
                    self._cond.wait(timeout)
                pending = [e for e in self._events if e["seq"] > last]
            if not pending:
                yield ": keep-alive\n\n"
                continue
            for event in pending:
                last = event["seq"]
                yield format_event(event)


# Input: event (dict[str, Any]), with_id (bool)
# Output: str
# Processing: One event in SSE wire format.
# Changed by: Claude Code
# Date: 2026-11-09
def format_event(event: dict[str, Any], with_id: bool = True) -> str:
    head = f"id: {event['seq']}\n" if with_id else ""
    return f"{head}event: {event.get('kind', 'change')}\ndata: {json.dumps(event, default=str)}\n\n"
