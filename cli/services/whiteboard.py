"""Find, start and talk to the whiteboard for a project, the way ``docker``
starts Docker Desktop.

    ensure_running(project)   whiteboard up for this folder, or launch it
    PersonClient              calls it as the person (token from server.json)

Launch order when nothing answers for the folder:

1. ``Plovane.app`` in /Applications or ~/Applications (macOS, packaged build)
2. this repo's Electron app: ``frontend/desktop`` with ``--project <dir>``
3. headless: ``python -m backend.whiteboard.server`` and the whiteboard
   opened in the default browser

``--headless`` skips straight to 3 without the browser, for CI and SSH.
The desktop app holds a single-instance lock, so launching it while it runs
hands it the folder and brings its window forward instead of starting a
second copy.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
import webbrowser
from pathlib import Path
from typing import Any

PORT = 8700
REPO = Path(__file__).resolve().parents[2]
DESKTOP = REPO / "frontend" / "desktop"
LOG_DIR = Path(os.path.expanduser("~")) / ".plovane" / "logs"


class WhiteboardError(RuntimeError):
    """The whiteboard could not be started or reached, or refused a call."""


# Input: url (str), token (str | None), body (dict | None), method (str | None), timeout (float)
# Output: dict[str, Any]
# Processing: Call the whiteboard API and return its envelope, even for HTTP errors.
# Changed by: Claude Code
# Date: 2026-11-09
def call(url: str, token: str | None = None, body: dict[str, Any] | None = None,
         method: str | None = None, timeout: float = 600) -> dict[str, Any]:
    data = None if body is None else json.dumps(body).encode()
    headers = {"content-type": "application/json"}
    if token:
        headers["authorization"] = f"Bearer {token}"
    request = urllib.request.Request(url, data=data, headers=headers, method=method or ("POST" if data else "GET"))
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read() or b"{}")
    except urllib.error.HTTPError as exc:
        try:
            return json.loads(exc.read() or b"{}")
        except json.JSONDecodeError:
            return {"ok": False, "data": None, "error": {"code": f"HTTP_{exc.code}", "message": str(exc)}}


# Input: port (int)
# Output: dict[str, Any] | None
# Processing: The /healthz data of whatever answers on the port, or None.
# Changed by: Claude Code
# Date: 2026-11-09
def health(port: int = PORT) -> dict[str, Any] | None:
    try:
        reply = call(f"http://127.0.0.1:{port}/healthz", timeout=1.0)
    except (OSError, ValueError):
        return None
    return reply.get("data") if reply.get("ok") else None


# Input: project (Path)
# Output: dict[str, Any]
# Processing: The project's .plovane/server.json (url, port, pid, token), or {}.
# Changed by: Claude Code
# Date: 2026-11-09
def server_file(project: Path) -> dict[str, Any]:
    try:
        return json.loads((project / ".plovane" / "server.json").read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


# Input: project (Path), port (int)
# Output: dict[str, Any] | None
# Processing: Connection details when a whiteboard for this folder is up and its server.json is readable.
# Changed by: Claude Code
# Date: 2026-11-09
def running(project: Path, port: int = PORT) -> dict[str, Any] | None:
    up = health(port)
    if not up or Path(up["project"]).resolve() != project:
        return None
    info = server_file(project)
    if not info.get("token"):
        return None
    return {"url": info.get("url") or f"http://127.0.0.1:{port}", "token": info["token"], "project": str(project)}


# Input: none
# Output: list[str] | None
# Processing: The command that opens the desktop app, packaged first, then this repo's Electron build.
# Changed by: Claude Code
# Date: 2026-11-09
def desktop_command() -> list[str] | None:
    if sys.platform == "darwin":
        for folder in (Path("/Applications"), Path.home() / "Applications"):
            if (folder / "Plovane.app").exists():
                return ["open", "-a", str(folder / "Plovane.app"), "--args"]
    electron = DESKTOP / "node_modules" / ".bin" / ("electron.cmd" if os.name == "nt" else "electron")
    if electron.exists():
        return [str(electron), str(DESKTOP)]
    return None


# Input: args (list[str]), log_name (str), env (dict | None)
# Output: subprocess.Popen
# Processing: Start a process detached from this terminal, logging to ~/.plovane/logs, without ELECTRON_RUN_AS_NODE.
# Changed by: Claude Code
# Date: 2026-11-09
def spawn(args: list[str], log_name: str, env: dict[str, str] | None = None) -> subprocess.Popen:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    log = open(LOG_DIR / log_name, "ab")  # noqa: SIM115  (handed to the child)
    child_env = {**os.environ, **(env or {})}
    # Terminals inside VS Code and other Electron apps set this, which makes
    # Electron start as plain Node with no window.
    child_env.pop("ELECTRON_RUN_AS_NODE", None)
    kwargs: dict[str, Any] = {"cwd": str(REPO), "stdout": log, "stderr": subprocess.STDOUT,
                              "stdin": subprocess.DEVNULL, "env": child_env}
    if os.name == "nt":
        kwargs["creationflags"] = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP  # type: ignore[attr-defined]
    else:
        kwargs["start_new_session"] = True
    return subprocess.Popen(args, **kwargs)


# Input: project (Path), headless (bool), port (int), timeout (float), focus (bool), quiet (bool)
# Output: dict[str, Any]
# Processing: Return the whiteboard for a folder, launching the desktop app (or a headless server) when none is up.
# Changed by: Claude Code
# Date: 2026-11-09
def ensure_running(project: Path | str, *, headless: bool = False, port: int = PORT, timeout: float = 45,
                   focus: bool = False, quiet: bool = False) -> dict[str, Any]:
    project = Path(project).expanduser().resolve()
    if not project.is_dir():
        raise WhiteboardError(f"{project} is not a folder")
    info = running(project, port)
    say = (lambda _msg: None) if quiet else (lambda msg: print(msg, file=sys.stderr, flush=True))
    desktop = None if headless else desktop_command()
    if info and not (focus and desktop):
        return info

    other = health(port)
    if other and Path(other["project"]).resolve() != project and not desktop:
        raise WhiteboardError(f"port {port} already serves {other['project']}; close that whiteboard first")

    env = {"PLOVANE_PYTHON": sys.executable, "PYTHONPATH": str(REPO)}
    how = "headless"
    if desktop:
        say(f"Opening Plovane for {project} ...")
        spawn([*desktop, "--project", str(project)], "desktop.log", env)
        how = "desktop"
    elif not info:
        say(f"Starting the Plovane whiteboard for {project} ...")
        spawn([sys.executable, "-m", "backend.whiteboard.server", "--project", str(project), "--port", str(port)],
              "server.log", env)

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        info = running(project, port)
        if info:
            if how == "headless" and not headless:
                webbrowser.open(f"{info['url']}/whiteboard")
            info["launched"] = how
            return info
        time.sleep(0.3)
    raise WhiteboardError(f"the whiteboard did not start within {int(timeout)} s; see {LOG_DIR / (how == 'desktop' and 'desktop.log' or 'server.log')}")


class PersonClient:
    """Calls a running whiteboard as the person, with the token from server.json."""

    # Input: info (dict[str, Any])
    # Output: None
    # Processing: Remember the server URL and person token from ensure_running.
    # Changed by: Claude Code
    # Date: 2026-11-09
    def __init__(self, info: dict[str, Any]) -> None:
        self.url = info["url"].rstrip("/")
        self.token = info["token"]

    # Input: path (str), body (dict | None), method (str | None)
    # Output: dict[str, Any]
    # Processing: Call /api/v1/whiteboard/<path> and return the envelope.
    # Changed by: Claude Code
    # Date: 2026-11-09
    def api(self, path: str, body: dict[str, Any] | None = None, method: str | None = None) -> dict[str, Any]:
        return call(f"{self.url}/api/v1/whiteboard/{path.lstrip('/')}", self.token, body, method)

    # Input: command (str), confirm (bool), dry_run (bool)
    # Output: dict[str, Any]
    # Processing: Run one slash command.
    # Changed by: Claude Code
    # Date: 2026-11-09
    def command(self, command: str, confirm: bool = False, dry_run: bool = False) -> dict[str, Any]:
        return self.api("command", {"command": command, "confirm": confirm, "dry_run": dry_run})

    # Input: agent (str), access (str | None), path (str)
    # Output: dict[str, Any]
    # Processing: Add an agent if needed, grant it access if asked, and get a fresh API token for it.
    # Changed by: Claude Code
    # Date: 2026-11-09
    def agent_token(self, agent: str, access: str | None = None, path: str = ".") -> dict[str, Any]:
        return self.api(f"agents/{agent}/token", {"access": access, "path": path})


# Input: none
# Output: bool
# Processing: Whether the desktop app can be launched on this machine.
# Changed by: Claude Code
# Date: 2026-11-09
def desktop_available() -> bool:
    return desktop_command() is not None or bool(shutil.which("electron"))
