"""Code: open a block's code, see what changed, explain it in plain English."""

from __future__ import annotations

import shutil
import subprocess
from typing import Any

from whiteboard import codegen
from whiteboard.refactor import symbols
from whiteboard.engine import Context
from whiteboard.registry import OpError, flag, operation


# Input: ctx (Context), target (str)
# Output: tuple[str, int]
# Processing: The file and first line a block or path refers to.
# Changed by: Sourish
# Date: 2026-10-22
def location(ctx: Context, target: str) -> tuple[str, int]:
    try:
        block = ctx.board.block(target)
    except OpError:
        path = target.strip("/")
        if not ctx.fs.exists(path):
            raise
        return path, 1
    ctx.require(block, "read")
    if block.kind in ("file", "folder") and block.path:
        return block.path, 1
    if block.meta.get("file"):
        return block.meta["file"], (block.meta.get("lines") or [1])[0]
    raise OpError("INVALID_ARGS", f"{block.id} has no code behind it")


# Input: ctx (Context), target (str), launch (Any)
# Output: dict[str, Any]
# Processing: A block's code location and content, optionally opened in VS Code.
# Changed by: Sourish
# Date: 2026-10-22
@operation("code.open", command="/open", tool="plovane_open", group="plovane_code", mutates=False)
def open_code(ctx: Context, target: str, launch: Any = False) -> dict[str, Any]:
    """Opens a block's code in the editor."""
    path, line = location(ctx, target)
    if ctx.fs.is_dir(path):
        return {"path": path, "files": ctx.fs.files_under(path)}
    launched = False
    if flag(launch):
        editor = shutil.which("code")
        if editor is None:
            ctx.notes.append("VS Code's `code` command is not on PATH; open the file from the returned path")
        else:
            subprocess.Popen([editor, "-g", f"{ctx.fs.path(path)}:{line}"])
            launched = True
    return {"path": path, "line": line, "content": ctx.fs.read(path), "launched": launched}


# Input: ctx (Context), block (str), since (str | None)
# Output: dict[str, Any]
# Processing: Git diff of a block's file, uncommitted or since a date.
# Changed by: Sourish
# Date: 2026-10-22
@operation("code.diff", command="/diff", tool="plovane_diff", group="plovane_code", mutates=False)
def diff(ctx: Context, block: str, since: str | None = None) -> dict[str, Any]:
    """Shows what changed in a block."""
    path, _ = location(ctx, block)
    if ctx.run(["git", "rev-parse", "--is-inside-work-tree"]).returncode != 0:
        raise OpError("UNAVAILABLE", "the project is not a Git repository")
    base = "HEAD"
    if since:
        found = ctx.run(["git", "rev-list", "-1", f"--before={since}", "HEAD"]).stdout.strip()
        if not found:
            raise OpError("NOT_FOUND", f"no commit before {since!r}")
        base = found
    done = ctx.run(["git", "diff", base, "--", path])
    untracked = ctx.run(["git", "ls-files", "--others", "--exclude-standard", "--", path]).stdout.split()
    return {"path": path, "since": since or "last commit", "base": base, "diff": done.stdout,
            "untracked": untracked}


# Input: ctx (Context), target (str), ai (Any)
# Output: dict[str, Any]
# Processing: Explain a block or file in plain English, by LLM or from its structure.
# Changed by: Sourish
# Date: 2026-10-22
@operation("code.explain", command="/explain", tool="plovane_explain", group="plovane_code", mutates=False)
def explain(ctx: Context, target: str, ai: Any = True) -> dict[str, Any]:
    """Explains a block in plain English."""
    path, _ = location(ctx, target)
    text = ctx.fs.read(path)
    try:
        block = ctx.board.block(target)
    except OpError:
        block = None
    focus = text
    if block is not None and block.kind == "function":
        from whiteboard.ops.blocks import function_unit

        focus = function_unit(ctx, block)[0].code
    if flag(ai):
        try:
            answer = ctx.llm(f"Explain what this code does in plain English, in a short paragraph and then "
                             f"a bullet per function, for someone who does not read code.\n\n```\n{focus[:12000]}\n```",
                             system="You explain code clearly and briefly.")
            return {"target": target, "explanation": answer, "source": "llm"}
        except OpError as exc:
            if exc.code != "LLM_UNAVAILABLE":
                raise
            ctx.notes.append("no LLM key set, so this is a structural summary")
    units = [u for u in symbols(focus, path) if u.kind != "class"]
    lines = [f"{path} is a {codegen.language_of(path) or 'text'} file with {len(units)} function(s)."]
    for unit in units:
        what = unit.docstring.splitlines()[0] if unit.docstring else "no description"
        lines.append(f"- {unit.name}{unit.signature or '()'}: {what}")
    if block is not None:
        outgoing = [a.target for a in ctx.board.arrows.values() if a.source == block.id]
        if outgoing:
            lines.append(f"It connects to: {', '.join(outgoing)}.")
    return {"target": target, "explanation": "\n".join(lines), "source": "structure"}
