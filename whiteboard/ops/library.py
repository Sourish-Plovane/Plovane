"""Library: the verified helper library, through the B06 models.

``/rank`` searches it, ``/insert`` copies an entry's exact tested code into
the project (with its licence and source), ``/suggest`` runs the reusability
model over a file, and ``/save`` queues a block for the library. Admission
itself happens in the verification pipeline (B12 to B14), not here.
"""

from __future__ import annotations

import json
import os
from typing import Any

from ml.ingest.extract import extract
from ml.ingest.find_helpers import find_helpers
from whiteboard import codegen, sandbox
from whiteboard.refactor import symbols
from whiteboard.engine import Context
from whiteboard.model import Block
from whiteboard.ops.project import ensure_folder_block
from whiteboard.registry import OpError, integer, operation
from whiteboard.store import now

EXTENSION = {"python": "py", "javascript": "js", "typescript": "ts", "cpp": "cpp"}


# Input: ctx (Context), target (str)
# Output: tuple[str, str, Block | None]
# Processing: The code a block or path stands for, its file, and the block.
# Changed by: Sourish
# Date: 2026-10-22
def code_of(ctx: Context, target: str) -> tuple[str, str, Block | None]:
    try:
        block = ctx.board.block(target)
    except OpError:
        block = None
    if block is None:
        path = target.strip("/")
        return ctx.fs.read(path), path, ctx.board.block_for_path(path)
    ctx.require(block, "read")
    if block.kind == "file" and block.path:
        return ctx.fs.read(block.path), block.path, block
    if block.kind == "function":
        from whiteboard.ops.blocks import function_unit

        unit, _, file = function_unit(ctx, block)
        return unit.code, file, block
    raise OpError("INVALID_ARGS", f"{block.id} is a {block.kind}; give a file or function block")


# Input: ctx (Context), query (str), k (Any), language (str | None), category (str | None)
# Output: dict[str, Any]
# Processing: Search the verified library and return ranked slim cards.
# Changed by: Sourish
# Date: 2026-10-22
@operation("library.rank", command="/rank", tool="plovane_recommend", group="plovane_library", mutates=False)
def rank(ctx: Context, query: str, k: Any = 5, language: str | None = None,
         category: str | None = None) -> dict[str, Any]:
    """Searches the verified library for a task and returns ranked slim cards."""
    engine = ctx.library()
    count = integer(k, "k")
    wanted = count * 3 if category else count
    found = engine.search(query, language=language, k=wanted)["data"]
    cards = found["recommendations"]
    if category:
        cards = [c for c in cards if engine.by_id.get(c["block_id"]) and
                 engine.by_id[c["block_id"]].category == category][:count]
    return {"query": query, "query_id": found["query_id"], "recommendations": cards,
            "scores_calibrated": found.get("cutoff_enforced", False)}


# Input: ctx (Context), entry (str), into (str)
# Output: dict[str, Any]
# Processing: Copy a library entry's exact code into the project as a block.
# Changed by: Sourish
# Date: 2026-10-22
@operation("library.insert", command="/insert", tool="plovane_insert", group="plovane_library")
def insert(ctx: Context, entry: str, into: str) -> dict[str, Any]:
    """Inserts a library entry as a block, copying the exact tested code."""
    found = ctx.library().get(entry)
    if "error" in found:
        raise OpError("NOT_FOUND", f"{found['error']}; similar: {found.get('did_you_mean', [])}")
    try:
        container = ctx.board.block(into)
        destination = container.path or ""
    except OpError:
        container, destination = None, into.strip("/")
    language = found["language"]
    c = codegen.comment(language)
    source = found.get("source_permalink") or found.get("source_url") or ""
    credit = f"{c} From the Plovane library: {found['block_id']}\n{c} Licence: {found['license']}. Source: {source}\n"
    if destination and ctx.fs.exists(destination) and not ctx.fs.is_dir(destination):
        # Into an existing file: append the function.
        ctx.require(destination)
        if codegen.language_of(destination) != language:
            raise OpError("CONFLICT", f"{entry} is {language}; {destination} is not")
        text = ctx.fs.read(destination)
        if any(u.name == found["name"] for u in symbols(text, destination)):
            raise OpError("CONFLICT", f"{destination} already defines {found['name']}")
        ctx.fs.write(destination, codegen.append_block(text, credit + found["code"]))
        owner = ctx.board.block_for_path(destination)
        block = ctx.new_block("function", found["name"], parent=owner.id if owner else None, symbol=found["name"],
                              meta={"file": destination, "library": found["block_id"], "license": found["license"]})
        return {"block": block.id, "file": destination, "entry": found["block_id"]}
    path = f"{destination}/{found['name']}.{EXTENSION.get(language, 'txt')}".lstrip("/")
    ctx.require(path)
    if ctx.fs.exists(path):
        raise OpError("CONFLICT", f"{path} already exists")
    ctx.fs.write(path, credit + "\n" + found["code"].rstrip() + "\n")
    parent = ensure_folder_block(ctx, os.path.dirname(path))
    block = ctx.new_block("file", os.path.basename(path), parent=parent, path=path,
                          meta={"language": language, "library": found["block_id"], "license": found["license"]})
    return {"block": block.id, "file": path, "entry": found["block_id"], "license": found["license"]}


# Input: ctx (Context), category (str | None), language (str | None), limit (Any)
# Output: dict[str, Any]
# Processing: The verified entries in a category, as a palette.
# Changed by: Sourish
# Date: 2026-10-22
@operation("library.palette", command="/palette", tool="plovane_palette", group="plovane_library", mutates=False)
def palette(ctx: Context, category: str | None = None, language: str | None = None, limit: Any = 50) -> dict[str, Any]:
    """Opens a palette of verified blocks for a category."""
    engine = ctx.library()
    entries = [e for e in engine.entries
               if (category is None or e.category == category) and (language is None or e.language == language.lower())]
    if not entries and category:
        raise OpError("NOT_FOUND", f"no entries in {category!r}; categories: {sorted(engine.categories()['categories'])}")
    return {"category": category, "total": len(entries), "entries": [
        {"block_id": e.id, "name": e.name, "language": e.language, "signature": e.signature,
         "description": e.description, "license": e.license} for e in entries[: integer(limit, "limit")]]}


# Input: ctx (Context), target (str)
# Output: dict[str, Any]
# Processing: Run the reusability model over a block or file's functions.
# Changed by: Sourish
# Date: 2026-10-22
@operation("library.suggest", command="/suggest", tool="plovane_suggest", group="plovane_library", mutates=False)
def suggest(ctx: Context, target: str) -> dict[str, Any]:
    """Runs the reusability model on a block or file."""
    from ml.reuse.suggest import ReusabilitySuggester
    from ml.serving.engine import unit_as_entry

    code, file, _ = code_of(ctx, target)
    engine = ctx.library()
    suggester = ReusabilitySuggester(library=engine.library_index)
    units = [u for u in extract(code, file, library_rules=False) if not u.is_template]
    if not units:
        return {"target": target, "functions": []}
    vectors = engine.embedding_model.encode_entries([unit_as_entry(u) for u in units])
    standalone = {h["name"] for h in find_helpers(code, file)["helpers"]}
    return {"target": target, "functions": [
        {"name": u.name, "standalone": u.name in standalone} | suggester.judge(u, v).as_dict()
        for u, v in zip(units, vectors)
    ]}


# Input: ctx (Context), block (str), private (Any), public (Any)
# Output: dict[str, Any]
# Processing: Check a block (standalone, not a duplicate, tests pass) and queue it for the library.
# Changed by: Sourish
# Date: 2026-10-22
@operation("library.save", command="/save", tool="plovane_save", group="plovane_library")
def save(ctx: Context, block: str, private: Any = False, public: Any = False) -> dict[str, Any]:
    """Saves a block to the library after naming, tests, and verification."""
    from whiteboard.registry import flag

    if flag(private) and flag(public):
        raise OpError("INVALID_ARGS", "choose --private or --public")
    code, file, owner = code_of(ctx, block)
    language = codegen.language_of(file)
    checks: dict[str, Any] = {}
    helpers = find_helpers(code, file)
    checks["standalone"] = bool(helpers["helpers"]) and not helpers["near_helpers"]
    if helpers["near_helpers"]:
        checks["needs"] = {h["name"]: h["needs"] for h in helpers["near_helpers"]}
    checked = ctx.library().check_file(code, file)
    duplicates = [f for f in checked.get("functions", []) if f["verdict"] == "in_library"]
    checks["not_duplicate"] = not duplicates
    if duplicates:
        checks["duplicates"] = {f["name"]: f["best_match"]["block_id"] for f in duplicates}
    tests = sandbox.run_affected(ctx.root, {file: ctx.fs.read(file)})
    checks["tests"] = tests["passed"]
    if tests["passed"] is None:
        checks["tests_note"] = "no tests cover this code; the library only admits tested code"
    elif tests["passed"] is False:
        checks["tests_output"] = tests["output"][-1500:]
    ok = checks["standalone"] and checks["not_duplicate"] and tests["passed"] is True
    if not ok:
        return {"saved": False, "checks": checks}
    submission = {"submitted_at": now(), "visibility": "public" if flag(public) else "private",
                  "language": language, "file": file, "block": owner.id if owner else None,
                  "functions": [h["name"] for h in helpers["helpers"]], "code": code,
                  "status": "pending_verification"}
    ctx.ws.store.ensure()
    with ctx.ws.store.submissions.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(submission) + "\n")
    ctx.notes.append("queued for verification (B12-B14); it joins the library once verified")
    return {"saved": True, "status": "pending_verification", "checks": checks,
            "visibility": submission["visibility"]}


# Input: ctx (Context), block (str)
# Output: dict[str, Any]
# Processing: The library entries a block uses: inserted ones and matching code.
# Changed by: Sourish
# Date: 2026-10-22
@operation("library.cite", command="/cite", tool="plovane_cite", group="plovane_library", mutates=False)
def cite(ctx: Context, block: str) -> dict[str, Any]:
    """Shows which library entries a block uses."""
    target = ctx.board.block(block)
    ctx.require(target, "read")
    inserted = [{"block": b.id, "entry": b.meta["library"], "license": b.meta.get("license")}
                for b in [target, *ctx.board.descendants(target.id)] if b.meta.get("library")]
    matching = []
    file = target.path if target.kind == "file" else target.meta.get("file")
    if file and ctx.fs.exists(file) and codegen.language_of(file):
        checked = ctx.library().check_file(ctx.fs.read(file), file)
        matching = [{"function": f["name"], "entry": f["best_match"]["block_id"],
                     "similarity": f["best_match"]["similarity"], "license": f["best_match"]["license"]}
                    for f in checked.get("functions", []) if f["verdict"] == "in_library"]
    return {"block": target.id, "inserted": inserted, "matching_library_code": matching}
