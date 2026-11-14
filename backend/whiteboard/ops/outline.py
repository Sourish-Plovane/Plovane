"""AI outline: a file's code grouped into nested blocks by what it does.

``/blocks generate src/payments/billing.py`` asks Claude or DeepSeek to read
the file and answer with a tree of blocks, each naming a part of the code by
what it does ("validate the card", "charge Stripe") and the lines it covers.
Complex parts get blocks inside them, down to ``--depth`` levels. Zooming
into a block on the whiteboard shows the blocks inside it, and a block with
none shows its lines of code.

An AI connected over MCP (Claude Code on a subscription, with no API key)
writes the tree itself and passes it as ``blocks``; no second AI is called.

    blocks = [{"name": "Charge the card", "summary": "...", "lines": [12, 40],
               "children": [{"name": "...", "lines": [14, 20]}, ...]}, ...]

The tree is checked against the file before anything is drawn: line ranges
are clamped to the file and to their parent, and a block that overlaps the
one before it is dropped with a note. Generating again replaces the old AI
blocks of that file; function, class and step blocks are left alone.
"""

from __future__ import annotations

import json
import os
from typing import Any

from backend.whiteboard import codegen
from backend.whiteboard.engine import Context
from backend.whiteboard.model import Block
from backend.whiteboard.ops.project import ensure_folder_block
from backend.whiteboard.registry import OpError, flag, integer, operation

MAX_FILES = 25
"""Files a folder target may hold; more needs a smaller folder."""
MAX_LINES = 4000
"""Longest file sent to the AI."""
MAX_BLOCKS = 200
"""Most blocks one file may get."""
SOURCE = "ai"
"""``meta.source`` of blocks made here, so generating again can replace them."""

SYSTEM = ("You map source code into a zoomable diagram. You reply with JSON only, no prose, "
          "no code fences.")


# Input: path (str), text (str), depth (int)
# Output: str
# Processing: The prompt asking the AI for a nested block outline of one file.
# Changed by: Claude Code
# Date: 2026-11-09
def prompt_for(path: str, text: str, depth: int) -> str:
    numbered = "\n".join(f"{n:>5} | {line}" for n, line in enumerate(text.splitlines(), start=1))
    return (
        f"Here is the file {path}, with line numbers.\n\n{numbered}\n\n"
        "Group the code into blocks by what it does, for someone who wants to understand the file "
        "by zooming into a diagram. Rules:\n"
        "- Top-level blocks follow the file from top to bottom and do not overlap. Imports and "
        "constants can be one block.\n"
        "- 3 to 8 blocks per level. Name each by what it does in 2 to 6 plain words, not by the "
        "function name.\n"
        "- A block longer than about 15 lines that does several things gets children: smaller "
        "blocks inside its line range, following the same rules.\n"
        f"- At most {depth} levels deep. Short or simple code has fewer levels.\n"
        "- summary is one plain-English sentence.\n\n"
        'Reply with exactly: {"summary": "<what the whole file does>", "blocks": '
        '[{"name": "...", "summary": "...", "lines": [start, end], "children": [...]}]}'
    )


# Input: reply (str)
# Output: dict[str, Any]
# Processing: The JSON object in an AI reply, with or without code fences around it.
# Changed by: Claude Code
# Date: 2026-11-09
def parse_reply(reply: str) -> dict[str, Any]:
    start, end = reply.find("{"), reply.rfind("}")
    if start < 0 or end < start:
        raise OpError("LLM_BAD_REPLY", "the AI did not answer with a JSON outline")
    try:
        found = json.loads(reply[start:end + 1])
    except json.JSONDecodeError as exc:
        raise OpError("LLM_BAD_REPLY", f"the AI's outline is not valid JSON ({exc.msg})") from None
    if isinstance(found, list):
        found = {"blocks": found}
    if not isinstance(found, dict) or not isinstance(found.get("blocks"), list):
        raise OpError("LLM_BAD_REPLY", "the AI's outline has no blocks list")
    return found


# Input: value (Any)
# Output: list[dict[str, Any]]
# Processing: A blocks argument as a list, whether it came as JSON text or a list.
# Changed by: Claude Code
# Date: 2026-11-09
def blocks_argument(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError as exc:
            raise OpError("INVALID_ARGS", f"blocks is not valid JSON ({exc.msg})") from None
    if isinstance(value, dict):
        value = value.get("blocks")
    if not isinstance(value, list):
        raise OpError("INVALID_ARGS", 'blocks is a list: [{"name", "summary", "lines": [start, end], "children"}]')
    return value


# Input: nodes (list), low (int), high (int), depth (int), max_depth (int), notes (list[str]), count (list[int])
# Output: list[dict[str, Any]]
# Processing: Keep only well-formed blocks whose lines fit inside their parent, in order, without overlaps.
# Changed by: Claude Code
# Date: 2026-11-09
def clean(nodes: list, low: int, high: int, depth: int, max_depth: int, notes: list[str],
          count: list[int]) -> list[dict[str, Any]]:
    kept: list[dict[str, Any]] = []
    ready = []
    for node in nodes:
        if not isinstance(node, dict) or not str(node.get("name") or "").strip():
            notes.append("skipped a block with no name")
            continue
        lines = node.get("lines")
        try:
            start, end = int(lines[0]), int(lines[1])
        except (TypeError, ValueError, IndexError):
            notes.append(f"skipped {node['name']!r}: lines must be [start, end]")
            continue
        start, end = max(low, min(start, end)), min(high, max(start, end))
        if start > end:
            notes.append(f"skipped {node['name']!r}: its lines are outside {low}-{high}")
            continue
        ready.append((start, end, node))
    last_end = low - 1
    for start, end, node in sorted(ready, key=lambda item: (item[0], -item[1])):
        if start <= last_end:
            notes.append(f"skipped {node['name']!r}: lines {start}-{end} overlap the block before it")
            continue
        if count[0] >= MAX_BLOCKS:
            notes.append(f"stopped at {MAX_BLOCKS} blocks")
            break
        count[0] += 1
        children = node.get("children") or []
        if depth >= max_depth and children:
            notes.append(f"left out blocks inside {node['name']!r}: deeper than --depth {max_depth}")
            children = []
        kept.append({
            "name": " ".join(str(node["name"]).split())[:80],
            "summary": " ".join(str(node.get("summary") or "").split())[:400],
            "lines": [start, end],
            "children": clean(children, start, end, depth + 1, max_depth, notes, count)
            if isinstance(children, list) else [],
        })
        last_end = end
    return kept


# Input: ctx (Context), path (str)
# Output: Block
# Processing: The file's block, made (with its folders) when the file has none yet.
# Changed by: Claude Code
# Date: 2026-11-09
def file_block(ctx: Context, path: str) -> Block:
    owner = ctx.board.block_for_path(path)
    if owner is None:
        owner = ctx.new_block("file", os.path.basename(path), path=path,
                              parent=ensure_folder_block(ctx, os.path.dirname(path)),
                              meta={"language": codegen.language_of(path)})
    return owner


# Input: ctx (Context), owner (Block)
# Output: list[str]
# Processing: Delete the AI blocks a file got last time, innermost first.
# Changed by: Claude Code
# Date: 2026-11-09
def remove_old(ctx: Context, owner: Block) -> list[str]:
    old = [b for b in ctx.board.blocks.values()
           if b.meta.get("source") == SOURCE and b.meta.get("file") == owner.path]
    for block in sorted(old, key=lambda b: -int(b.meta.get("depth", 0))):
        for arrow_id in [a.id for a in ctx.board.arrows.values() if block.id in (a.source, a.target)]:
            del ctx.board.arrows[arrow_id]
        del ctx.board.blocks[block.id]
    return [b.id for b in old]


# Input: ctx (Context), owner (Block), nodes (list), parent (Block), depth (int), by (str), made (list[str])
# Output: None
# Processing: Draw a cleaned outline as nested group blocks under the file.
# Changed by: Claude Code
# Date: 2026-11-09
def draw(ctx: Context, owner: Block, nodes: list[dict[str, Any]], parent: Block, depth: int, by: str,
         made: list[str]) -> None:
    for node in nodes:
        block = ctx.new_block("group", node["name"], layer=4, parent=parent.id,
                              id_hint=f"{owner.id}-{node['name']}",
                              meta={"file": owner.path, "lines": node["lines"], "summary": node["summary"],
                                    "description": node["summary"], "source": SOURCE, "by": by,
                                    "depth": depth})
        made.append(block.id)
        draw(ctx, owner, node["children"], block, depth + 1, by, made)


# Input: ctx (Context), path (str), provider (str | None), depth (int), given (list | None), keep (bool)
# Output: dict[str, Any]
# Processing: Outline one file: ask the AI (or take the given tree), check it, and draw it.
# Changed by: Claude Code
# Date: 2026-11-09
def outline_file(ctx: Context, path: str, provider: str | None, depth: int, given: list | None,
                 keep: bool) -> dict[str, Any]:
    ctx.require(path)
    text = ctx.fs.read(path)
    total = len(text.splitlines())
    if total == 0:
        return {"file": path, "blocks": [], "note": "empty file"}
    summary = ""
    usage: dict[str, Any] = {}
    if given is None:
        if total > MAX_LINES:
            raise OpError("TOO_LARGE", f"{path} has {total} lines; the AI outline reads at most {MAX_LINES}")
        reply = ctx.ws.llm_result(prompt_for(path, text, depth), SYSTEM, provider=provider)
        usage = reply.get("usage") or {}
        found = parse_reply(reply["text"])
        nodes, summary = found["blocks"], str(found.get("summary") or "")
        by = provider or os.environ.get("PLOVANE_LLM_PROVIDER", "anthropic")
    else:
        nodes, by = given, ctx.actor
    notes: list[str] = []
    cleaned = clean(nodes, 1, total, 1, depth, notes, [0])
    if not cleaned:
        raise OpError("LLM_BAD_REPLY", f"no usable blocks for {path}: " + "; ".join(notes[:5] or ["empty outline"]))
    owner = file_block(ctx, path)
    removed = [] if flag(keep) else remove_old(ctx, owner)
    made: list[str] = []
    draw(ctx, owner, cleaned, owner, 1, by, made)
    if summary:
        owner.meta["summary"] = owner.meta["description"] = " ".join(summary.split())[:400]
    owner.meta["outline"] = {"by": by, "blocks": len(made)}
    ctx.notes.extend(f"{path}: {note}" for note in notes[:10])
    return {"file": path, "block": owner.id, "blocks": made, "replaced": removed, "by": by,
            "summary": owner.meta.get("summary", ""), "usage": usage}


# Input: ctx (Context), target (str), provider (str | None), depth (Any), blocks (Any), keep (Any)
# Output: dict[str, Any]
# Processing: Group a file's (or every file in a folder's) code into nested blocks by what it does.
# Changed by: Claude Code
# Date: 2026-11-09
@operation("blocks.generate", command="/blocks generate", tool="plovane_blocks_generate", group="plovane_compile")
def generate(ctx: Context, target: str, provider: str | None = None, depth: Any = 3, blocks: Any = None,
             keep: Any = False) -> dict[str, Any]:
    """Groups a file's code into nested blocks by what it does: pass blocks=[{name, summary, lines: [start, end], children: [...]}] you wrote, or provider=claude or deepseek to have that AI write them."""
    levels = integer(depth, "depth")
    if not 1 <= levels <= 6:
        raise OpError("INVALID_ARGS", "depth is 1 to 6")
    given = None if blocks in (None, "") else blocks_argument(blocks)
    try:
        block = ctx.board.block(target)
        path = (block.path or block.meta.get("file") or "").strip("/")
    except OpError:
        path = target.strip("/")
    if not path or not ctx.fs.exists(path):
        raise OpError("NOT_FOUND", f"no file or folder {target!r}")
    if provider:
        provider = {"claude": "anthropic"}.get(provider.lower(), provider.lower())
    if ctx.fs.is_dir(path):
        if given is not None:
            raise OpError("INVALID_ARGS", "blocks describes one file; give a file, not a folder")
        files = [f for f in ctx.fs.files_under(path) if codegen.language_of(f)]
        if not files:
            raise OpError("NOT_FOUND", f"no source files under {path or 'the project'}")
        if len(files) > MAX_FILES:
            raise OpError("TOO_LARGE", f"{path or 'the project'} has {len(files)} source files; "
                                       f"outline at most {MAX_FILES} at a time (pick a smaller folder)")
    else:
        if codegen.language_of(path) is None:
            raise OpError("UNSUPPORTED", f"{path} is not a supported source file")
        files = [path]
    results, failed = [], []
    for file in files:
        try:
            results.append(outline_file(ctx, file, provider, levels, given, keep))
        except OpError as exc:
            if len(files) == 1 or exc.code == "LLM_UNAVAILABLE":
                raise
            failed.append({"file": file, "error": exc.message})
    return {"target": path, "files": results, "failed": failed,
            "blocks": sum(len(r["blocks"]) for r in results)}
