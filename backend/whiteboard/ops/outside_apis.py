"""Find the outside APIs the code calls, add external blocks and arrows for them.

``/outside scan`` reads the project once, with no AI and no network. It looks
for calls to full ``https://host/...`` URLs from common HTTP clients:

* Python: ``requests.get/post/...``, ``httpx.get/post/...``, ``urllib.request.urlopen``.
* JavaScript / TypeScript: ``fetch('https://...')``, ``axios.get/post/...``,
  ``ky.get/post/...``, ``got('https://...')``.

Each distinct host becomes an external block (named after the host, e.g.
``api.stripe.com`` → ``stripe``), and one arrow goes from the file that called
it to that block. Scanning again replaces arrows and blocks the previous scan
made; anything you added by hand stays.
"""

from __future__ import annotations

import ast
import re
from typing import Any
from urllib.parse import urlparse

from backend.whiteboard.engine import Context
from backend.whiteboard.model import LAYER_FOR_KIND, Arrow, Block
from backend.whiteboard.ops.routes import blocks_by_path, block_of
from backend.whiteboard.registry import operation

SCANNED_API = "outside_scanned"
"""``meta`` key marking external blocks and arrows the scan made, so a rescan can replace them."""
MAX_BYTES = 400_000
CODE = (".py", ".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs")
PY_CLIENTS = frozenset({"requests", "httpx", "urllib", "aiohttp", "r"})
PY_METHODS = frozenset({"get", "post", "put", "patch", "delete", "head", "options", "request", "urlopen"})
JS_CALL = re.compile(r"\b(?:fetch|got|ky(?:\.(?:get|post|put|patch|delete|head))?|axios(?:\.(?:get|post|put|patch|delete|head|request))?)\s*\(\s*(['\"`])(https?://[^'\"`\s]+)\1")
# Hosts we treat as "your own machine", never an outside API.
LOCAL = frozenset({"localhost", "127.0.0.1", "0.0.0.0", "::1"})


# Input: url (str)
# Output: str | None
# Processing: The clean host of an absolute http(s) URL (no port), or None when it's local or not a URL.
# Changed by: Claude Code
# Date: 2026-11-24
def host_of(url: str) -> str | None:
    try:
        parsed = urlparse(url.strip())
    except ValueError:
        return None
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        return None
    host = parsed.hostname.lower().rstrip(".")
    if host in LOCAL or host.endswith(".local"):
        return None
    return host


# Input: host (str)
# Output: str
# Processing: A short, friendly block name for an API host (api.stripe.com → stripe, api.openai.com → openai).
# Changed by: Claude Code
# Date: 2026-11-24
GENERIC = {"api", "www", "hooks", "webhook", "webhooks", "edge", "svc", "services", "cdn", "assets", "data", "us", "eu"}


def pretty_name(host: str) -> str:
    parts = host.split(".")
    while len(parts) > 1 and parts[0] in GENERIC:
        parts = parts[1:]
    return parts[0] if parts else host


# Input: text (str)
# Output: set[str]
# Processing: Every full http(s) URL used as a string literal inside a Python HTTP-client call.
# Changed by: Claude Code
# Date: 2026-11-24
def python_urls(text: str) -> set[str]:
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return set()
    out: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not node.args:
            continue
        func = node.func
        name = None
        if isinstance(func, ast.Attribute):
            value = func.value
            root = value.id if isinstance(value, ast.Name) else (value.attr if isinstance(value, ast.Attribute) else None)
            if root in PY_CLIENTS and func.attr in PY_METHODS:
                name = func.attr
        elif isinstance(func, ast.Name) and func.id in PY_CLIENTS:
            name = func.id
        if not name:
            continue
        first = node.args[0]
        if isinstance(first, ast.Constant) and isinstance(first.value, str):
            out.add(first.value)
    return out


# Input: text (str)
# Output: set[str]
# Processing: Every full http(s) URL passed to fetch/axios/ky/got in JavaScript or TypeScript code.
# Changed by: Claude Code
# Date: 2026-11-24
def js_urls(text: str) -> set[str]:
    return {match.group(2) for match in JS_CALL.finditer(text)}


# Input: ctx (Context), under (str)
# Output: dict[str, Any]
# Processing: Scan the project for calls to outside APIs; add an external block per host and an arrow from the caller.
# Changed by: Claude Code
# Date: 2026-11-24
@operation("outside.scan", command="/outside scan", tool="plovane_outside_scan", group="plovane_endpoint")
def scan(ctx: Context, under: str = "") -> dict[str, Any]:
    """Finds the outside APIs the code calls; adds external blocks and arrows (no AI)."""
    base = under.strip("/")
    ctx.require(None)
    texts: dict[str, str] = {}
    for path in ctx.fs.files_under(base):
        if path.endswith(CODE):
            try:
                text = ctx.fs.read(path)
            except Exception:  # noqa: BLE001
                continue
            if len(text) <= MAX_BYTES:
                texts[path] = text

    index = blocks_by_path(ctx.board)
    # host -> { "sources": set[block_id], "urls": set[str] }
    hits: dict[str, dict[str, Any]] = {}
    for path, text in texts.items():
        source = block_of(index, path)
        if not source:
            continue
        urls = python_urls(text) if path.endswith(".py") else js_urls(text)
        for url in urls:
            host = host_of(url)
            if not host:
                continue
            entry = hits.setdefault(host, {"sources": set(), "urls": set()})
            entry["sources"].add(source)
            entry["urls"].add(url)

    # Replace what the previous scan added, keep anything a person made.
    for bid in [b.id for b in list(ctx.board.blocks.values()) if b.meta.get(SCANNED_API) and b.kind == "external"]:
        if bid not in [h.get("block") for h in hits.values() if h.get("block")]:
            del ctx.board.blocks[bid]
    for aid in [a.id for a in list(ctx.board.arrows.values()) if a.meta.get(SCANNED_API)]:
        del ctx.board.arrows[aid]

    # Reuse an external block with the same name a person made, when it exists; otherwise create one.
    by_name = {b.name: b.id for b in ctx.board.blocks.values() if b.kind == "external"}
    added_blocks, added_arrows = [], []
    for host, data in sorted(hits.items()):
        name = pretty_name(host)
        if name in by_name:
            block_id = by_name[name]
        else:
            block_id = ctx.board.new_id(name, pool=ctx.board.blocks)
            x = 1400 + 220 * (len(added_blocks) % 2)
            y = 120 + 160 * (len(added_blocks) // 2)
            ctx.board.blocks[block_id] = Block(
                id=block_id, kind="external", name=name, layer=LAYER_FOR_KIND["external"],
                x=x, y=y, w=180, h=90, meta={SCANNED_API: True, "host": host, "urls": sorted(data["urls"])[:10]},
            )
            by_name[name] = block_id
            added_blocks.append(block_id)
        for source in sorted(data["sources"]):
            if source == block_id:
                continue
            arrow_id = ctx.board.new_id(f"{source}-to-{block_id}", pool=ctx.board.arrows)
            ctx.board.arrows[arrow_id] = Arrow(
                id=arrow_id, source=source, target=block_id, type="external",
                meta={SCANNED_API: True, "host": host},
            )
            added_arrows.append(arrow_id)

    if not hits:
        ctx.notes.append("no outside API calls found (looked for requests, httpx, fetch, axios and friends)")
    return {
        "hosts": len(hits),
        "blocks_added": added_blocks,
        "arrows_added": len(added_arrows),
        "files_scanned": len(texts),
        "found": {host: {"urls": sorted(data["urls"])[:5], "sources": sorted(data["sources"])}
                  for host, data in hits.items()},
    }
