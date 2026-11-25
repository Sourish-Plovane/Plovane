"""Whiteboards built for one job, which the AI makes when its task calls for one.

``/board make <kind>`` writes ``whiteboards/<kind>.plo`` from the main
whiteboard and the project, and it opens as its own tab:

- ``structure``: services, folders and files, with the work going on. Files
  an agent is changing glow, and a folder being orchestrated has a banner.
- ``api``: services, endpoints and the calls between them. Arrows turn green
  while requests pass and red once one fails (see ``ops/live.py``).
- ``security``: the API picture plus attacks seen in requests and weak spots
  in the code, marked on the endpoint or file they concern.
- ``frontend``: every page of the app, live, in its own tile, with arrows for
  the links between pages. ``/frontend start`` runs the app's dev server;
  ``/pages`` lists the pages found.

Making one again with ``--refresh`` redraws it from the current project.
The live colours are not saved in the file: they come from the running
project, so the file stays a clean drawing.
"""

from __future__ import annotations

import json
import os
import re
import socket
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from backend.whiteboard.engine import USER, Context
from backend.whiteboard.model import Arrow, Block, Board, default_board
from backend.whiteboard.ops import live
from backend.whiteboard.ops.boardfile import EXTENSION, render
from backend.whiteboard.registry import OpError, flag, operation
from backend.whiteboard.store import MAIN_BOARD, SKIP_DIRS, STATE_DIR

KINDS = ("structure", "api", "security", "frontend")
FOLDER = "whiteboards"
"""Where purpose-built whiteboards are written in the project."""

STRUCTURE_KINDS = {"service", "server", "external", "database", "queue", "endpoint", "folder", "group", "file",
                   "note", "slot"}
API_KINDS = {"service", "server", "external", "database", "queue", "endpoint"}
API_ARROWS = {"api", "external", "database", "event"}

PAGE_SUFFIXES = (".tsx", ".ts", ".jsx", ".js", ".mdx", ".vue", ".svelte")
FRONTEND_DIRS = ("", "frontend", "web", "client", "app", "site", "ui", "www")
URL = re.compile(r"https?://(?:localhost|127\.0\.0\.1|0\.0\.0\.0|\[::1\]):\d+[^\s'\"]*")
FRONTEND_STATE = "frontend.json"
FRONTEND_LOG = "frontend.log"
START_WAIT = 30
"""Seconds to wait for a dev server to print its address."""

NOTES = {
    "structure": "Structure and work: files an agent is changing glow in its colour; a folder being orchestrated "
                 "shows a banner with how far along it is.",
    "api": "API requests: arrows turn green while requests pass and red when one fails. Report requests with "
           "/traffic or the middleware in backend/whiteboard/traffic.py; click a red arrow to ask why.",
    "security": "Security: attacks seen in requests (SQL injection, path traversal, script and command injection, "
                "scanners, repeated failed logins) and weak spots in the code show on the endpoint or file.",
    "frontend": "Frontend pages: each tile is a live page of the app; arrows are links between pages. "
                "Run /frontend start, or make it again with --url.",
}


# Input: source (Board), keep (set[str])
# Output: Board
# Processing: A new board holding copies of some blocks (their arrows and endpoints too), parents kept or dropped.
# Changed by: Claude Code
# Date: 2026-11-18
def copy_board(source: Board, keep: set[str], arrow_types: set[str] | None = None) -> Board:
    board = Board.from_dict(source.to_dict())
    fresh = default_board()
    board.agents, board.alerts, board.compiles = {}, [], {}
    board.view = dict(fresh.view)
    board.templates = {k: v for k, v in board.templates.items() if v.builtin}
    board.blocks = {i: b for i, b in board.blocks.items() if i in keep}
    for block in board.blocks.values():
        if block.parent not in keep:
            block.parent = None
    board.arrows = {i: a for i, a in board.arrows.items()
                    if a.source in keep and a.target in keep and (arrow_types is None or a.type in arrow_types)}
    board.endpoints = {i: e for i, e in board.endpoints.items() if e.service in keep}
    return board


# Input: board (Board), ids (set[str])
# Output: set[str]
# Processing: The blocks and every block that holds them.
# Changed by: Claude Code
# Date: 2026-11-18
def with_parents(board: Board, ids: set[str]) -> set[str]:
    found = set()
    for block_id in ids:
        guard, current = 0, board.blocks.get(block_id)
        while current is not None and guard < 50:
            found.add(current.id)
            current = board.blocks.get(current.parent) if current.parent else None
            guard += 1
    return found


# Input: board (Board), kind (str), text (str)
# Output: None
# Processing: Put the note saying what this whiteboard is for at its top.
# Changed by: Claude Code
# Date: 2026-11-18
def add_note(board: Board, kind: str, text: str) -> None:
    board.blocks[f"about-{kind}"] = Block(id=f"about-{kind}", kind="note", name=f"{kind.title()} whiteboard",
                                          layer=1, x=0, y=-120, w=420, h=80, meta={"summary": text, "about": kind})


# Input: ctx (Context)
# Output: Board
# Processing: The structure whiteboard: services, folders and files, no code or steps.
# Changed by: Claude Code
# Date: 2026-11-18
def structure_board(ctx: Context) -> Board:
    source = ctx.ws.main.board
    return copy_board(source, {b.id for b in source.blocks.values() if b.kind in STRUCTURE_KINDS})


# Input: ctx (Context), extra (set[str] | None)
# Output: Board
# Processing: The API whiteboard: services, endpoints and whatever API arrows connect.
# Changed by: Claude Code
# Date: 2026-11-18
def api_board(ctx: Context, extra: set[str] | None = None) -> Board:
    source = ctx.ws.main.board
    ids = {b.id for b in source.blocks.values() if b.kind in API_KINDS}
    ids |= {e.service for e in source.endpoints.values() if e.service in source.blocks}
    for arrow in source.arrows.values():
        if arrow.type in API_ARROWS or arrow.endpoint:
            ids |= {arrow.source, arrow.target}
    ids = {i for i in ids if i in source.blocks} | (extra or set())
    return copy_board(source, with_parents(source, ids), arrow_types=API_ARROWS | {"calls"})


# Input: ctx (Context)
# Output: Board
# Processing: The security whiteboard: the API picture plus every file with a weak spot.
# Changed by: Claude Code
# Date: 2026-11-18
def security_board(ctx: Context) -> Board:
    if ctx.actor == USER or ctx.access(None) >= 1:
        ctx.execute("security.scan", {})
    else:  # an agent scans only what it may read
        for grant in ctx.board.agents[ctx.actor].grants:
            if grant.get("mode") in ("read", "read-write") and (ctx.root / grant["path"]).exists():
                ctx.execute("security.scan", {"target": grant["path"]})
    source = ctx.ws.main.board
    files = {spot["file"] for spot in live.live_of(ctx.ws)["weak_spots"]}
    extra = {b.id for b in source.blocks.values() if b.kind == "file" and (b.path or "").strip("/") in files}
    return api_board(ctx, extra)


# Input: root (Path)
# Output: Path | None
# Processing: The folder holding the app's frontend: a package.json with a dev or start script, else HTML pages.
# Changed by: Claude Code
# Date: 2026-11-18
def frontend_dir(root: Path) -> Path | None:
    for name in FRONTEND_DIRS:
        folder = root / name if name else root
        package = folder / "package.json"
        if package.is_file():
            try:
                scripts = json.loads(package.read_text(encoding="utf-8")).get("scripts") or {}
            except ValueError:
                scripts = {}
            if scripts.get("dev") or scripts.get("start"):
                return folder
    for name in FRONTEND_DIRS:
        folder = root / name if name else root
        if folder.is_dir() and any(folder.glob("*.html")):
            return folder
    return None


# Input: path (Path), base (Path)
# Output: bool
# Processing: Whether a file sits in a tool, build or dependency folder.
# Changed by: Claude Code
# Date: 2026-11-18
def skipped(path: Path, base: Path) -> bool:
    parts = path.relative_to(base).parts
    return any(p in SKIP_DIRS or p.startswith(".") or p in ("dist", "build", "out", "coverage") for p in parts)


# Input: root (Path)
# Output: list[dict[str, str]]
# Processing: The app's pages and the file behind each: Next.js, SvelteKit, Nuxt, React Router or plain HTML.
# Changed by: Claude Code
# Date: 2026-11-18
def find_pages(root: Path) -> list[dict[str, str]]:
    base = frontend_dir(root) or root
    pages: dict[str, str] = {}

    # Input: route (str), file (Path)
    # Output: None
    # Processing: Note one page, keeping the first file found for a route.
    # Changed by: Claude Code
    # Date: 2026-11-18
    def add(route: str, file: Path) -> None:
        route = "/" + "/".join(p for p in route.split("/") if p and not (p.startswith("(") and p.endswith(")")))
        pages.setdefault(route, file.relative_to(root).as_posix())

    for app in (base / "app", base / "src" / "app"):
        for file in sorted(app.rglob("page.*")) if app.is_dir() else []:
            if file.suffix in PAGE_SUFFIXES and not skipped(file, base):
                add(file.parent.relative_to(app).as_posix().replace(".", ""), file)
    for folder in (base / "pages", base / "src" / "pages"):
        for file in sorted(folder.rglob("*")) if folder.is_dir() else []:
            rel = file.relative_to(folder)
            if file.suffix in PAGE_SUFFIXES and not skipped(file, base) and rel.parts[0] != "api" \
                    and not file.stem.startswith("_"):
                route = rel.with_suffix("").as_posix()
                add(re.sub(r"(^|/)index$", "", route), file)
    routes = base / "src" / "routes"
    for file in sorted(routes.rglob("+page.svelte")) if routes.is_dir() else []:
        add(file.parent.relative_to(routes).as_posix().replace(".", ""), file)
    if not pages:
        for file in sorted((base / "src").rglob("*")) if (base / "src").is_dir() else []:
            if file.suffix in PAGE_SUFFIXES and file.is_file() and not skipped(file, base):
                text = file.read_text(encoding="utf-8", errors="replace")
                for route in re.findall(r"""\bpath\s*[=:]\s*["'](/[^"'*]*)["']""", text):
                    add(route, file)
    if not pages:
        for file in sorted(base.rglob("*.html")):
            if not skipped(file, base):
                rel = file.relative_to(base).as_posix()
                add("" if rel == "index.html" else rel, file)
    return [{"route": r, "file": f} for r, f in sorted(pages.items())][:60]


# Input: root (Path), pages (list[dict[str, str]])
# Output: list[tuple[str, str]]
# Processing: Links between pages found in each page's file: (from route, to route).
# Changed by: Claude Code
# Date: 2026-11-18
def page_links(root: Path, pages: list[dict[str, str]]) -> list[tuple[str, str]]:
    routes = {p["route"] for p in pages}
    links = set()
    for page in pages:
        file = root / page["file"]
        if not file.is_file():
            continue
        text = file.read_text(encoding="utf-8", errors="replace")
        for target in re.findall(r"""(?:href|to)\s*=\s*\{?\s*["'`](/[^"'`#?]*)|navigate\(\s*["'`](/[^"'`#?]*)|"""
                                 r"""push\(\s*["'`](/[^"'`#?]*)""", text):
            route = next((t for t in target if t), "").rstrip("/") or "/"
            route = route.removesuffix(".html") if route not in routes and route.removesuffix(".html") in routes \
                else route
            if route in routes and route != page["route"]:
                links.add((page["route"], route))
            elif route.lstrip("/") in routes:
                links.add((page["route"], route.lstrip("/")))
    return sorted(links)


# Input: route (str)
# Output: str
# Processing: A block id for a page route.
# Changed by: Claude Code
# Date: 2026-11-18
def page_id(route: str) -> str:
    return "page-" + (re.sub(r"[^a-z0-9]+", "-", route.lower()).strip("-") or "home")


# Input: ctx (Context)
# Output: dict[str, Any] | None
# Processing: The running dev server's details, if it is still up.
# Changed by: Claude Code
# Date: 2026-11-18
def running_frontend(ctx: Context) -> dict[str, Any] | None:
    process = ctx.ws.main.processes.get("frontend")
    path = ctx.root / STATE_DIR / FRONTEND_STATE
    if process is not None and process.poll() is None and path.is_file():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except ValueError:
            return None
    return None


# Input: ctx (Context), url (str | None)
# Output: Board
# Processing: The frontend whiteboard: one live tile per page, with arrows for links between them.
# Changed by: Claude Code
# Date: 2026-11-18
def frontend_board(ctx: Context, url: str | None) -> Board:
    base_url = (url or (running_frontend(ctx) or {}).get("url") or "").rstrip("/")
    pages = find_pages(ctx.root)
    if not pages:
        ctx.notes.append("no pages found; looked for Next.js, SvelteKit, Nuxt, React Router routes and .html files")
    board = default_board()
    for index, page in enumerate(pages):
        route = page["route"]
        board.blocks[page_id(route)] = Block(
            id=page_id(route), kind="page", name=route, layer=1, x=(index % 3) * 360, y=(index // 3) * 260,
            w=320, h=220, path=page["file"],
            meta={"route": route, "url": f"{base_url}{route if route.startswith('/') else '/' + route}"
                  if base_url else None, "summary": page["file"]})
    for source, target in page_links(ctx.root, pages):
        arrow_id = f"link-{page_id(source)}-{page_id(target)}"
        board.arrows[arrow_id] = Arrow(id=arrow_id, source=page_id(source), target=page_id(target), type="calls",
                                       meta={"label": "link"})
    if not base_url:
        ctx.notes.append("pages have no address yet: run /frontend start, or make it again with --url")
    return board


# Input: ctx (Context), kind (str), path (str | None), url (str | None), refresh (Any)
# Output: dict[str, Any]
# Processing: Write a whiteboard built for one job and open it as its own tab.
# Changed by: Claude Code
# Date: 2026-11-18
@operation("board.make", command="/board make", tool="plovane_board_make", group="plovane_view")
def make(ctx: Context, kind: str, path: str | None = None, url: str | None = None,
         refresh: Any = False) -> dict[str, Any]:
    """Builds a whiteboard for one job (structure, api, security or frontend) as its own .plo tab."""
    kind = kind.strip().lower()
    if kind not in KINDS:
        raise OpError("INVALID_ARGS", f"kind is one of {', '.join(KINDS)}")
    if ctx.actor != USER and not ctx.board.agents.get(ctx.actor, None):
        raise OpError("FORBIDDEN", f"unknown agent {ctx.actor!r}")
    rel = (path or f"{FOLDER}/{kind}{EXTENSION}").strip().strip("/")
    if not rel.endswith(EXTENSION) or rel == MAIN_BOARD or ".." in rel.split("/"):
        raise OpError("INVALID_ARGS", f"give a whiteboard file other than {MAIN_BOARD}, ending in {EXTENSION}")
    if ctx.fs.exists(rel) and not flag(refresh):
        return {"board": rel, "kind": kind, "existing": True,
                "message": f"{rel} already exists; open its tab, or make it again with --refresh"}
    board = {"structure": structure_board, "api": api_board, "security": security_board}[kind](ctx) \
        if kind != "frontend" else frontend_board(ctx, url)
    about = ctx.ws.main.board.view.get("about")
    if about:
        board.view["about"] = about
    add_note(board, kind, NOTES[kind])
    ctx.fs.allow_plo = True
    try:
        ctx.fs.write(rel, render(board, agents=False))
    finally:
        ctx.fs.allow_plo = False
    blocks = [b for b in board.blocks.values() if b.kind != "note"]
    return {"board": rel, "kind": kind, "blocks": len(blocks), "arrows": len(board.arrows),
            "endpoints": len(board.endpoints)}


# Input: ctx (Context)
# Output: dict[str, Any]
# Processing: The app's pages and links found in the project, and the dev server's address if it runs.
# Changed by: Claude Code
# Date: 2026-11-18
@operation("frontend.pages", command="/pages", tool="plovane_pages", group="plovane_view", mutates=False)
def pages(ctx: Context) -> dict[str, Any]:
    """Lists the app's pages, the file behind each, and the links between them."""
    found = find_pages(ctx.root)
    folder = frontend_dir(ctx.root)
    return {"folder": folder.relative_to(ctx.root).as_posix() if folder else None, "pages": found,
            "links": [{"from": a, "to": b} for a, b in page_links(ctx.root, found)],
            "url": (running_frontend(ctx) or {}).get("url")}


# Input: none
# Output: int
# Processing: A free local port.
# Changed by: Claude Code
# Date: 2026-11-18
def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


# Input: ctx (Context), action (str), script (str | None), url (str | None)
# Output: dict[str, Any]
# Processing: Start or stop the app's dev server so the frontend whiteboard can show live pages.
# Changed by: Claude Code
# Date: 2026-11-18
@operation("frontend.run", command="/frontend", tool="plovane_frontend", group="plovane_view", mutates=False)
def run_frontend(ctx: Context, action: str = "start", script: str | None = None,
                 url: str | None = None) -> dict[str, Any]:
    """Starts or stops the app's dev server (npm run dev, or a static server for HTML pages)."""
    state_path = ctx.root / STATE_DIR / FRONTEND_STATE
    process = ctx.ws.main.processes.get("frontend")
    if action == "stop":
        stopped = process is not None and process.poll() is None
        if stopped and not ctx.dry_run:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
        if not ctx.dry_run:
            ctx.ws.main.processes.pop("frontend", None)
            state_path.unlink(missing_ok=True)
        return {"stopped": stopped}
    if action == "status":
        return {"running": running_frontend(ctx) is not None, **(running_frontend(ctx) or {})}
    if action != "start":
        raise OpError("INVALID_ARGS", "action is start, stop or status")
    if url:  # the person runs the app themselves; just remember where
        if not ctx.dry_run:
            state_path.parent.mkdir(exist_ok=True)
            state_path.write_text(json.dumps({"url": url.rstrip("/"), "external": True}), encoding="utf-8")
        return {"url": url.rstrip("/"), "started": False}
    folder = frontend_dir(ctx.root)
    if folder is None:
        raise OpError("NOT_FOUND", "no frontend found: no package.json with a dev or start script, and no .html pages")
    rel = folder.relative_to(ctx.root).as_posix()
    if ctx.actor != USER:
        ctx.require(rel if rel != "." else None, "read-write")  # running the app runs its code
    if running_frontend(ctx):
        return {"already_running": True, **running_frontend(ctx)}
    package = folder / "package.json"
    if package.is_file():
        scripts = json.loads(package.read_text(encoding="utf-8")).get("scripts") or {}
        name = script or ("dev" if scripts.get("dev") else "start")
        if name not in scripts:
            raise OpError("NOT_FOUND", f"package.json has no {name!r} script; it has {sorted(scripts)}")
        command, port = ["npm", "run", name], None
    else:
        port = free_port()
        command = [sys.executable, "-m", "http.server", str(port), "--bind", "127.0.0.1"]
    if ctx.dry_run:
        return {"would_run": " ".join(command), "folder": rel}
    state_path.parent.mkdir(exist_ok=True)
    log_path = ctx.root / STATE_DIR / FRONTEND_LOG
    log = log_path.open("w", encoding="utf-8")
    try:
        started = subprocess.Popen(command, cwd=folder, stdout=log, stderr=subprocess.STDOUT,
                                   env={**os.environ, "BROWSER": "none", "CI": "1"})
    except FileNotFoundError:
        raise OpError("UNAVAILABLE", f"{command[0]} is not installed") from None
    ctx.ws.main.processes["frontend"] = started
    found = f"http://127.0.0.1:{port}" if port else None
    deadline = time.time() + START_WAIT
    while found is None and time.time() < deadline and started.poll() is None:
        time.sleep(0.3)
        match = URL.search(log_path.read_text(encoding="utf-8", errors="replace"))
        if match:
            found = match.group(0).rstrip("/").replace("0.0.0.0", "127.0.0.1")
    if port:
        for _ in range(30):  # wait until the static server answers
            with socket.socket() as sock:
                if sock.connect_ex(("127.0.0.1", port)) == 0:
                    break
            time.sleep(0.1)
    if started.poll() is not None:
        raise OpError("FAILED", f"the dev server stopped: {log_path.read_text(errors='replace')[-400:]}")
    state = {"url": found, "pid": started.pid, "folder": rel, "command": " ".join(command)}
    state_path.write_text(json.dumps(state), encoding="utf-8")
    if not found:
        ctx.notes.append(f"started, but no address was printed within {START_WAIT}s; see .plovane/{FRONTEND_LOG}")
    return {"started": True, **state}
