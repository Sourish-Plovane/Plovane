"""English blocks and compile: code shown as plain-English steps, and back.

``/blocks view`` turns a file's functions into step blocks: one sentence per
statement, at the detail level asked for. Editing a step's sentence marks it
changed; ``/compile blocks-to-code`` asks the LLM to rewrite only those lines,
shows the diff and runs the affected tests in a sandbox; ``/compile apply``
writes it. Nothing reaches the project before a person has seen the diff.

Python is read through its syntax tree. Other languages get one step per
line of the function body, which is cruder; ``--ai`` asks the LLM to word
the steps instead.
"""

from __future__ import annotations

import ast
import difflib
import json
import re
import textwrap
from typing import Any

from whiteboard import codegen, sandbox
from whiteboard.engine import Context
from whiteboard.model import Block
from whiteboard.ops.blocks import symbol_blocks
from whiteboard.ops.project import ensure_folder_block
from whiteboard.refactor import definition, symbols
from whiteboard.registry import OpError, flag, integer, operation

MAX_SHOWN = 70
"""Characters of an expression quoted inside a sentence before it is cut."""


# Input: node (ast.AST)
# Output: str
# Processing: An expression's source, shortened for a sentence.
# Changed by: Sourish
# Date: 2026-10-22
def short(node: ast.AST) -> str:
    text = ast.unparse(node)
    return text if len(text) <= MAX_SHOWN else text[: MAX_SHOWN - 1] + "…"


# Input: node (ast.stmt)
# Output: str
# Processing: One Python statement as a plain-English sentence.
# Changed by: Sourish
# Date: 2026-10-22
def sentence(node: ast.stmt) -> str:
    if isinstance(node, ast.Assign):
        return f"Set {', '.join(short(t) for t in node.targets)} to {short(node.value)}"
    if isinstance(node, ast.AnnAssign):
        return f"Set {short(node.target)} to {short(node.value)}" if node.value else f"Declare {short(node.target)}"
    if isinstance(node, ast.AugAssign):
        if isinstance(node.op, ast.Add):
            return f"Add {short(node.value)} to {short(node.target)}"
        if isinstance(node.op, ast.Sub):
            return f"Subtract {short(node.value)} from {short(node.target)}"
        return f"Update {short(node.target)} by {short(node.value)}"
    if isinstance(node, ast.Return):
        return f"Return {short(node.value)}" if node.value is not None else "Return"
    if isinstance(node, ast.If):
        return f"If {short(node.test)}" + (", otherwise run the alternative" if node.orelse else "")
    if isinstance(node, (ast.For, ast.AsyncFor)):
        return f"For each {short(node.target)} in {short(node.iter)}"
    if isinstance(node, ast.While):
        return f"Repeat while {short(node.test)}"
    if isinstance(node, ast.Try):
        caught = [short(h.type) for h in node.handlers if h.type is not None] or ["any error"]
        return f"Try the following, handling {', '.join(caught)}"
    if isinstance(node, (ast.With, ast.AsyncWith)):
        return f"Using {', '.join(short(i.context_expr) for i in node.items)}"
    if isinstance(node, ast.Raise):
        return f"Raise {short(node.exc)}" if node.exc is not None else "Re-raise the current error"
    if isinstance(node, ast.Assert):
        return f"Check that {short(node.test)}"
    if isinstance(node, (ast.Import, ast.ImportFrom)):
        return f"Import {', '.join(a.name for a in node.names)}"
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        return f"Define helper {node.name}"
    if isinstance(node, ast.Pass):
        return "Do nothing"
    if isinstance(node, (ast.Break, ast.Continue)):
        return "Stop the loop" if isinstance(node, ast.Break) else "Skip to the next item"
    if isinstance(node, ast.Expr) and isinstance(node.value, ast.Call):
        return f"Call {short(node.value)}"
    if isinstance(node, ast.Expr) and isinstance(node.value, (ast.Await, ast.Yield, ast.YieldFrom)):
        return f"{type(node.value).__name__} {short(node.value.value) if node.value.value else ''}".strip()
    return f"Run {short(node)}"


# Input: function (ast.FunctionDef | ast.AsyncFunctionDef), detail (int)
# Output: list[dict[str, Any]]
# Processing: Steps for a Python function's body, nested up to the detail level.
# Changed by: Sourish
# Date: 2026-10-22
def python_steps(function: ast.FunctionDef | ast.AsyncFunctionDef, detail: int) -> list[dict[str, Any]]:
    steps: list[dict[str, Any]] = []

    # Input: body (list[ast.stmt]), depth (int)
    # Output: None
    # Processing: Add one step per statement, recursing into compound bodies.
    # Changed by: Sourish
    # Date: 2026-10-22
    def walk(body: list[ast.stmt], depth: int) -> None:
        for node in body:
            if depth == 0 and node is body[0] and isinstance(node, ast.Expr) and \
                    isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
                continue  # the docstring
            steps.append({"english": sentence(node), "lines": [node.lineno, node.end_lineno], "depth": depth})
            if detail >= 3:
                for field in ("body", "orelse", "finalbody"):
                    inner = getattr(node, field, None)
                    if isinstance(inner, list) and inner and isinstance(inner[0], ast.stmt):
                        walk(inner, depth + 1)
                for handler in getattr(node, "handlers", []) or []:
                    walk(handler.body, depth + 1)

    walk(function.body, 0)
    return steps


# Input: lines (list[str]), start (int), end (int)
# Output: list[dict[str, Any]]
# Processing: One step per meaningful line of a non-Python function body.
# Changed by: Sourish
# Date: 2026-10-22
def line_steps(lines: list[str], start: int, end: int) -> list[dict[str, Any]]:
    steps = []
    for number in range(start + 1, end):
        text = lines[number - 1].strip()
        if text and text not in ("{", "}", "};", "});") and not text.startswith(("//", "/*", "*")):
            steps.append({"english": f"Run: {text.rstrip('{').strip()}", "lines": [number, number], "depth": 0})
    return steps


# Input: ctx (Context), steps (list[dict]), code (str), language (str)
# Output: None
# Processing: Reword steps with the LLM, keeping the heuristic text on any failure.
# Changed by: Sourish
# Date: 2026-10-22
def reword(ctx: Context, steps: list[dict[str, Any]], code: str, language: str) -> None:
    lines = code.splitlines()
    numbered = "\n".join(f"{i + 1}. lines {s['lines'][0]}-{s['lines'][1]}: "
                         + " / ".join(lines[s['lines'][0] - 1:s['lines'][1]]).strip() for i, s in enumerate(steps))
    reply = ctx.llm(
        f"Here are {len(steps)} pieces of a {language} file. Describe what each does in one short plain-English "
        f"sentence for a non-programmer. Reply with only a JSON array of {len(steps)} strings.\n\n{numbered}",
        system="You explain code in plain English. You reply with JSON only.",
    )
    try:
        sentences = json.loads(reply[reply.index("["): reply.rindex("]") + 1])
    except ValueError:
        ctx.notes.append("the LLM's wording could not be read; showing the built-in wording")
        return
    if len(sentences) == len(steps):
        for step, text in zip(steps, sentences):
            step["english"] = str(text).strip()


# Input: ctx (Context), file (str), detail (Any), ai (Any), discard (Any)
# Output: dict[str, Any]
# Processing: Show a code file as plain-English step blocks.
# Changed by: Sourish
# Date: 2026-10-22
@operation("file.blocks", command="/blocks view", tool="plovane_file_blocks", group="plovane_compile")
def view(ctx: Context, file: str, detail: Any = 2, ai: Any = False, discard: Any = False) -> dict[str, Any]:
    """Shows a code file as plain-English step blocks at the chosen detail level."""
    path = file.strip("/")
    ctx.require(path, "read")
    level = integer(detail, "detail")
    if level not in (1, 2, 3):
        raise OpError("INVALID_ARGS", "detail is 1 (functions), 2 (statements) or 3 (nested statements)")
    text = ctx.fs.read(path)
    language = codegen.language_of(path)
    if language is None:
        raise OpError("UNSUPPORTED", f"{path} is not a supported source file")
    owner = ctx.board.block_for_path(path)
    if owner is None:
        owner = ctx.new_block("file", path.rsplit("/", 1)[-1], path=path,
                              parent=ensure_folder_block(ctx, path.rsplit("/", 1)[0] if "/" in path else ""),
                              meta={"language": language})
    old_steps = [b for b in ctx.board.blocks.values() if b.kind == "step" and b.meta.get("file") == path]
    pending = [b.id for b in old_steps if b.meta.get("changed")]
    if pending and not flag(discard):
        raise OpError("CONFLICT", f"steps {pending} have edits not compiled yet; /compile them, or pass --discard")
    for step in old_steps:
        del ctx.board.blocks[step.id]
    lines = text.splitlines()
    blocks = symbol_blocks(ctx, owner)
    shown = []
    for unit in symbols(text, path):
        if unit.kind == "class":
            continue  # a class is the container; its methods carry the steps
        function = blocks[unit.qualname]
        if level == 1:
            steps = [{"english": (unit.docstring.splitlines()[0] if unit.docstring else
                                  f"{unit.name}{unit.signature or '()'}"),
                      "lines": [unit.start_line, unit.end_line], "depth": 0}]
        elif language == "python":
            node = definition(text, unit.qualname)
            steps = python_steps(node, level) if node else []
        else:
            steps = line_steps(lines, unit.start_line, unit.end_line)
        if flag(ai) and steps:
            reword(ctx, steps, text, language)
        for index, step in enumerate(steps, start=1):
            block = ctx.new_block("step", step["english"][:120], parent=function.id, symbol=unit.qualname,
                                  id_hint=f"{function.id}-step-{index}",
                                  meta={"file": path, "function": unit.qualname, "lines": step["lines"],
                                        "english": step["english"], "original": step["english"],
                                        "depth": step["depth"], "detail": level, "changed": False})
            block.x += 20 * step["depth"]
            shown.append({"block": block.id, "function": unit.qualname, "english": step["english"],
                          "lines": step["lines"], "depth": step["depth"]})
    return {"file": path, "detail": level, "steps": shown}


# Input: ctx (Context), block (str), text (str)
# Output: dict[str, Any]
# Processing: Rewrite a step's English and, when an LLM is available, preview the new code.
# Changed by: Sourish
# Date: 2026-10-22
@operation("block.edit_text", command="/block edit", tool="plovane_block_edit_text", group="plovane_compile")
def edit_text(ctx: Context, block: str, text: str) -> dict[str, Any]:
    """Rewrites an English block; the AI regenerates the code underneath as a reviewable diff."""
    step = ctx.board.block(block)
    if step.kind != "step":
        raise OpError("INVALID_ARGS", f"{step.id} is a {step.kind}; only step blocks have English to edit")
    ctx.require(step)
    step.meta["english"], step.meta["changed"] = text.strip(), True
    step.name, step.status = text.strip()[:120], "changed"
    result: dict[str, Any] = {"block": step.id, "english": text.strip()}
    try:
        result["compile"] = ctx.execute("compile", {"direction": "blocks-to-code", "target": step.meta["file"]})
    except OpError as exc:
        if exc.code != "LLM_UNAVAILABLE":
            raise
        ctx.notes.append(f"saved; no code preview because the LLM is unavailable ({exc.message})")
    return result


# Input: text (str)
# Output: str
# Processing: Strip Markdown code fences from an LLM reply.
# Changed by: Sourish
# Date: 2026-10-22
def unfence(text: str) -> str:
    match = re.search(r"```[\w+-]*\n(.*?)```", text, re.S)
    return (match.group(1) if match else text).strip("\n")


# Input: ctx (Context), target (str), proposed (dict[str, str]), kind (str), steps (list[str])
# Output: dict[str, Any]
# Processing: Store a reviewable compile: the diff, the tests' result and the new text.
# Changed by: Sourish
# Date: 2026-10-22
def record_compile(ctx: Context, target: str, proposed: dict[str, str], kind: str, steps: list[str]) -> dict[str, Any]:
    base = {path: ctx.fs.read(path) if ctx.fs.exists(path) else "" for path in proposed}
    diff = "".join(
        "".join(difflib.unified_diff(base[p].splitlines(keepends=True), proposed[p].splitlines(keepends=True),
                                     fromfile=f"a/{p}", tofile=f"b/{p}"))
        for p in proposed
    )
    tests = sandbox.run_affected(ctx.root, proposed)
    compile_id = f"c_{ctx.counter('compile')}"
    ctx.board.compiles[compile_id] = {"id": compile_id, "kind": kind, "target": target, "base": base,
                                      "files": proposed, "diff": diff, "tests": tests, "steps": steps}
    return {"compile": compile_id, "diff": diff, "tests": tests,
            "next": f"/compile apply {compile_id}" if tests["passed"] is not False else
            "the tests fail with this change; edit the steps and compile again"}


# Input: ctx (Context), direction (str), target (str), preview (Any)
# Output: dict[str, Any]
# Processing: Compile changed steps to code (as a reviewable diff), or code to steps.
# Changed by: Sourish
# Date: 2026-10-22
@operation("compile", command="/compile", tool="plovane_compile", group="plovane_compile",
           confirm=lambda args: not flag(args.get("preview", True)))
def compile_(ctx: Context, direction: str, target: str, preview: Any = True) -> dict[str, Any]:
    """Compiles changed blocks to code (or code to blocks), shows a diff, and runs affected tests."""
    path = target.strip("/")
    if direction == "code-to-blocks":
        detail = next((b.meta.get("detail") for b in ctx.board.blocks.values()
                       if b.kind == "step" and b.meta.get("file") == path), 2)
        return ctx.execute("file.blocks", {"file": path, "detail": detail})
    if direction != "blocks-to-code":
        raise OpError("INVALID_ARGS", "direction is blocks-to-code or code-to-blocks")
    ctx.require(path)
    changed = sorted((b for b in ctx.board.blocks.values()
                      if b.kind == "step" and b.meta.get("file") == path and b.meta.get("changed")),
                     key=lambda b: -b.meta["lines"][0])
    if not changed:
        raise OpError("NOTHING_TO_COMPILE", f"no step in {path} has been edited")
    language = codegen.language_of(path)
    lines = ctx.fs.read(path).splitlines(keepends=True)
    for step in changed:
        start, end = step.meta["lines"]
        old = "".join(lines[start - 1:end])
        indent = re.match(r"\s*", old).group(0)
        reply = ctx.llm(
            f"Language: {language}\nThese lines belong to the function {step.meta['function']} in {path}:\n"
            f"```\n{old}```\nThey currently do: {step.meta['original']}\n"
            f"Rewrite them so they do: {step.meta['english']}\n"
            "Use only names that exist in the surrounding code. Reply with only the replacement lines.",
            system="You rewrite a few lines of code exactly as asked. Reply with code only, no explanation.",
        )
        new = textwrap.indent(textwrap.dedent(unfence(reply)), indent)
        lines[start - 1:end] = [new if new.endswith("\n") else new + "\n"]
    result = record_compile(ctx, path, {path: "".join(lines)}, "english", [s.id for s in changed])
    if not flag(preview):
        if result["tests"]["passed"] is False:
            raise OpError("TESTS_FAILED", "the affected tests fail with this change; nothing was written\n"
                          + result["tests"]["output"][-1500:])
        return result | {"applied": ctx.execute("compile.apply", {"compile": result["compile"]})}
    return result


# Input: ctx (Context), compile (str)
# Output: dict[str, Any]
# Processing: Write a previewed compile to the project once its tests pass.
# Changed by: Sourish
# Date: 2026-10-22
@operation("compile.apply", command="/compile apply", tool="plovane_compile_apply", group="plovane_compile",
           confirm=True)
def apply(ctx: Context, compile: str) -> dict[str, Any]:
    """Applies a previewed compile after tests pass."""
    record = ctx.board.compiles.get(compile)
    if record is None:
        raise OpError("NOT_FOUND", f"no compile {compile!r}; /compile ... --preview makes one")
    if record["tests"]["passed"] is False:
        raise OpError("TESTS_FAILED", f"{compile} fails its tests; it cannot be applied")
    for path, text in record["files"].items():
        ctx.require(path)
        current = ctx.fs.read(path) if ctx.fs.exists(path) else ""
        if current != record["base"][path]:
            raise OpError("CONFLICT", f"{path} changed after {compile} was previewed; compile again")
        ctx.fs.write(path, text)
    del ctx.board.compiles[compile]
    regenerated = None
    if record["kind"] in ("english", "replace"):
        detail = next((b.meta.get("detail") for b in ctx.board.blocks.values()
                       if b.kind == "step" and b.meta.get("file") == record["target"]), None)
        if detail:
            regenerated = ctx.execute("file.blocks", {"file": record["target"], "detail": detail, "discard": True})
    if record["tests"]["passed"] is None:
        ctx.notes.append("no tests cover this file, so the change is untested")
    return {"applied": compile, "files": sorted(record["files"]), "tests": record["tests"]["passed"],
            "steps": regenerated["steps"] if regenerated else None}


# Input: value (Any)
# Output: str
# Processing: A parameter value as a source-code literal.
# Changed by: Sourish
# Date: 2026-10-22
def literal(value: Any) -> str:
    if isinstance(value, (int, float, bool)) or value is None:
        return repr(value)
    text = str(value)
    if re.fullmatch(r"-?\d+(\.\d+)?", text):
        return text
    if text in ("True", "False", "None", "true", "false", "null"):
        return text
    return json.dumps(text)


# Input: ctx (Context), block (str), entry (str), params (dict | None)
# Output: dict[str, Any]
# Processing: Preview replacing a step with a call to a verified library helper.
# Changed by: Sourish
# Date: 2026-10-22
@operation("block.replace", command="/block replace", tool="plovane_block_replace", group="plovane_compile")
def replace(ctx: Context, block: str, entry: str, params: dict | None = None) -> dict[str, Any]:
    """Replaces a block with a verified palette block."""
    step = ctx.board.block(block)
    if step.kind != "step":
        raise OpError("INVALID_ARGS", "replace works on a step block (see /blocks view)")
    path = step.meta["file"]
    ctx.require(path)
    found = ctx.library().get(entry)
    if "error" in found:
        raise OpError("NOT_FOUND", f"{found['error']}; similar: {found.get('did_you_mean', [])}")
    language = codegen.language_of(path)
    if found["language"] != language:
        raise OpError("CONFLICT", f"{entry} is {found['language']}; {path} is {language}")
    text = ctx.fs.read(path)
    lines = text.splitlines(keepends=True)
    start, end = step.meta["lines"]
    first = lines[start - 1]
    indent = re.match(r"\s*", first).group(0)
    args = ", ".join(f"{k}={literal(v)}" for k, v in (params or {}).items())
    call = f"{found['name']}({args})"
    keep = re.match(r"\s*(return\s+|[\w.\[\]]+\s*=\s*)", first)
    prefix = keep.group(1) if keep else ""
    end_mark = ";" if language in ("javascript", "typescript", "cpp") else ""
    lines[start - 1:end] = [f"{indent}{prefix}{call}{end_mark}\n"]
    new_text = "".join(lines)
    if not re.search(rf"\b(def|function)\s+{re.escape(found['name'])}\b", new_text):
        c = codegen.comment(language)
        source = found.get("source_permalink") or found.get("source_url") or found["block_id"]
        helper = f"{c} From the Plovane library: {found['block_id']} ({found['license']}), {source}\n{found['code']}\n"
        new_text = codegen.append_block(new_text, helper)
    result = record_compile(ctx, path, {path: new_text}, "replace", [step.id])
    return result | {"entry": found["block_id"], "call": call}


# Input: ctx (Context), step (Block)
# Output: str
# Processing: Delete a step's lines from its file, keeping Python bodies valid.
# Changed by: Sourish
# Date: 2026-10-22
def delete_step_code(ctx: Context, step: Block) -> str:
    path = step.meta["file"]
    lines = ctx.fs.read(path).splitlines(keepends=True)
    start, end = step.meta["lines"]
    indent = re.match(r"\s*", lines[start - 1]).group(0)
    del lines[start - 1:end]
    if codegen.language_of(path) == "python":
        try:
            ast.parse("".join(lines))
        except SyntaxError:
            lines.insert(start - 1, f"{indent}pass\n")
    ctx.fs.write(path, "".join(lines))
    return f"{path}:{start}-{end}"
