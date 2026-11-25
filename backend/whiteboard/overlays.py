"""Overlays: information switched on top of the whiteboard, like filters on a map.

Zoom levels (``board.layers``) decide which blocks are on screen; overlays
decide what extra information is drawn on them. Overlays are computed by
premade analyzers from the code and the board, never by an AI, so showing or
hiding one costs no tokens. Handbook section 21 defines the model.

Each analyzer finds results on the blocks it is about (a file, a function,
a service). Results then roll up to every block that contains them, so a
problem deep in the code is still counted on its folder and service.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from pathlib import Path
from typing import Any

import json

from backend.whiteboard import codegen
from backend.whiteboard.store import STATE_DIR

PREMADE_PREFIX = "plovane.overlays."
MAX_ITEMS = 20
AUTH_MARKERS = re.compile(
    r"login_required|requires_auth|auth_required|jwt_required|permission_required|"
    r"Authorization|authenticate|current_user|verify_token|check_token|bearer|isAuthenticated|passport\.",
    re.IGNORECASE)


# Input: none
# Output: dict[str, dict[str, Any]]
# Processing: The overlays every board starts with.
# Changed by: Claude Code
# Date: 2026-11-22
def default_overlays() -> dict[str, dict[str, Any]]:
    everywhere = {"1": "count", "2": "count", "3": "mark", "4": "mark", "5": "detail"}
    return {
        "cyber_security": {"label": "Cyber security", "source": "group"},
        "code_vulnerabilities": {
            "label": "Vulnerabilities in code", "source": "premade", "parent": "cyber_security",
            "engine_ref": "plovane.overlays.code_vulnerabilities@1", "channel": "border", "priority": 100,
            "appearance": dict(everywhere), "style": {"color": "red", "badge": "risky code"}},
        "leaked_secrets": {
            "label": "Leaked secrets", "source": "premade", "parent": "cyber_security",
            "engine_ref": "plovane.overlays.leaked_secrets@1", "channel": "badge", "priority": 100,
            "appearance": dict(everywhere), "style": {"color": "red", "badge": "secret"}},
        "unauthenticated_endpoints": {
            "label": "Endpoints with no login check", "source": "premade", "parent": "cyber_security",
            "engine_ref": "plovane.overlays.unauthenticated_endpoints@1", "channel": "badge", "priority": 90,
            "appearance": {"1": "mark", "2": "count", "3": "mark"}, "style": {"color": "orange", "badge": "no auth"}},
        "api_endpoints": {
            "label": "API endpoints", "source": "premade",
            "engine_ref": "plovane.overlays.api_endpoints@1", "channel": "port", "priority": 50,
            "appearance": {"1": "detail", "2": "count", "3": "mark"}, "style": {"color": "blue"}},
        "test_health": {
            "label": "Test health", "source": "premade",
            "engine_ref": "plovane.overlays.test_health@1", "channel": "fill", "priority": 60,
            "appearance": {"2": "count", "3": "mark", "4": "mark"}},
        "agent_access": {
            "label": "Agent access", "source": "premade",
            "engine_ref": "plovane.overlays.agent_access@1", "channel": "chip", "priority": 40,
            "appearance": {"1": "mark", "2": "mark", "3": "mark", "4": "mark", "5": "mark"}},
        "library_code": {
            "label": "Verified library code", "source": "premade",
            "engine_ref": "plovane.overlays.library_code@1", "channel": "icon", "priority": 30,
            "appearance": {"2": "count", "3": "mark", "4": "mark"}, "style": {"icon": "✓"}},
        "log_errors": {
            "label": "Errors in logs", "source": "premade",
            "engine_ref": "plovane.overlays.log_errors@1", "channel": "badge", "priority": 95,
            "appearance": dict(everywhere), "style": {"color": "red", "badge": "errors"}},
    }


# Input: none
# Output: dict[str, dict[str, Any]]
# Processing: The roles every board starts with: which overlays each kind of person sees first.
# Changed by: Claude Code
# Date: 2026-11-22
def default_roles() -> dict[str, dict[str, Any]]:
    return {
        "developer": {"label": "Developer", "overlays": ["api_endpoints", "test_health", "agent_access",
                                                         "library_code"], "default_level": 3},
        "cyber": {"label": "Cyber security", "overlays": ["cyber_security", "api_endpoints"], "default_level": 1},
        "reviewer": {"label": "Reviewer", "overlays": ["test_health", "library_code"], "default_level": 2},
    }


# Input: overlays (dict), ids (list[str])
# Output: list[str]
# Processing: Expand group ids into the drawing overlays inside them, at any depth, keeping order.
# Changed by: Claude Code
# Date: 2026-11-22
def expand(overlays: dict[str, dict[str, Any]], ids: list[str]) -> list[str]:
    out: list[str] = []
    for oid in ids:
        spec = overlays.get(oid)
        if spec is None:
            continue
        if spec.get("source") == "group":
            children = [k for k, v in overlays.items() if v.get("parent") == oid]
            out += [c for c in expand(overlays, children) if c not in out]
        elif oid not in out:
            out.append(oid)
    return out


# Input: board (Board), actor (str)
# Output: dict[str, Any]
# Processing: An actor's view: its role (the board's first role when unset), additions and removals.
# Changed by: Claude Code
# Date: 2026-11-22
def view_of(board: Any, actor: str) -> dict[str, Any]:
    view = dict(board.views.get(actor) or {})
    if view.get("base_role") not in board.roles:
        view["base_role"] = "developer" if "developer" in board.roles else next(iter(board.roles), None)
    view.setdefault("overlays_add", [])
    view.setdefault("overlays_remove", [])
    return view


# Input: board (Board), actor (str)
# Output: list[str]
# Processing: The drawing overlays switched on for an actor: its role's, plus additions, minus removals.
# Changed by: Claude Code
# Date: 2026-11-22
def active(board: Any, actor: str) -> list[str]:
    view = view_of(board, actor)
    role = board.roles.get(view["base_role"] or "", {})
    on = expand(board.overlays, list(role.get("overlays", [])) + list(view["overlays_add"]))
    off = set(expand(board.overlays, list(view["overlays_remove"])))
    return [oid for oid in on if oid not in off]


# -- Analyzers --------------------------------------------------------------------
# Each takes (workspace, board) and returns {block_id: [item, ...]} for the blocks
# it is directly about. An item is a dict with at least "message".


class ScanCache:
    """File scans keyed by path, reused while the file's size and modification time stay the same."""

    # Input: none
    # Output: None
    # Processing: Start empty.
    # Changed by: Claude Code
    # Date: 2026-11-22
    def __init__(self) -> None:
        self._entries: dict[str, tuple[tuple[int, int], list[dict[str, Any]]]] = {}

    # Input: root (Path), rel (str), scan (Callable[[str, str], list])
    # Output: list[dict[str, Any]]
    # Processing: The scan of one file, from the cache when the file has not changed.
    # Changed by: Claude Code
    # Date: 2026-11-22
    def get(self, root: Path, rel: str, scan: Callable[[str, str], list[dict[str, Any]]]) -> list[dict[str, Any]]:
        path = root / rel
        try:
            stat = path.stat()
        except OSError:
            return []
        stamp = (stat.st_mtime_ns, stat.st_size)
        cached = self._entries.get(rel)
        if cached and cached[0] == stamp:
            return cached[1]
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return []
        found = scan(rel, text)
        self._entries[rel] = (stamp, found)
        return found


# Input: board (Board)
# Output: list[Any]
# Processing: File blocks that stand for code files Plovane can read.
# Changed by: Claude Code
# Date: 2026-11-22
def code_files(board: Any) -> list[Any]:
    return [b for b in board.blocks.values() if b.kind == "file" and b.path and codegen.language_of(b.path)]


# Input: board (Board), file (str), line (int)
# Output: str | None
# Processing: The innermost function or class block on the board that holds a line of a file.
# Changed by: Claude Code
# Date: 2026-11-22
def block_at_line(board: Any, file: str, line: int) -> str | None:
    best, size = None, None
    for block in board.blocks.values():
        lines = block.meta.get("lines")
        if block.meta.get("file") != file or not lines or len(lines) != 2:
            continue
        if lines[0] <= line <= lines[1] and (size is None or lines[1] - lines[0] < size):
            best, size = block.id, lines[1] - lines[0]
    return best


# Input: ws (Workspace), board (Board), keep (Callable[[dict], bool])
# Output: dict[str, list[dict[str, Any]]]
# Processing: Security scan findings that pass keep, placed on their file block and the function holding the line.
# Changed by: Claude Code
# Date: 2026-11-22
def scan_findings(ws: Any, board: Any, keep: Callable[[dict[str, Any]], bool]) -> dict[str, list[dict[str, Any]]]:
    from backend.whiteboard.ops.health import scan_text

    found: dict[str, list[dict[str, Any]]] = {}
    for block in code_files(board):
        for item in ws.overlay_cache.get(ws.root, block.path, scan_text):
            if not keep(item):
                continue
            found.setdefault(block.id, []).append(item)
            inner = block_at_line(board, block.path, item["line"])
            if inner and inner != block.id:
                found.setdefault(inner, []).append(item)
    return found


# Input: item (dict)
# Output: bool
# Processing: Whether a scan finding is a leaked secret.
# Changed by: Claude Code
# Date: 2026-11-22
def is_secret(item: dict[str, Any]) -> bool:
    return item["message"].endswith(" in source")


# Input: ws (Workspace), board (Board)
# Output: dict[str, list[dict[str, Any]]]
# Processing: Risky code (eval, shell commands, disabled TLS checks, unsafe loading) by block.
# Changed by: Claude Code
# Date: 2026-11-22
def code_vulnerabilities(ws: Any, board: Any) -> dict[str, list[dict[str, Any]]]:
    return scan_findings(ws, board, lambda item: not is_secret(item) and item["severity"] in ("high", "medium"))


# Input: ws (Workspace), board (Board)
# Output: dict[str, list[dict[str, Any]]]
# Processing: Keys, tokens and passwords written into source, by block (location and rule only, never the value).
# Changed by: Claude Code
# Date: 2026-11-22
def leaked_secrets(ws: Any, board: Any) -> dict[str, list[dict[str, Any]]]:
    return scan_findings(ws, board, is_secret)


# Input: ws (Workspace), board (Board)
# Output: dict[str, list[dict[str, Any]]]
# Processing: Endpoints whose handler code shows no sign of checking who is calling, on the service and handler file.
# Changed by: Claude Code
# Date: 2026-11-22
def unauthenticated_endpoints(ws: Any, board: Any) -> dict[str, list[dict[str, Any]]]:
    found: dict[str, list[dict[str, Any]]] = {}
    for endpoint in board.endpoints.values():
        if endpoint.status == "external" or not endpoint.handler:
            continue
        handler = ws.root / endpoint.handler
        text = handler.read_text(encoding="utf-8", errors="replace") if handler.is_file() else ""
        if AUTH_MARKERS.search(text):
            continue
        item = {"message": f"{endpoint.method} {endpoint.path} has no login check in {endpoint.handler}",
                "file": endpoint.handler, "endpoint": endpoint.id, "severity": "medium"}
        if endpoint.service in board.blocks:
            found.setdefault(endpoint.service, []).append(item)
        owner = board.block_for_path(endpoint.handler)
        if owner is not None:
            found.setdefault(owner.id, []).append(item)
    return found


# Input: ws (Workspace), board (Board)
# Output: dict[str, list[dict[str, Any]]]
# Processing: Each service's endpoints, and the file that handles each one.
# Changed by: Claude Code
# Date: 2026-11-22
def api_endpoints(ws: Any, board: Any) -> dict[str, list[dict[str, Any]]]:
    found: dict[str, list[dict[str, Any]]] = {}
    for endpoint in sorted(board.endpoints.values(), key=lambda e: e.id):
        item = {"message": f"{endpoint.method} {endpoint.path}", "endpoint": endpoint.id, "status": endpoint.status}
        if endpoint.service in board.blocks:
            found.setdefault(endpoint.service, []).append(item)
        owner = board.block_for_path(endpoint.handler) if endpoint.handler else None
        if owner is not None and owner.id != endpoint.service:
            found.setdefault(owner.id, []).append(item)
    return found


# Input: path (str)
# Output: bool
# Processing: Whether a path is itself a test file.
# Changed by: Claude Code
# Date: 2026-11-22
def is_test_file(path: str) -> bool:
    name = path.rsplit("/", 1)[-1]
    return (name.startswith("test_") or name.endswith(("_test.py", ".test.js", ".spec.js", ".test.ts", ".spec.ts"))
            or "/tests/" in f"/{path}" or "/__tests__/" in f"/{path}")


# Input: ws (Workspace), board (Board)
# Output: dict[str, list[dict[str, Any]]]
# Processing: Each code file's test state: failing (from /health --run-tests), tested (a test file names it) or untested.
# Changed by: Claude Code
# Date: 2026-11-22
def test_health(ws: Any, board: Any) -> dict[str, list[dict[str, Any]]]:
    files = code_files(board)
    tests = [b.path for b in files if is_test_file(b.path)]
    test_text = {}
    for path in tests:
        target = ws.root / path
        test_text[path] = target.read_text(encoding="utf-8", errors="replace") if target.is_file() else ""
    found: dict[str, list[dict[str, Any]]] = {}
    for block in files:
        if is_test_file(block.path):
            continue
        stem = Path(block.path).stem
        health = str(block.meta.get("health", ""))
        named = [t for t in tests if Path(t).stem in (f"test_{stem}", f"{stem}_test", f"{stem}.test", f"{stem}.spec")]
        imported = [t for t in tests if t not in named and re.search(rf"\b{re.escape(stem)}\b", test_text[t])]
        if "tests failing" in health:
            item = {"message": health, "status": "failing", "severity": "high"}
        elif named or imported:
            item = {"message": f"tested by {', '.join((named + imported)[:3])}", "status": "tested"}
        else:
            item = {"message": "no test file covers this file", "status": "untested", "severity": "low"}
        found[block.id] = [item]
    return found


# Input: ws (Workspace), board (Board)
# Output: dict[str, list[dict[str, Any]]]
# Processing: The agents that can reach each block, by the engine's own access rules.
# Changed by: Claude Code
# Date: 2026-11-22
def agent_access(ws: Any, board: Any) -> dict[str, list[dict[str, Any]]]:
    return {block: [{"message": f"{agent} can {'read and change' if mode == 'read-write' else 'read'} this",
                     "agent": agent, "mode": mode} for agent, mode in sorted(agents.items())]
            for block, agents in ws.access_map().items()}


# Input: ws (Workspace), board (Board)
# Output: dict[str, list[dict[str, Any]]]
# Processing: Blocks whose code was inserted from the verified library, with its entry and licence.
# Changed by: Claude Code
# Date: 2026-11-22
def library_code(ws: Any, board: Any) -> dict[str, list[dict[str, Any]]]:
    return {b.id: [{"message": f"verified library code: {b.meta['library']}"
                    + (f" ({b.meta['license']})" if b.meta.get("license") else ""), "entry": b.meta["library"]}]
            for b in board.blocks.values() if b.meta.get("library")}


# Input: ws (Workspace), board (Board)
# Output: dict[str, list[dict[str, Any]]]
# Processing: Files a log blames for errors (from .plovane/log_errors.json, written by /logs scan), with their count and worst lines.
# Changed by: Claude Code
# Date: 2026-11-24
def log_errors(ws: Any, board: Any) -> dict[str, list[dict[str, Any]]]:
    path = ws.root / STATE_DIR / "log_errors.json"
    if not path.is_file():
        return {}
    try:
        record = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    source = record.get("source") or "the log"
    out: dict[str, list[dict[str, Any]]] = {}
    for rel, entry in (record.get("files") or {}).items():
        block = board.block_for_path(rel)
        if not block:
            continue
        count = int(entry.get("count", 0))
        lines = entry.get("lines") or {}
        worst_line, worst_count = (None, 0)
        for line_no, hits in lines.items():
            if hits > worst_count:
                worst_line, worst_count = line_no, hits
        message = (f"{count} error{'s' if count != 1 else ''} in {source}"
                   + (f"; worst at line {worst_line}" if worst_line else ""))
        samples = entry.get("samples") or []
        out[block.id] = [{"message": message, "count": count, "line": int(worst_line) if worst_line else None,
                          "source": source, "samples": samples[:3]}]
    return out


ANALYZERS: dict[str, Callable[[Any, Any], dict[str, list[dict[str, Any]]]]] = {
    "code_vulnerabilities": code_vulnerabilities,
    "leaked_secrets": leaked_secrets,
    "unauthenticated_endpoints": unauthenticated_endpoints,
    "api_endpoints": api_endpoints,
    "test_health": test_health,
    "agent_access": agent_access,
    "library_code": library_code,
    "log_errors": log_errors,
}
NO_ROLL_UP = {"agent_access"}
"""Overlays whose results already cover every block they apply to, so counting them on parents would mislead."""


# Input: spec (dict)
# Output: str | None
# Processing: The analyzer a premade overlay names, if Plovane has it.
# Changed by: Claude Code
# Date: 2026-11-22
def analyzer_name(spec: dict[str, Any]) -> str | None:
    ref = str(spec.get("engine_ref", ""))
    if spec.get("source") != "premade" or not ref.startswith(PREMADE_PREFIX):
        return None
    name = ref[len(PREMADE_PREFIX):].split("@", 1)[0]
    return name if name in ANALYZERS else None


# Input: board (Board), direct (dict[str, list])
# Output: dict[str, dict[str, Any]]
# Processing: Per-block results with each block's own items and the count rolled up from everything inside it.
# Changed by: Claude Code
# Date: 2026-11-22
def roll_up(board: Any, direct: dict[str, list[dict[str, Any]]], *, climb: bool = True) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for block_id, items in direct.items():
        if block_id not in board.blocks:
            continue
        entry = out.setdefault(block_id, {"count": 0, "items": [], "own": True})
        entry["own"] = True
        entry["items"] = items[:MAX_ITEMS]
    if not climb:
        for entry in out.values():
            entry["count"] = len(entry["items"])
        return out
    seen_per_block: dict[str, set] = {}
    for block_id, items in direct.items():
        if block_id not in board.blocks:
            continue
        keys = {_key(item, block_id) for item in items}
        current, guard = block_id, set()
        while current and current in board.blocks and current not in guard:
            guard.add(current)
            seen_per_block.setdefault(current, set()).update(keys)
            current = board.blocks[current].parent
    for block_id, keys in seen_per_block.items():
        entry = out.setdefault(block_id, {"count": 0, "items": [], "own": False})
        entry["count"] = len(keys)
    return out


STATUS_RANK = {"failing": 3, "untested": 2, "tested": 1}


# Input: board (Board), direct (dict[str, list])
# Output: dict[str, dict[str, Any]]
# Processing: Results for a fill overlay: each block's own state, and on containers the worst state inside them
#             with the number of blocks inside that need attention.
# Changed by: Claude Code
# Date: 2026-11-22
def fill_results(board: Any, direct: dict[str, list[dict[str, Any]]]) -> dict[str, dict[str, Any]]:
    out = roll_up(board, {b: items for b, items in direct.items()
                          if items and items[0].get("status") != "tested"})
    for entry in out.values():
        entry["status"] = None
    for block_id, items in direct.items():
        if block_id not in board.blocks or not items:
            continue
        state = items[0].get("status")
        entry = out.setdefault(block_id, {"count": 0, "items": [], "own": True})
        entry.update(own=True, items=items[:MAX_ITEMS], status=state)
        current, guard = board.blocks[block_id].parent, {block_id}
        while current and current in board.blocks and current not in guard:
            guard.add(current)
            parent = out.setdefault(current, {"count": 0, "items": [], "own": False, "status": None})
            if STATUS_RANK.get(state, 0) > STATUS_RANK.get(parent.get("status"), 0):
                parent["status"] = state
            current = board.blocks[current].parent
    return out


# Input: item (dict), origin (str)
# Output: tuple
# Processing: The place a result is about (file and line, an endpoint, or the block it was found on), so one
#             place counts once on a folder even when it is on a function and its file or matches two rules.
# Changed by: Claude Code
# Date: 2026-11-22
def _key(item: dict[str, Any], origin: str) -> tuple:
    if item.get("endpoint"):
        return ("endpoint", item["endpoint"])
    return (item.get("file") or origin, item.get("line"))


# Input: ws (Workspace), spec (dict), oid (str)
# Output: dict[str, list[dict[str, Any]]]
# Processing: Read the items a proposed overlay saved to .plovane/overlays/<oid>.json; silently empty if missing.
# Changed by: Claude Code
# Date: 2026-11-25
def custom_items(ws: Any, spec: dict[str, Any], oid: str) -> dict[str, list[dict[str, Any]]]:
    path = ws.root / STATE_DIR / "overlays" / f"{oid}.json"
    if not path.is_file():
        return {}
    try:
        record = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    items = record.get("items") if isinstance(record.get("items"), dict) else {}
    return {bid: notes for bid, notes in items.items() if isinstance(notes, list)}


# Input: ws (Workspace), board (Board), actor (str)
# Output: dict[str, Any]
# Processing: Everything the whiteboard needs to draw overlays for an actor: definitions, roles, view and results.
# Changed by: Claude Code
# Date: 2026-11-22
def overlay_state(ws: Any, board: Any, actor: str) -> dict[str, Any]:
    on = active(board, actor)
    results: dict[str, dict[str, dict[str, Any]]] = {}
    status: dict[str, str] = {}
    for oid in on:
        spec = board.overlays.get(oid, {})
        name = analyzer_name(spec)
        if name is None:
            if spec.get("source") == "custom":
                direct = custom_items(ws, spec, oid)
                results[oid] = roll_up(board, direct)
            else:
                status[oid] = "Plovane has no analyzer for this overlay"
            continue
        direct = ANALYZERS[name](ws, board)
        if spec.get("channel") == "fill":
            results[oid] = fill_results(board, direct)
        else:
            results[oid] = roll_up(board, direct, climb=name not in NO_ROLL_UP)
    return {"defs": board.overlays, "roles": board.roles, "view": view_of(board, actor), "active": on,
            "results": results, "unavailable": status}
