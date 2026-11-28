"""Find an app's API routes in its code, and the code that calls them, so API arrows work on real projects.

``/endpoint scan`` reads the project once, with no AI and no network:

* **Routes.** Flask and FastAPI decorators (``@app.route``, ``@bp.get``), ``add_url_rule`` and
  ``routes=[...]`` helpers, with blueprint prefixes (including ``app.config["X_URL_PREFIX"]`` when
  the config file sets it); Express ``app.get('/x')`` and ``router.post(...)``; Next.js
  ``app/**/route.ts`` handlers and ``pages/api`` files.
* **Callers.** ``fetch('/x')``, ``axios.get('/x')`` and similar in JavaScript, and ``requests``,
  ``httpx`` or test-client calls in Python.

Each route becomes an endpoint on the block of the file that handles it, and each caller gets an API
arrow to that block. Requests reported through ``/traffic`` then colour those arrows green or red.
Scanning again updates what it found before; it never touches endpoints you made yourself.
"""

from __future__ import annotations

import ast
import re
from pathlib import PurePosixPath
from typing import Any

from backend.whiteboard.engine import Context
from backend.whiteboard.model import Arrow, Endpoint
from backend.whiteboard.ops import live
from backend.whiteboard.registry import operation

METHODS = ("GET", "POST", "PUT", "PATCH", "DELETE")
ANY = "ANY"
"""Method of a route that answers whatever its view class handles (class-based views, pages/api)."""
SCANNED = "scanned"
"""``meta`` key marking arrows the scan made, so a new scan can replace them."""
MAX_ARROWS = 400
"""Most caller arrows one scan adds; past this the board would be unreadable anyway."""
MAX_BYTES = 400_000
"""Files larger than this are skipped (generated bundles, data)."""
CODE = (".py", ".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs")
SERVER_NAMES = re.compile(r"^(app|router|server|routes?|api_?router|\w+Router|\w+_router|bp|blueprint)$")
CLIENT_NAMES = re.compile(r"^(axios|http|client|api|ky|\$http|request|instance|\w+Client|\w+_client|self\.client)$")


# Input: raw (str)
# Output: str
# Processing: A caller's URL reduced to its path, with ${...} and {..} placeholders as one segment.
# Changed by: Claude Code
# Date: 2026-11-21
def clean_url(raw: str) -> str | None:
    url = re.sub(r"^\$\{[^}]*\}", "", raw.strip())              # `${API}/x` -> /x
    url = re.sub(r"^https?://[^/]+", "", url)
    url = re.sub(r"\$\{[^}]*\}|\{[^}]*\}", "X", url).split("?", 1)[0].split("#", 1)[0]
    if not url.startswith("/") or url.startswith("//") or len(url) > 200:
        return None
    return url.rstrip("/") or "/"


# Input: prefix (str), route (str)
# Output: str
# Processing: Join a blueprint prefix and a route.
# Changed by: Claude Code
# Date: 2026-11-21
def join_route(prefix: str, route: str) -> str:
    joined = "/" + "/".join(p for p in (prefix.strip("/"), route.strip("/")) if p)
    return joined


# Input: files (dict[str, str])
# Output: dict[str, str]
# Processing: Config values that look like URL prefixes (NAME_PREFIX = "/x", API_V1_STR: str = "/api/v1") across the project's Python.
# Changed by: Claude Code
# Date: 2026-11-27
def config_prefixes(files: dict[str, str]) -> dict[str, str]:
    found: dict[str, str] = {}
    for path, text in files.items():
        if path.endswith(".py"):
            for name, value in re.findall(r"^\s*(\w*PREFIX\w*)\s*(?::\s*[\w\[\], ]+)?=\s*['\"]([^'\"]*)['\"]", text, re.M):
                found.setdefault(name, value)
            # Settings like API_V1_STR: str = "/api/v1": an upper-case name holding a path.
            for name, value in re.findall(r"^\s*([A-Z][A-Z0-9_]*)\s*(?::\s*[\w\[\], ]+)?=\s*['\"](/[^'\"]*)['\"]", text, re.M):
                found.setdefault(name, value)
    return found


MOUNT = re.compile(r"(\w+)\.include_router\(\s*(\w+)\s*,[^)]*?\bprefix\s*=\s*([\w.]+|['\"][^'\"]*['\"])")
"""app.include_router(api_router, prefix=settings.API_V1_STR): a router from another file mounted under a prefix."""


# Input: texts (dict[str, str]), config (dict[str, str])
# Output: tuple[str, str] | None
# Processing: The one prefix the app mounts routers from other files under (FastAPI's /api/v1), and the file that mounts them; None when there is not exactly one.
# Changed by: Claude Code
# Date: 2026-11-27
def mount_prefix(texts: dict[str, str], config: dict[str, str]) -> tuple[str, str] | None:
    mounts: set[tuple[str, str]] = set()
    for path, text in texts.items():
        if not path.endswith(".py") or "include_router" not in text:
            continue
        for _owner, name, raw in MOUNT.findall(text):
            if re.search(rf"^\s*{re.escape(name)}\s*=\s*APIRouter\(", text, re.M):
                continue  # a router made in this same file: python_routes already joins its prefix
            value = raw.strip("'\"") if raw[:1] in "'\"" else config.get(raw.split(".")[-1])
            if value and value.startswith("/"):
                mounts.add((value, path))
    prefixes = {value for value, _ in mounts}
    if len(prefixes) != 1:
        return None
    value = prefixes.pop()
    return value, sorted(path for v, path in mounts if v == value)[0]


# Input: node (ast.AST | None), config (dict[str, str])
# Output: str | None
# Processing: A literal string, or app.config["X"] / config.X looked up in the project's config.
# Changed by: Claude Code
# Date: 2026-11-21
def literal(node: ast.AST | None, config: dict[str, str]) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.Subscript) and isinstance(node.slice, ast.Constant):
        return config.get(str(node.slice.value))
    if isinstance(node, ast.Attribute):
        return config.get(node.attr)
    return None


# Input: call (ast.Call), name (str)
# Output: ast.AST | None
# Processing: One keyword argument of a call.
# Changed by: Claude Code
# Date: 2026-11-21
def keyword(call: ast.Call, name: str) -> ast.AST | None:
    return next((k.value for k in call.keywords if k.arg == name), None)


# Input: node (ast.AST | None)
# Output: list[str]
# Processing: The HTTP methods in methods=[...], or none.
# Changed by: Claude Code
# Date: 2026-11-21
def methods_of(node: ast.AST | None) -> list[str]:
    if isinstance(node, (ast.List, ast.Tuple, ast.Set)):
        return [e.value.upper() for e in node.elts if isinstance(e, ast.Constant) and isinstance(e.value, str)
                and e.value.upper() in METHODS]
    return []


# Input: node (ast.AST)
# Output: str
# Processing: The receiver of a method call as text: app, bp, self.client.
# Changed by: Claude Code
# Date: 2026-11-21
def receiver(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return f"{receiver(node.value)}.{node.attr}"
    return ""


PY_HINT = re.compile(r"@[\w.]+\.(route|get|post|put|patch|delete)\(|add_url_rule|register_blueprint|include_router"
                     r"|\broutes\s*=|(requests|httpx|client|session)\.(get|post|put|patch|delete)\(")
"""Text a Python file must contain before it is worth parsing for routes or calls."""


# Input: path (str), text (str), config (dict[str, str])
# Output: tuple[list[tuple[str, str]], list[tuple[str, str]]]
# Processing: Routes ((method, path)) and calls ((method, url)) in one Python file.
# Changed by: Claude Code
# Date: 2026-11-21
def python_routes(path: str, text: str, config: dict[str, str]) -> tuple[list[tuple[str, str]], list[tuple[str, str]]]:
    if not PY_HINT.search(text):  # most files declare and call no routes: skip parsing them
        return [], []
    try:
        nodes = list(ast.walk(ast.parse(text)))
    except (SyntaxError, ValueError):
        return [], []
    prefixes: dict[str, str] = {}
    for node in nodes:  # bp = Blueprint(..., url_prefix=...) and app.register_blueprint(bp, url_prefix=...)
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Call) and len(node.targets) == 1 \
                and isinstance(node.targets[0], ast.Name) and receiver(node.value.func).split(".")[-1] in ("Blueprint", "APIRouter"):
            prefix = literal(keyword(node.value, "url_prefix") or keyword(node.value, "prefix"), config)
            prefixes[node.targets[0].id] = prefix or ""
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) \
                and node.func.attr in ("register_blueprint", "include_router") and node.args:
            prefix = literal(keyword(node, "url_prefix") or keyword(node, "prefix"), config)
            if prefix is not None and isinstance(node.args[0], ast.Name):
                prefixes[node.args[0].id] = join_route(prefix, prefixes.get(node.args[0].id, ""))
    routes: list[tuple[str, str]] = []
    calls: list[tuple[str, str]] = []
    for node in nodes:
        if not isinstance(node, ast.Call):
            continue
        func, first = node.func, (node.args[0] if node.args else None)
        owner = receiver(func.value) if isinstance(func, ast.Attribute) else ""
        base = prefixes.get(owner.split(".")[0], "")
        attr = func.attr if isinstance(func, ast.Attribute) else ""
        route = literal(first, config)
        if attr == "route" and route is not None:
            for method in methods_of(keyword(node, "methods")) or ["GET"]:
                routes.append((method, join_route(base, route)))
        elif attr.upper() in METHODS and route is not None and route.startswith("/"):
            if CLIENT_NAMES.match(owner) or owner.split(".")[0] in ("requests", "httpx") or "client" in owner.lower():
                calls.append((attr.upper(), route))
            elif owner in prefixes or SERVER_NAMES.match(owner):
                routes.append((attr.upper(), join_route(base, route)))
        elif attr == "add_url_rule" and route is not None:
            for method in methods_of(keyword(node, "methods")) or [ANY]:
                routes.append((method, join_route(base, route)))
        else:
            listed = keyword(node, "routes")
            if isinstance(listed, (ast.List, ast.Tuple)) and node.args:
                owner = receiver(node.args[0])
                for item in listed.elts:
                    value = literal(item, config)
                    if value is not None:
                        for method in methods_of(keyword(node, "methods")) or [ANY]:
                            routes.append((method, join_route(prefixes.get(owner, ""), value)))
    return routes, calls


JS_SERVER = re.compile(r"\b([\w$]+)\.(get|post|put|patch|delete|all)\(\s*(['\"`])(/[^'\"`]*)\3")
JS_FETCH = re.compile(r"\bfetch\(\s*(['\"`])(.+?)\1")
JS_METHOD = re.compile(r"method\s*:\s*['\"](\w+)['\"]", re.I)
JS_OBJECT_CALL = re.compile(r"\.(get|post|put|patch|delete)\s*(?:<[^()]*?>)?\s*\(\s*\{[^()]{0,400}?\burl\s*:\s*(['\"`])(/[^'\"`]*)\2",
                            re.S | re.I)
"""Generated API clients: client.get<...>({ ..., url: '/api/v1/items/' })."""
JS_REQUEST = re.compile(r"\bmethod\s*:\s*['\"](\w+)['\"]\s*,\s*url\s*:\s*(['\"`])(/[^'\"`]*)\2", re.I)
"""request({ method: 'GET', url: '/api/v1/items/' })."""
NEXT_EXPORT = re.compile(r"export\s+(?:async\s+)?(?:function\s+|const\s+)(GET|POST|PUT|PATCH|DELETE)\b")


# Input: path (str), text (str)
# Output: tuple[list[tuple[str, str]], list[tuple[str, str]]]
# Processing: Routes and calls in one JavaScript or TypeScript file (Express, Next.js, fetch, axios, generated API clients).
# Changed by: Claude Code
# Date: 2026-11-27
def js_routes(path: str, text: str) -> tuple[list[tuple[str, str]], list[tuple[str, str]]]:
    routes: list[tuple[str, str]] = []
    calls: list[tuple[str, str]] = []
    parts = PurePosixPath(path).parts
    if "app" in parts and PurePosixPath(path).stem == "route":
        at = len(parts) - 1 - parts[::-1].index("app")
        segments = [p for p in parts[at + 1:-1] if not (p.startswith("(") and p.endswith(")")) and not p.startswith("@")]
        for method in NEXT_EXPORT.findall(text) or [ANY]:
            routes.append((method, "/" + "/".join(segments)))
    elif "pages" in parts and "api" in parts and parts.index("api") == parts.index("pages") + 1:
        rest = list(parts[parts.index("api") + 1:])
        rest[-1] = PurePosixPath(rest[-1]).stem
        if rest[-1] == "index":
            rest.pop()
        routes.append((ANY, "/api/" + "/".join(rest) if rest else "/api"))
    for match in JS_SERVER.finditer(text):
        owner, verb, url = match.group(1), match.group(2).upper(), match.group(4)
        if CLIENT_NAMES.match(owner):
            calls.append((verb, url))
        elif SERVER_NAMES.match(owner):
            routes.append((ANY if verb == "ALL" else verb, url))
    for match in JS_FETCH.finditer(text):
        nearby = JS_METHOD.search(text, match.end(), match.end() + 300)
        calls.append(((nearby.group(1).upper() if nearby else "GET"), match.group(2)))
    for match in JS_OBJECT_CALL.finditer(text):
        calls.append((match.group(1).upper(), match.group(3)))
    for match in JS_REQUEST.finditer(text):
        calls.append((match.group(1).upper(), match.group(3)))
    return routes, calls


# Input: board (Board)
# Output: dict[str, str]
# Processing: Block id for every file and folder path on the board.
# Changed by: Claude Code
# Date: 2026-11-21
def blocks_by_path(board: Any) -> dict[str, str]:
    return {b.path.strip("/"): b.id for b in board.blocks.values() if b.path and b.kind in ("file", "folder")}


# Input: index (dict[str, str]), path (str)
# Output: str | None
# Processing: The block showing a file: its own, else its nearest folder's.
# Changed by: Claude Code
# Date: 2026-11-21
def block_of(index: dict[str, str], path: str) -> str | None:
    current = PurePosixPath(path)
    while str(current) not in (".", ""):
        if str(current) in index:
            return index[str(current)]
        current = current.parent
    return None


# Input: ctx (Context), under (str)
# Output: dict[str, Any]
# Processing: Find routes and their callers in the code (routers mounted under one app-wide prefix get it); add endpoints and API arrows for them, dropping routes an earlier scan found that are gone.
# Changed by: Claude Code
# Date: 2026-11-27
@operation("endpoint.scan", command="/endpoint scan", tool="plovane_endpoint_scan", group="plovane_endpoint")
def scan(ctx: Context, under: str = "") -> dict[str, Any]:
    """Finds the app's API routes and the code calling them; adds endpoints and API arrows (no AI)."""
    base = under.strip("/")
    ctx.require(None)
    texts: dict[str, str] = {}
    for path in ctx.fs.files_under(base):
        if path.endswith(CODE):
            try:
                text = ctx.fs.read(path)
            except Exception:  # noqa: BLE001  (binary or unreadable files are simply skipped)
                continue
            if len(text) <= MAX_BYTES:
                texts[path] = text
    config = config_prefixes(texts)
    mounted = mount_prefix(texts, config)
    index = blocks_by_path(ctx.board)
    found: dict[str, Endpoint] = {}
    callers: list[tuple[str, str, str]] = []
    unplaced = 0
    for path, text in texts.items():
        routes, calls = python_routes(path, text, config) if path.endswith(".py") else js_routes(path, text)
        if mounted and path.endswith(".py") and path != mounted[1]:
            routes = [(m, r if r.startswith(mounted[0] + "/") or r == mounted[0] else join_route(mounted[0], r))
                      for m, r in routes]
        owner = block_of(index, path)
        unplaced += 0 if owner else len(routes)
        for method, route in routes:
            if owner:
                key = f"{owner}:{method} {route}"
                found.setdefault(key, Endpoint(id=key, service=owner, method=method, path=route, status="built",
                                               handler=path))
        callers += [(path, method, url) for method, url in calls]
    for key in [k for k, e in ctx.board.endpoints.items()  # routes an earlier scan found that are gone (or moved)
                if e.status == "built" and e.handler and k not in found and k.startswith(f"{e.service}:")
                and (not base or e.handler.startswith(base.rstrip("/") + "/"))]:
        del ctx.board.endpoints[key]
    for key, endpoint in found.items():
        if key not in ctx.board.endpoints:
            ctx.board.endpoints[key] = endpoint
    for arrow_id in [a.id for a in ctx.board.arrows.values() if a.meta.get(SCANNED)]:
        del ctx.board.arrows[arrow_id]
    links: set[tuple[str, str, str]] = set()
    for path, method, raw in callers:
        url, source = clean_url(raw), block_of(index, path)
        if not url or not source:
            continue
        for endpoint in found.values():
            if endpoint.method not in (method, ANY) or not live.route_pattern(endpoint.path).match(url):
                continue
            if endpoint.service != source and len(links) < MAX_ARROWS:
                links.add((source, endpoint.service, endpoint.id))
    for source, target, endpoint_id in sorted(links):
        arrow_id = ctx.board.new_id(f"{source}-to-{target}", pool=ctx.board.arrows)
        ctx.board.arrows[arrow_id] = Arrow(id=arrow_id, source=source, target=target, type="api",
                                           endpoint=endpoint_id, meta={SCANNED: True})
    if unplaced and not found:
        ctx.notes.append(f"found {unplaced} routes, but their files are not on the board yet: "
                         "import the project first (/project import), then scan again")
    elif not found:
        ctx.notes.append("no API routes found (looked for Flask, FastAPI, Express and Next.js routes)")
    return {"routes": len(found), "arrows": len(links),
            "endpoints": sorted(found)[:50], "files": len(texts)}
