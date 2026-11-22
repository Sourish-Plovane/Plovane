"""Report an app's requests to the whiteboard, so its API arrows go green or red live.

Wrap any WSGI app (Flask, Django, Bottle):

    from backend.whiteboard.traffic import PlovaneTraffic
    app.wsgi_app = PlovaneTraffic(app.wsgi_app, project="/path/to/project")      # Flask

Each request's method, path, status and time are sent in the background to
the whiteboard server running for that project (its address and token are
read from ``.plovane/server.json``). A short sample of the query and body is
sent too, so attacks can be spotted. Sending never slows down or breaks the
app: if the whiteboard is not running, reports are dropped.

For Node (Express), the same report is one POST per request:

    app.use((req, res, next) => { const t = Date.now(); res.on('finish', () => fetch(
      'http://127.0.0.1:8700/api/v1/whiteboard/traffic', {method: 'POST', headers: {'content-type':
      'application/json', authorization: 'Bearer ' + TOKEN}, body: JSON.stringify({method: req.method,
      path: req.originalUrl, status: res.statusCode, ms: Date.now() - t, ip: req.ip})}).catch(() => {})); next() })
"""

from __future__ import annotations

import json
import queue
import threading
import time
import urllib.request
from pathlib import Path
from typing import Any, Callable, Iterable

SAMPLE = 500
"""Characters of the request body sent along, enough to recognise an attack."""


class PlovaneTraffic:
    """WSGI middleware sending each request's outcome to the whiteboard."""

    # Input: app (Callable), project (str | Path), url (str | None), token (str | None)
    # Output: None
    # Processing: Wrap a WSGI app; find the whiteboard server from the project's .plovane/server.json.
    # Changed by: Claude Code
    # Date: 2026-11-18
    def __init__(self, app: Callable, project: str | Path = ".", url: str | None = None,
                 token: str | None = None) -> None:
        self.app = app
        info: dict[str, Any] = {}
        try:
            info = json.loads((Path(project) / ".plovane" / "server.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            pass
        self.url = (url or info.get("url") or f"http://127.0.0.1:{info.get('port', 8700)}").rstrip("/")
        self.token = token or info.get("token")
        self.outbox: queue.Queue = queue.Queue(maxsize=1000)
        threading.Thread(target=self._send_loop, daemon=True).start()

    # Input: environ (dict), start_response (Callable)
    # Output: Iterable[bytes]
    # Processing: Run the app, note its status, and queue a report.
    # Changed by: Claude Code
    # Date: 2026-11-18
    def __call__(self, environ: dict[str, Any], start_response: Callable) -> Iterable[bytes]:
        started, seen = time.time(), {}
        body = b""
        try:
            length = int(environ.get("CONTENT_LENGTH") or 0)
        except ValueError:
            length = 0
        if 0 < length <= 1_000_000:
            body = environ["wsgi.input"].read(length)
            import io

            environ["wsgi.input"] = io.BytesIO(body)  # the app still reads the whole body

        # Input: status (str), headers (list), exc_info (Any)
        # Output: Callable
        # Processing: Remember the status code the app answered with.
        # Changed by: Claude Code
        # Date: 2026-11-18
        def recording_start(status: str, headers: list, exc_info: Any = None) -> Callable:
            seen["status"] = int(status.split(" ", 1)[0])
            return start_response(status, headers, exc_info) if exc_info else start_response(status, headers)

        try:
            return self.app(environ, recording_start)
        except Exception as exc:
            seen["status"], seen["error"] = 500, f"{type(exc).__name__}: {exc}"
            raise
        finally:
            self.report({"method": environ.get("REQUEST_METHOD", "GET"),
                         "path": environ.get("PATH_INFO", "/") + (f"?{environ['QUERY_STRING']}"
                                                                   if environ.get("QUERY_STRING") else ""),
                         "status": seen.get("status", 0), "ms": round((time.time() - started) * 1000),
                         "ip": environ.get("HTTP_X_FORWARDED_FOR", environ.get("REMOTE_ADDR", "unknown")),
                         "body": body[:SAMPLE].decode("utf-8", "replace"), "error": seen.get("error")})

    # Input: request (dict[str, Any])
    # Output: None
    # Processing: Queue one report; drop it if the queue is full rather than slow the app.
    # Changed by: Claude Code
    # Date: 2026-11-18
    def report(self, request: dict[str, Any]) -> None:
        try:
            self.outbox.put_nowait(request)
        except queue.Full:
            pass

    # Input: none
    # Output: None
    # Processing: Send queued reports in small batches, forever, in the background.
    # Changed by: Claude Code
    # Date: 2026-11-18
    def _send_loop(self) -> None:
        while True:
            batch = [self.outbox.get()]
            while len(batch) < 50:
                try:
                    batch.append(self.outbox.get_nowait())
                except queue.Empty:
                    break
            request = urllib.request.Request(
                f"{self.url}/api/v1/whiteboard/traffic", data=json.dumps({"requests": batch}).encode(),
                headers={"content-type": "application/json",
                         **({"authorization": f"Bearer {self.token}"} if self.token else {})})
            try:
                urllib.request.urlopen(request, timeout=3).read()
            except Exception:  # noqa: BLE001  (the whiteboard being down must never hurt the app)
                pass
