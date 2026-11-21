"""Live state: what agents are working on, how API requests are going, and attacks.

Nothing here is saved in a whiteboard file or undone: it describes the
running project, not the drawing. It lives on the main whiteboard's
``Workspace.live`` so every whiteboard tab shows the same picture.

- **Working on**: every change an agent makes marks the files it touched for
  ``ACTIVITY_TTL`` seconds. ``/agent working <file or folder> "task"`` says so
  up front; on a folder it is an *orchestration*, shown as a banner with how
  many of the folder's files the agent has touched so far. ``/agent done``
  clears it.
- **API status**: ``/traffic`` (or ``POST /api/v1/whiteboard/traffic``, or the
  WSGI middleware in ``backend.whiteboard.traffic``) reports a request. It is
  matched to an endpoint on the board; arrows to that endpoint turn green
  while requests pass and red once one fails. Rules decide pass and fail, so
  watching costs no tokens; ``/api explain`` asks an AI why one failed, only
  when you ask.
- **Security**: every reported request is checked for attack patterns (SQL
  injection, path traversal, script injection, command injection, scanners,
  repeated failed logins). ``/security scan`` looks for weak spots in the
  code itself. Both show on the endpoint or file they concern.
"""

from __future__ import annotations

import json
import re
import time
from collections import deque
from pathlib import Path
from typing import Any
from urllib.parse import unquote_plus

from backend.whiteboard.engine import USER, Context
from backend.whiteboard.registry import OpError, flag, integer, items, operation
from backend.whiteboard.store import SKIP_DIRS, STATE_DIR

ACTIVITY_TTL = 45
"""Seconds a file stays marked as being worked on after the agent last touched it."""

ORCHESTRATION_TTL = 900
"""Seconds an orchestration banner stays up with no sign of the agent."""

KEEP_EVENTS = 200
"""Security events and unmatched requests kept in memory."""

BRUTE_FORCE = (5, 60)
"""This many refused logins (401/403) from one address to one path within this many seconds is an attack."""

ATTACKS = (
    ("sql_injection", "high", re.compile(
        r"(\bunion\b[\s\S]{0,40}\bselect\b|'\s*or\s*'?\d*'?\s*=|\bor\s+\d+\s*=\s*\d+|;\s*drop\s+table|"
        r"\bsleep\s*\(\s*\d+\s*\)|information_schema|'\s*--)", re.I)),
    ("path_traversal", "high", re.compile(r"(\.\./|\.\.\\|/etc/passwd|\\windows\\system32)", re.I)),
    ("command_injection", "high", re.compile(
        r"(;\s*(cat|ls|rm|curl|wget|bash|sh|nc)\b|\$\([^)]*\)|`[^`]+`|\|\s*(sh|bash)\b|&&\s*(cat|rm|curl|wget)\b)",
        re.I)),
    ("script_injection", "medium", re.compile(r"(<script|javascript:|onerror\s*=|onload\s*=|<iframe|<svg[^>]*\bon\w+=)",
                                              re.I)),
    ("scanner", "low", re.compile(r"/(\.env|\.git/|wp-admin|wp-login|phpmyadmin|\.aws/|server-status|actuator)\b",
                                  re.I)),
)
"""Attack patterns looked for in each request's path, query and body (decoded)."""

WEAK_SPOTS = (
    ("sql_from_text", "high", "SQL built from text; pass the values as query parameters instead",
     re.compile(r"\b(execute|executemany|query|raw)\s*\(\s*(f['\"]|['\"][^'\"\n]*['\"]\s*(%|\+|\.format)|`[^`]*\$\{)")),
    ("eval", "high", "eval/exec runs text as code; anything from a user here is an open door",
     re.compile(r"(?<![\w.])(eval|exec)\s*\(")),
    ("shell", "high", "runs a shell command; a value from outside can add its own commands",
     re.compile(r"shell\s*=\s*True|\bos\.system\s*\(|child_process\.exec\s*\(|\bexecSync\s*\(")),
    ("secret_in_code", "high", "a secret written into the code; read it from an environment variable",
     re.compile(r"(api[_-]?key|secret|password|passwd|token)['\"]?\s*[:=]\s*['\"][A-Za-z0-9_\-./+]{12,}['\"]", re.I)),
    ("unsafe_load", "high", "loading pickle or YAML this way can run code hidden in the data",
     re.compile(r"\bpickle\.loads?\s*\(|\byaml\.load\s*\((?![^)]*Loader\s*=\s*yaml\.SafeLoader)")),
    ("raw_html", "medium", "puts raw HTML on the page; text from users can carry scripts",
     re.compile(r"\.innerHTML\s*=|dangerouslySetInnerHTML|\|\s*safe\b|mark_safe\s*\(")),
    ("tls_off", "medium", "turns off certificate checks, so traffic can be intercepted",
     re.compile(r"verify\s*=\s*False|rejectUnauthorized\s*:\s*false")),
    ("weak_hash", "low", "MD5 and SHA-1 are too weak for passwords or signatures",
     re.compile(r"\b(md5|sha1)\s*\(|createHash\(\s*['\"](md5|sha1)['\"]", re.I)),
    ("debug_on", "low", "debug mode left on shows internals to anyone who triggers an error",
     re.compile(r"\bdebug\s*=\s*True\b|app\.run\([^)]*debug\s*=\s*True")),
    ("open_cors", "low", "any website may call this API from a visitor's browser",
     re.compile(r"Access-Control-Allow-Origin['\"]?\s*[:,]\s*['\"]\*|CORS\([^)]*origins\s*=\s*['\"]\*")),
)
"""Weak spots looked for line by line in the project's code: (kind, severity, why, pattern)."""

CODE_SUFFIXES = (".py", ".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs", ".go", ".rb", ".php", ".java")
SECURITY_LOG = "security.log"


# Input: none
# Output: dict[str, Any]
# Processing: An empty live state.
# Changed by: Claude Code
# Date: 2026-11-18
def new_live() -> dict[str, Any]:
    return {"files": {}, "orchestration": {}, "api": {}, "unmatched": deque(maxlen=KEEP_EVENTS),
            "security": deque(maxlen=KEEP_EVENTS), "attempts": {}, "weak_spots": [], "scanned_at": None}


# Input: ws (Any)
# Output: dict[str, Any]
# Processing: The live state of the project, kept on the main whiteboard.
# Changed by: Claude Code
# Date: 2026-11-18
def live_of(ws: Any) -> dict[str, Any]:
    main = ws.main
    if getattr(main, "live", None) is None:
        main.live = new_live()
    return main.live


# Input: ws (Any), event (dict[str, Any])
# Output: None
# Processing: Announce a live change on the main whiteboard's event stream.
# Changed by: Claude Code
# Date: 2026-11-18
def announce(ws: Any, event: dict[str, Any]) -> None:
    ws.main.emit({"kind": "live", **event})


# Input: ws (Any), agent (str), paths (list[str]), op (str)
# Output: bool
# Processing: Mark files an agent just changed, and count them toward its orchestrations.
# Changed by: Claude Code
# Date: 2026-11-18
def touch(ws: Any, agent: str, paths: list[str], op: str) -> bool:
    live, at = live_of(ws), time.time()
    marked = False
    for path in {p.strip("/") for p in paths if p}:
        live["files"][path] = {"agent": agent, "at": at, "op": op, "task": live["files"].get(path, {}).get("task")}
        marked = True
        for folder, job in live["orchestration"].items():
            if job["agent"] == agent and inside(path, folder):
                job["at"] = at
                if path not in job["done"]:
                    job["done"].append(path)
    return marked


# Input: path (str), folder (str)
# Output: bool
# Processing: Whether a project path is the folder or lies inside it ("" is the whole project).
# Changed by: Claude Code
# Date: 2026-11-18
def inside(path: str, folder: str) -> bool:
    folder = folder.strip("/")
    return not folder or path == folder or path.startswith(folder + "/")


# Input: ws (Any)
# Output: dict[str, Any]
# Processing: The live state as plain data for the whiteboard, dropping marks that have gone quiet.
# Changed by: Claude Code
# Date: 2026-11-18
def snapshot(ws: Any) -> dict[str, Any]:
    live, at = live_of(ws), time.time()
    for path in [p for p, mark in live["files"].items() if at - mark["at"] > ACTIVITY_TTL]:
        del live["files"][path]
    for folder in [f for f, job in live["orchestration"].items() if at - job["at"] > ORCHESTRATION_TTL]:
        del live["orchestration"][folder]
    files = {p: {**m, "age": round(at - m["at"], 1)} for p, m in live["files"].items()}
    jobs = {f: {"agent": j["agent"], "task": j["task"], "total": len(set(j["files"]) | set(j["done"])), "done": len(j["done"]),
                "touched": list(j["done"]), "age": round(at - j["started"], 1)}
            for f, j in live["orchestration"].items()}
    api = dict(live["api"])
    for endpoint in ws.board.endpoints.values():  # a /endpoint test counts until real traffic arrives
        test = endpoint.last_test
        if endpoint.id not in api and isinstance(test, dict) and test.get("status") is not None:
            code = int(test["status"]) if str(test["status"]).isdigit() else 0
            passed = 200 <= code < 400
            api[endpoint.id] = {"ok": passed, "status": code, "count": 1, "fails": 0 if passed else 1,
                                "source": "test"}
    threats: dict[str, dict[str, Any]] = {}
    for event in live["security"]:
        key = event.get("endpoint") or event.get("path")
        entry = threats.setdefault(key, {"count": 0, "worst": "low", "kinds": []})
        entry["count"] += 1
        if rank(event["severity"]) > rank(entry["worst"]):
            entry["worst"] = event["severity"]
        if event["kind"] not in entry["kinds"]:
            entry["kinds"].append(event["kind"])
    weak: dict[str, dict[str, Any]] = {}
    for spot in live["weak_spots"]:
        entry = weak.setdefault(spot["file"], {"count": 0, "worst": "low"})
        entry["count"] += 1
        if rank(spot["severity"]) > rank(entry["worst"]):
            entry["worst"] = spot["severity"]
    return {"files": files, "orchestration": jobs, "api": api, "unmatched": list(live["unmatched"])[-20:],
            "security": list(live["security"])[-50:], "threats": threats, "weak_spots": weak,
            "scanned_at": live["scanned_at"]}


# Input: severity (str)
# Output: int
# Processing: Order of severities, for picking the worst.
# Changed by: Claude Code
# Date: 2026-11-18
def rank(severity: str) -> int:
    return {"low": 0, "medium": 1, "high": 2}.get(severity, 0)


# Input: ctx (Context), agent (str | None)
# Output: str
# Processing: Which agent a working mark is for: the caller, or the one a person names.
# Changed by: Claude Code
# Date: 2026-11-18
def who(ctx: Context, agent: str | None) -> str:
    if ctx.actor != USER:
        return ctx.actor
    if not agent:
        raise OpError("INVALID_ARGS", "name the agent, e.g. /agent working src/payments \"task\" --agent claude")
    if agent not in ctx.board.agents:
        raise OpError("NOT_FOUND", f"no agent {agent!r}; add it with /agent add {agent}")
    return agent


# Input: root (Path), folder (str)
# Output: list[str]
# Processing: The code files on disk inside a folder, skipping tool and dependency folders.
# Changed by: Claude Code
# Date: 2026-11-18
def folder_files(root: Path, folder: str, limit: int = 2000) -> list[str]:
    base = root / folder if folder else root
    found: list[str] = []
    for path in sorted(base.rglob("*")):
        rel = path.relative_to(root)
        if any(part in SKIP_DIRS or part.startswith(".") for part in rel.parts):
            continue
        if path.is_file():
            found.append(rel.as_posix())
            if len(found) >= limit:
                break
    return found


# Input: ctx (Context), target (str), task (str), files (Any), agent (str | None)
# Output: dict[str, Any]
# Processing: Say an agent is working on a file, or orchestrating a whole folder.
# Changed by: Claude Code
# Date: 2026-11-18
@operation("agent.working", command="/agent working", tool="plovane_agent_working", group="plovane_agent",
           mutates=False)
def working(ctx: Context, target: str = "", task: str = "", files: Any = None,
            agent: str | None = None) -> dict[str, Any]:
    """Shows that an agent is working on a file, or orchestrating every file in a folder."""
    name = who(ctx, agent)
    path = target.strip().strip("/").removeprefix("./")
    path = "" if path == "." else path
    ctx.require(path or None, "read")
    where = ctx.root / path if path else ctx.root
    if not where.exists():
        raise OpError("NOT_FOUND", f"{path} is not in the project")
    live = live_of(ctx.ws)
    if where.is_dir():
        planned = sorted({f.strip("/") for f in items(files)}) if files else folder_files(ctx.root, path)
        job = {"agent": name, "task": task, "started": time.time(), "at": time.time(), "files": planned,
               "done": []}
        if not ctx.dry_run:
            live["orchestration"][path] = job
            announce(ctx.ws, {"what": "orchestration", "agent": name, "folder": path, "task": task})
        return {"agent": name, "orchestrating": path or ".", "task": task, "files": len(planned)}
    if not ctx.dry_run:
        live["files"][path] = {"agent": name, "at": time.time(), "op": "working", "task": task}
        announce(ctx.ws, {"what": "working", "agent": name, "file": path, "task": task})
    return {"agent": name, "working_on": path, "task": task}


# Input: ctx (Context), target (str | None), agent (str | None)
# Output: dict[str, Any]
# Processing: Clear an agent's working marks and orchestrations, everywhere or under one path.
# Changed by: Claude Code
# Date: 2026-11-18
@operation("agent.done", command="/agent done", tool="plovane_agent_done", group="plovane_agent", mutates=False)
def done(ctx: Context, target: str | None = None, agent: str | None = None) -> dict[str, Any]:
    """Clears an agent's working marks and orchestration banners."""
    name = who(ctx, agent)
    path = (target or "").strip().strip("/")
    path = "" if path == "." else path
    live = live_of(ctx.ws)
    jobs = [f for f, j in live["orchestration"].items() if j["agent"] == name and (not path or inside(f, path))]
    marks = [p for p, m in live["files"].items() if m["agent"] == name and inside(p, path)]
    if not ctx.dry_run:
        for folder in jobs:
            del live["orchestration"][folder]
        for file in marks:
            del live["files"][file]
        announce(ctx.ws, {"what": "done", "agent": name, "path": path})
    return {"agent": name, "cleared": {"orchestrations": jobs, "files": marks}}


# Input: ctx (Context)
# Output: dict[str, Any]
# Processing: The live state: files being worked on, orchestrations, API status and security.
# Changed by: Claude Code
# Date: 2026-11-18
@operation("agent.activity", command="/agent activity", tool="plovane_agent_activity", group="plovane_agent",
           mutates=False)
def activity(ctx: Context) -> dict[str, Any]:
    """Lists what agents are working on right now and every folder being orchestrated."""
    view = snapshot(ctx.ws)
    return {"files": view["files"], "orchestration": view["orchestration"]}


# -- API traffic -----------------------------------------------------------------

# Input: template (str)
# Output: re.Pattern
# Processing: A route template ("/orders/{id}", "/orders/:id", "/orders/<int:id>", "/orders/[id]") as a pattern.
# Changed by: Claude Code
# Date: 2026-11-18
def route_pattern(template: str) -> re.Pattern:
    parts = []
    for piece in template.strip().rstrip("/").split("/"):
        if re.fullmatch(r"\{[^}]+\}|:\w+|<[^>]+>|\[[^\]]+\]|\*", piece):
            parts.append("[^/]+")
        else:
            parts.append(re.escape(piece))
    return re.compile("^" + "/".join(parts) + "/?$")


# Input: board (Any), method (str), path (str)
# Output: Any
# Processing: The endpoint a request is for, preferring an exact path over a template.
# Changed by: Claude Code
# Date: 2026-11-18
def match_endpoint(board: Any, method: str, path: str) -> Any:
    path = path.split("?", 1)[0] or "/"
    candidates = [e for e in board.endpoints.values() if e.method.upper() == method.upper()]
    for endpoint in candidates:
        if endpoint.path.rstrip("/") == path.rstrip("/"):
            return endpoint
    for endpoint in candidates:
        if route_pattern(endpoint.path).match(path):
            return endpoint
    return None


# Input: text (str)
# Output: list[tuple[str, str, str]]
# Processing: The attack patterns found in a request's text: (kind, severity, matched piece).
# Changed by: Claude Code
# Date: 2026-11-18
def attacks_in(text: str) -> list[tuple[str, str, str]]:
    decoded = unquote_plus(unquote_plus(text))
    return [(kind, severity, m.group(0)[:80]) for kind, severity, pattern in ATTACKS
            if (m := pattern.search(decoded))]


# Input: ws (Any), request (dict[str, Any])
# Output: dict[str, Any]
# Processing: Record one observed request: endpoint status, unmatched routes and any attack it carries.
# Changed by: Claude Code
# Date: 2026-11-18
def record(ws: Any, request: dict[str, Any]) -> dict[str, Any]:
    live, at = live_of(ws), time.time()
    method = str(request.get("method") or "GET").upper()
    raw_path = str(request.get("path") or "/")
    path, _, query = raw_path.partition("?")
    query = str(request.get("query") or query)
    status = integer(request.get("status") or 0, "status")
    ok = 0 < status < 400
    ip = str(request.get("ip") or "unknown")
    body = request.get("body")
    body = body if isinstance(body, str) else json.dumps(body) if body is not None else ""
    endpoint = match_endpoint(ws.main.board, method, path) or match_endpoint(ws.board, method, path)
    reply: dict[str, Any] = {"method": method, "path": path, "status": status, "ok": ok,
                             "endpoint": endpoint.id if endpoint else None, "threats": []}
    if endpoint:
        entry = live["api"].setdefault(endpoint.id, {"ok": True, "status": 0, "count": 0, "fails": 0})
        entry.update(ok=ok, status=status, at=at, ms=request.get("ms"), source="traffic")
        entry["count"] += 1
        if not ok:
            entry["fails"] += 1
            entry["last_failure"] = {"status": status, "path": raw_path, "body": body[:500],
                                     "error": str(request.get("error") or "")[:500], "at": at}
    else:
        live["unmatched"].append({"method": method, "path": path, "status": status, "at": at})
    found = attacks_in(f"{path}?{query} {body}")
    if status in (401, 403):
        key = f"{ip} {path}"
        times = live["attempts"].setdefault(key, deque(maxlen=50))
        times.append(at)
        recent = [t for t in times if at - t <= BRUTE_FORCE[1]]
        already = any(e["kind"] == "brute_force" and e.get("ip") == ip and e["path"] == path and at - e["at"] < 60
                      for e in live["security"])
        if len(recent) >= BRUTE_FORCE[0] and not already:
            found.append(("brute_force", "high", f"{len(recent)} refused logins in {BRUTE_FORCE[1]}s"))
    for kind, severity, sample in found:
        event = {"kind": kind, "severity": severity, "endpoint": endpoint.id if endpoint else None,
                 "method": method, "path": path, "ip": ip, "status": status, "sample": sample, "at": at}
        live["security"].append(event)
        reply["threats"].append(kind)
        log_event(ws, event)
    return reply


# Input: ws (Any), event (dict[str, Any])
# Output: None
# Processing: Keep a security event on disk too, so it outlives the server (the folder is git-ignored).
# Changed by: Claude Code
# Date: 2026-11-18
def log_event(ws: Any, event: dict[str, Any]) -> None:
    try:
        folder = ws.root / STATE_DIR
        folder.mkdir(exist_ok=True)
        with (folder / SECURITY_LOG).open("a", encoding="utf-8") as out:
            out.write(json.dumps(event) + "\n")
    except OSError:
        pass  # the live view still has it


# Input: ctx (Context), method, path, status, ms, ip, body, error, requests (Any)
# Output: dict[str, Any]
# Processing: Report requests the app handled, so arrows show pass or fail and attacks are caught.
# Changed by: Claude Code
# Date: 2026-11-18
@operation("traffic.record", command="/traffic", tool="plovane_traffic", group="plovane_endpoint", mutates=False)
def traffic(ctx: Context, method: str | None = None, path: str | None = None, status: Any = None,
            ms: Any = None, ip: str | None = None, body: Any = None, error: str | None = None,
            requests: Any = None) -> dict[str, Any]:
    """Reports a request the app handled: its arrow turns green or red and attacks are flagged."""
    batch = requests if isinstance(requests, list) else []
    if method or path:
        if status is None:
            raise OpError("INVALID_ARGS", "give the status code, e.g. /traffic POST /charge 500")
        batch.append({"method": method, "path": path, "status": status, "ms": ms, "ip": ip, "body": body,
                      "error": error})
    if not batch:
        raise OpError("INVALID_ARGS", "give a request, e.g. /traffic POST /charge 500")
    if len(batch) > 500:
        raise OpError("INVALID_ARGS", "send at most 500 requests at once")
    if ctx.dry_run:
        return {"recorded": 0, "dry_run": True}
    results = [record(ctx.ws, dict(r)) for r in batch if isinstance(r, dict)]
    announce(ctx.ws, {"what": "traffic", "count": len(results),
                      "failed": sum(not r["ok"] for r in results),
                      "threats": sum(len(r["threats"]) for r in results)})
    return {"recorded": len(results), "results": results[-20:]}


# Input: ctx (Context)
# Output: dict[str, Any]
# Processing: Every endpoint's live status, and requests to routes not on the whiteboard.
# Changed by: Claude Code
# Date: 2026-11-18
@operation("api.status", command="/api status", tool="plovane_api_status", group="plovane_endpoint", mutates=False)
def api_status(ctx: Context) -> dict[str, Any]:
    """Shows which endpoints pass and which fail, from the requests reported so far."""
    view = snapshot(ctx.ws)
    rows = []
    for endpoint in sorted(ctx.board.endpoints.values(), key=lambda e: e.id):
        state = view["api"].get(endpoint.id)
        rows.append({"endpoint": endpoint.id, "state": "unknown" if not state else "pass" if state["ok"] else "fail",
                     **({k: state[k] for k in ("status", "count", "fails") if k in state} if state else {})})
    return {"endpoints": rows, "unmatched": view["unmatched"]}


# Input: ctx (Context), endpoint (str), provider (str | None)
# Output: dict[str, Any]
# Processing: Ask an AI why an endpoint's last request failed, using its handler code and the request.
# Changed by: Claude Code
# Date: 2026-11-18
@operation("api.explain", command="/api explain", tool="plovane_api_explain", group="plovane_endpoint",
           mutates=False)
def api_explain(ctx: Context, endpoint: str, provider: str | None = None) -> dict[str, Any]:
    """Explains in plain English why an endpoint's last request failed (asks the AI only now)."""
    found = ctx.board.endpoints.get(endpoint) or next(
        (e for e in ctx.board.endpoints.values() if f"{e.method} {e.path}" == endpoint), None)
    if found is None:
        raise OpError("NOT_FOUND", f"no endpoint {endpoint!r}; /api status lists them")
    state = live_of(ctx.ws)["api"].get(found.id) or {}
    failure = state.get("last_failure")
    if not failure:
        return {"endpoint": found.id, "explanation": "No failed request has been reported for this endpoint."}
    code = ""
    if found.handler:
        block = ctx.board.blocks.get(found.handler)
        path = ctx.path_of(block) if block else found.handler.split(":")[0]
        if path and (ctx.root / path).is_file():
            ctx.require(path, "read")
            code = (ctx.root / path).read_text(encoding="utf-8", errors="replace")[:6000]
    prompt = (f"A request to {found.method} {found.path} failed with status {failure['status']}.\n"
              f"Request body: {failure['body'] or '(none)'}\nError reported: {failure['error'] or '(none)'}\n"
              f"Expected request: {json.dumps(found.request)}\nExpected response: {json.dumps(found.response)}\n\n"
              f"Handler code:\n{code or '(not found)'}\n\n"
              "In at most four short sentences of plain English, say the most likely reason it failed and what to "
              "change. Name the line or field when you can.")
    reply = ctx.ws.llm_result(prompt, "You debug web APIs and explain causes plainly.", provider=provider)
    return {"endpoint": found.id, "explanation": reply["text"].strip(), "usage": reply.get("usage", {})}


# -- Security --------------------------------------------------------------------

# Input: ctx (Context), clear (Any)
# Output: dict[str, Any]
# Processing: The attacks seen in reported requests, newest last, and the weak spots found in code.
# Changed by: Claude Code
# Date: 2026-11-18
@operation("security.events", command="/security", tool="plovane_security", group="plovane_code", mutates=False)
def security(ctx: Context, clear: Any = False) -> dict[str, Any]:
    """Lists attacks seen in requests and weak spots found in the code."""
    live = live_of(ctx.ws)
    view = snapshot(ctx.ws)
    if flag(clear) and not ctx.dry_run:
        live["security"].clear()
        live["attempts"].clear()
        announce(ctx.ws, {"what": "security"})
    return {"events": view["security"], "threats": view["threats"], "weak_spots": live["weak_spots"][:200]}


# Input: ctx (Context), target (str | None)
# Output: dict[str, Any]
# Processing: Look through the project's code for weak spots, line by line, with rules (no AI, no tokens).
# Changed by: Claude Code
# Date: 2026-11-18
@operation("security.scan", command="/security scan", tool="plovane_security_scan", group="plovane_code",
           mutates=False)
def security_scan(ctx: Context, target: str | None = None) -> dict[str, Any]:
    """Looks through the code for weak spots: SQL built from text, eval, shell calls, secrets, raw HTML."""
    folder = (target or "").strip().strip("/")
    folder = "" if folder == "." else folder
    ctx.require(folder or None, "read")
    if folder and not (ctx.root / folder).exists():
        raise OpError("NOT_FOUND", f"{folder} is not in the project")
    files = [folder] if folder and (ctx.root / folder).is_file() else folder_files(ctx.root, folder)
    spots: list[dict[str, Any]] = []
    for rel in files:
        if not rel.endswith(CODE_SUFFIXES):
            continue
        path = ctx.root / rel
        if path.stat().st_size > 1_000_000:
            continue
        for number, line in enumerate(path.read_text(encoding="utf-8", errors="replace").splitlines(), start=1):
            stripped = line.strip()
            if stripped.startswith(("#", "//", "*")) or "environ" in line or "process.env" in line:
                continue
            for kind, severity, why, pattern in WEAK_SPOTS:
                if pattern.search(line):
                    spots.append({"file": rel, "line": number, "kind": kind, "severity": severity, "why": why,
                                  "code": stripped[:160]})
    spots.sort(key=lambda s: (-rank(s["severity"]), s["file"], s["line"]))
    if not ctx.dry_run:
        live = live_of(ctx.ws)
        live["weak_spots"] = [s for s in live["weak_spots"] if not inside(s["file"], folder)] + spots
        live["scanned_at"] = time.time()
        announce(ctx.ws, {"what": "security_scan", "found": len(spots)})
    counts = {level: sum(s["severity"] == level for s in spots) for level in ("high", "medium", "low")}
    return {"scanned": len(files), "found": len(spots), "counts": counts, "weak_spots": spots[:200]}
