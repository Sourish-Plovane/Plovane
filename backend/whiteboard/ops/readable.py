"""Readable code for little or no money: summaries one layer up, and English line by line.

- **Summaries**: ``/summary`` gives each file block a one-line "what this is",
  shown on its tile in the folder view, and the project one line shown at the
  top. The parser writes them for free (docstring, leading comment, or what
  the file defines); ``--provider claude`` asks an AI once for every stale file
  in a single call. Each is stored in the block's ``meta`` with a fingerprint
  of the file, so it travels with the ``.plo`` through git and is only
  rewritten when the file changes. An agent that has already read a file
  can write the line itself with ``/summary set`` for no extra tokens.
- **Line by line**: ``/translate`` puts a plain-English sentence beside each
  line. Rules word the common lines (assignments, returns, loops, calls,
  definitions) for free; only lines no rule covers go to an AI, all in one
  call, and only with ``--provider``. Every AI sentence is cached by the
  line's text, so the same line anywhere in the project is never paid twice.
"""

from __future__ import annotations

import ast
import hashlib
import json
import re
from typing import Any

from backend.whiteboard import codegen
from backend.whiteboard.engine import Context
from backend.whiteboard.ops.english import brace_sentence, project_path, sentence, short
from backend.whiteboard.registry import OpError, flag, integer, operation
from backend.whiteboard.store import STATE_DIR

SUMMARY_WORDS = 16
"""Longest summary line kept, in words."""

AI_FILE_CHARS = 1500
"""Characters of each file sent when asking an AI for summaries: enough to tell what it is."""

AI_BATCH = 40
"""Files per AI call for summaries."""

AI_LINES = 200
"""Lines per AI call for translations."""

TRANSLATIONS = "translations.json"


# Input: text (str)
# Output: str
# Processing: A short fingerprint of a file's text, to tell when a summary is out of date.
# Changed by: Claude Code
# Date: 2026-11-18
def fingerprint(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8", "replace")).hexdigest()[:12]


# Input: text (str)
# Output: str
# Processing: The first sentence of a passage, cut to SUMMARY_WORDS words.
# Changed by: Claude Code
# Date: 2026-11-18
def first_sentence(text: str) -> str:
    flat = re.sub(r"\s+", " ", text).strip()
    flat = re.split(r"(?<=[.!?])\s", flat, maxsplit=1)[0]
    words = flat.split(" ")
    return " ".join(words[:SUMMARY_WORDS]).rstrip(",;:") + ("…" if len(words) > SUMMARY_WORDS else "")


# Input: names (list[str])
# Output: str
# Processing: "a, b and c" from a list of names.
# Changed by: Claude Code
# Date: 2026-11-18
def listing(names: list[str]) -> str:
    return names[0] if len(names) == 1 else ", ".join(names[:-1]) + " and " + names[-1]


# Input: path (str), text (str)
# Output: str
# Processing: A free one-line summary of a file from its docstring, leading comment or definitions.
# Changed by: Claude Code
# Date: 2026-11-18
def free_summary(path: str, text: str) -> str:
    language = codegen.language_of(path) or path.rsplit(".", 1)[-1]
    if path.endswith(".py"):
        try:
            tree = ast.parse(text)
        except SyntaxError:
            tree = None
        if tree is not None:
            doc = ast.get_docstring(tree)
            if doc and doc.strip():
                return first_sentence(doc)
            names = [n.name for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
                     and not n.name.startswith("_")]
            if names:
                return f"Defines {listing(names[:4])}" + (f" and {len(names) - 4} more" if len(names) > 4 else "")
    else:
        head = re.match(r"\s*(?:/\*\*?([\s\S]*?)\*/|((?:\s*//[^\n]*\n)+)|((?:\s*#[^\n!]*\n)+))", text)
        if head:
            comment = head.group(1) or head.group(2) or head.group(3) or ""
            cleaned = re.sub(r"^\s*(\*|//|#)\s?", "", comment, flags=re.M).strip()
            if cleaned and not cleaned.lower().startswith(("eslint", "@ts-", "copyright", "license")):
                return first_sentence(cleaned)
        names = re.findall(r"^\s*(?:export\s+)?(?:default\s+)?(?:async\s+)?(?:function\*?|class)\s+([A-Za-z_$][\w$]*)",
                           text, re.M)
        names += re.findall(r"^\s*(?:export\s+)?const\s+([A-Za-z_$][\w$]*)\s*=\s*(?:async\s*)?\(", text, re.M)
        if names:
            unique = list(dict.fromkeys(names))
            return f"Defines {listing(unique[:4])}" + (f" and {len(unique) - 4} more" if len(unique) > 4 else "")
        if path.endswith((".html", ".htm")) and (m := re.search(r"<title>([^<]+)</title>", text, re.I)):
            return f"Web page: {m.group(1).strip()}"
    lines = len(text.splitlines())
    return f"{lines} lines of {language}" if lines else "Empty file"


# Input: ctx (Context)
# Output: str
# Processing: A free one-line description of the project from its README, package.json or pyproject.
# Changed by: Claude Code
# Date: 2026-11-18
def free_about(ctx: Context) -> str:
    for name in ("package.json",):
        path = ctx.root / name
        if path.is_file():
            try:
                description = json.loads(path.read_text(encoding="utf-8")).get("description")
            except (ValueError, AttributeError):
                description = None
            if description:
                return first_sentence(description)
    pyproject = ctx.root / "pyproject.toml"
    if pyproject.is_file():
        m = re.search(r'^description\s*=\s*"([^"]+)"', pyproject.read_text(encoding="utf-8", errors="replace"), re.M)
        if m:
            return first_sentence(m.group(1))
    for name in ("README.md", "README", "readme.md", "README.txt"):
        path = ctx.root / name
        if path.is_file():
            for para in re.split(r"\n\s*\n", path.read_text(encoding="utf-8", errors="replace")):
                para = para.strip()
                if para and not para.startswith(("#", "!", "[", "<", "```", "|")):
                    return first_sentence(para)
    return ""


# Input: ctx (Context), target (str)
# Output: list[Any]
# Processing: The file blocks on the whiteboard at or under a path ("" is the whole project).
# Changed by: Claude Code
# Date: 2026-11-18
def file_blocks(ctx: Context, target: str) -> list[Any]:
    found = []
    for block in ctx.board.blocks.values():
        if block.kind != "file" or not block.path:
            continue
        path = block.path.strip("/")
        if not target or path == target or path.startswith(target + "/"):
            found.append(block)
    return sorted(found, key=lambda b: b.path)


# Input: ctx (Context), files (dict[str, str]), about (str), want_about (bool), provider (str | None)
# Output: tuple[dict[str, str], dict[str, Any]]
# Processing: One AI call wording summaries for many files at once (and the project line if asked).
# Changed by: Claude Code
# Date: 2026-11-18
def ai_summaries(ctx: Context, files: dict[str, str], about: str, want_about: bool,
                 provider: str | None) -> tuple[dict[str, str], dict[str, Any]]:
    parts = [f"### {path}\n{text[:AI_FILE_CHARS]}" for path, text in files.items()]
    ask_about = (' Also include the key "_project" with one line (at most 20 words) on what the whole project is.'
                 if want_about else "")
    prompt = (f"Project: {about or '(not described yet)'}\n\nFor each file below, write one line of at most "
              f"{SUMMARY_WORDS - 2} words saying what it does, for a developer new to the project.{ask_about} "
              "Reply with only a JSON object mapping each file path to its line.\n\n" + "\n\n".join(parts))
    reply = ctx.ws.llm_result(prompt, "You summarise code files in plain English. You reply with JSON only.",
                              provider=provider)
    text = reply.get("text", "")
    try:
        found = json.loads(text[text.index("{"): text.rindex("}") + 1])
    except ValueError:
        ctx.notes.append("the AI's summaries could not be read; kept the free ones")
        return {}, reply.get("usage", {})
    return {str(k): first_sentence(str(v)) for k, v in found.items() if isinstance(v, (str, int, float))}, \
        reply.get("usage", {})


# Input: ctx (Context), target (str), provider (str | None), refresh (Any)
# Output: dict[str, Any]
# Processing: Give file blocks a one-line summary (and the project a line), only where missing or out of date.
# Changed by: Claude Code
# Date: 2026-11-18
@operation("code.summary", command="/summary", tool="plovane_summary", group="plovane_code")
def summary(ctx: Context, target: str = "", provider: str | None = None, refresh: Any = False) -> dict[str, Any]:
    """Gives each file a one-line summary shown on its tile; free by default, one AI call with --provider."""
    path = project_path(ctx, target or "")
    path = "" if path == "." else path
    ctx.require(path or None, "read")
    blocks = file_blocks(ctx, path)
    if path and not blocks and not (ctx.root / path).exists():
        raise OpError("NOT_FOUND", f"{path} is not in the project")
    stale: dict[str, str] = {}
    counts = {"cached": 0, "free": 0, "ai": 0}
    for block in blocks:
        file = ctx.root / block.path
        if not file.is_file():
            continue
        text = file.read_text(encoding="utf-8", errors="replace")
        mark = fingerprint(text)
        if block.meta.get("summary_hash") == mark and block.meta.get("summary") and not flag(refresh):
            counts["cached"] += 1
            continue
        if block.meta.get("summary_by") not in (None, "parser") and block.meta.get("summary_hash") == mark:
            counts["cached"] += 1  # a person's or agent's line is kept until the file changes
            continue
        stale[block.path] = text
        block.meta.update(summary=free_summary(block.path, text), summary_hash=mark, summary_by="parser")
        counts["free"] += 1
    about = ctx.board.view.get("about") or ""
    usage: dict[str, Any] = {}
    if provider and (stale or not about):
        paths = list(stale)
        for start in range(0, max(len(paths), 1), AI_BATCH):
            chunk = {p: stale[p] for p in paths[start:start + AI_BATCH]}
            lines, used = ai_summaries(ctx, chunk, about, want_about=not about and start == 0, provider=provider)
            for key, value in used.items():
                usage[key] = usage.get(key, 0) + (value if isinstance(value, int) else 0)
            if lines.get("_project") and not about:
                about = lines["_project"]
                ctx.board.view["about"] = about
            for block in blocks:
                if block.path in chunk and lines.get(block.path):
                    block.meta.update(summary=lines[block.path], summary_by=provider)
                    counts["ai"] += 1
                    counts["free"] -= 1
    if not ctx.board.view.get("about"):
        free = free_about(ctx)
        if free:
            ctx.board.view["about"] = about = free
    return {"about": ctx.board.view.get("about", ""), "files": {b.path: b.meta.get("summary") for b in blocks},
            "counts": counts, "usage": usage}


# Input: ctx (Context), target (str), text (str)
# Output: dict[str, Any]
# Processing: Store a summary someone wrote (a person, or an agent that has read the file), no AI call.
# Changed by: Claude Code
# Date: 2026-11-18
@operation("code.summary_set", command="/summary set", tool="plovane_summary_set", group="plovane_code")
def summary_set(ctx: Context, target: str, text: str) -> dict[str, Any]:
    """Stores your own one-line summary for a file (an agent that read it pays no extra tokens)."""
    path = project_path(ctx, target)
    ctx.require(path, "read")
    block = ctx.board.block_for_path(path)
    if block is None:
        raise OpError("NOT_FOUND", f"{path} is not on the whiteboard; /project import or /blocks view it first")
    file = ctx.root / path
    mark = fingerprint(file.read_text(encoding="utf-8", errors="replace")) if file.is_file() else None
    line = first_sentence(text)
    if not line:
        raise OpError("INVALID_ARGS", "give the summary text")
    block.meta.update(summary=line, summary_hash=mark, summary_by=ctx.actor)
    return {"file": path, "summary": line}


# Input: ctx (Context), text (str)
# Output: dict[str, Any]
# Processing: Set the one line saying what the whole project is, shown above every whiteboard.
# Changed by: Claude Code
# Date: 2026-11-18
@operation("project.describe", command="/project describe", tool="plovane_project_describe", group="plovane_project")
def describe(ctx: Context, text: str = "") -> dict[str, Any]:
    """Sets the one line saying what the whole project is, shown at the top of the whiteboard."""
    ctx.require(None, "read")
    if text.startswith('"'):  # whiteboard files carry awkward text as a JSON string
        try:
            text = str(json.loads(text))
        except ValueError:
            pass
    line = re.sub(r"\s+", " ", text).strip()
    if line:
        ctx.board.view["about"] = line[:200]
    else:
        ctx.board.view.pop("about", None)
    return {"about": ctx.board.view.get("about", "")}


# -- Line by line ------------------------------------------------------------------

# Input: node (ast.AST)
# Output: str
# Processing: The parameter list of a function, for "Define f taking a and b".
# Changed by: Claude Code
# Date: 2026-11-18
def parameters(node: ast.FunctionDef | ast.AsyncFunctionDef) -> str:
    names = [a.arg for a in node.args.posonlyargs + node.args.args + node.args.kwonlyargs if a.arg not in ("self", "cls")]
    if node.args.vararg:
        names.append("*" + node.args.vararg.arg)
    if node.args.kwarg:
        names.append("**" + node.args.kwarg.arg)
    return f" taking {listing(names)}" if names else ""


# Input: text (str)
# Output: dict[int, tuple[str, str]]
# Processing: Python lines worded through the syntax tree: {line: (english, "rule" | "continues")}.
# Changed by: Claude Code
# Date: 2026-11-18
def python_lines(text: str) -> dict[int, tuple[str, str]]:
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return {}
    out: dict[int, tuple[str, str]] = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.stmt):
            continue
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            words = f"Define {'async ' if isinstance(node, ast.AsyncFunctionDef) else ''}{node.name}{parameters(node)}"
            for deco in node.decorator_list:
                out[deco.lineno] = (f"Mark {node.name} with {short(deco)}", "rule")
        elif isinstance(node, ast.ClassDef):
            bases = [short(b) for b in node.bases]
            words = f"Define the {node.name} type" + (f", built on {listing(bases)}" if bases else "")
            for deco in node.decorator_list:
                out[deco.lineno] = (f"Mark {node.name} with {short(deco)}", "rule")
        elif isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
            words = "Describe it: " + first_sentence(node.value.value)
        else:
            words = sentence(node)
        out[node.lineno] = (words, "rough" if words.startswith("Run ") else "rule")
        body = getattr(node, "body", None)
        last = (body[0].lineno - 1) if isinstance(body, list) and body and isinstance(body[0], ast.stmt) \
            else (node.end_lineno or node.lineno)
        for number in range(node.lineno + 1, last + 1):
            out.setdefault(number, ("(continues the line above)", "continues"))
    return out


PY_CLAUSES = (
    (re.compile(r"^elif\s+(.+):$"), lambda m: f"Otherwise, if {m.group(1)}"),
    (re.compile(r"^else\s*:$"), lambda m: "Otherwise"),
    (re.compile(r"^except\s*:$"), lambda m: "If anything fails"),
    (re.compile(r"^except\s+\(?([^)]+?)\)?(?:\s+as\s+\w+)?\s*:$"), lambda m: f"If that fails with {m.group(1)}"),
    (re.compile(r"^finally\s*:$"), lambda m: "Whatever happened, finish with"),
)
"""Python clause lines that are not statements of their own."""

JS_RULES = (
    (re.compile(r"^(?:export\s+)?(?:default\s+)?(async\s+)?function\*?\s*([\w$]*)\s*\(([^)]*)\)"),
     lambda m: f"Define {'async ' if m.group(1) else ''}{m.group(2) or 'a function'}"
               + (f" taking {m.group(3).strip()}" if m.group(3).strip() else "")),
    (re.compile(r"^(?:export\s+)?(?:const|let|var)\s+([\w$]+)\s*=\s*(async\s*)?\(?([^)=]*)\)?\s*=>"),
     lambda m: f"Define {'async ' if m.group(2) else ''}{m.group(1)}"
               + (f" taking {m.group(3).strip()}" if m.group(3).strip() else "")),
    (re.compile(r"^(?:export\s+)?(?:default\s+)?class\s+([\w$]+)(?:\s+extends\s+([\w$.]+))?"),
     lambda m: f"Define the {m.group(1)} type" + (f", built on {m.group(2)}" if m.group(2) else "")),
    (re.compile(r"^import\s+(.+?)\s+from\s+['\"]([^'\"]+)['\"]"), lambda m: f"Bring in {m.group(1)} from {m.group(2)}"),
    (re.compile(r"^import\s+['\"]([^'\"]+)['\"]"), lambda m: f"Load {m.group(1)}"),
    (re.compile(r"^(?:const|let|var)\s+(\{[^}]*\}|\[[^\]]*\])\s*=\s*(.+)$"), lambda m: f"Take {m.group(1)} out of {m.group(2)}"),
    (re.compile(r"^export\s+(?:default\s+)?([\w$]+)$"), lambda m: f"Share {m.group(1)} with other files"),
    (re.compile(r"^(?:async\s+)?(?!(?:if|for|while|switch|catch|return|function|await|typeof|new)\b)([\w$]+)\s*\(([^)]*)\)$"),
     lambda m: f"Define the {m.group(1)} method"
               + (f" taking {m.group(2).strip()}" if m.group(2).strip() else "")),
)
"""JavaScript and TypeScript line shapes the brace-language rules do not cover."""


# Input: raw (str), language (str)
# Output: tuple[str | None, str]
# Processing: One line in plain English by rule: (english, "rule" | "rough" | "skip").
# Changed by: Claude Code
# Date: 2026-11-18
def rule_line(raw: str, language: str) -> tuple[str | None, str]:
    text = raw.strip()
    if not text:
        return None, "skip"
    if language == "python":
        if text.startswith("#"):
            return "Note: " + text.lstrip("# ").strip(), "rule"
        for pattern, word in PY_CLAUSES:
            if m := pattern.match(text):
                return word(m), "rule"
        if re.fullmatch(r"[)\]}]+[,:]?", text):
            return None, "skip"
        try:
            node = ast.parse(text.rstrip(":") + ("\n    pass" if text.endswith(":") else "")).body[0]
        except (SyntaxError, IndexError):
            return None, "unknown"
        words = sentence(node)
        return words, "rough" if words.startswith("Run ") else "rule"
    if text.startswith("//"):
        return "Note: " + text.lstrip("/ ").strip(), "rule"
    if text.startswith(("/*", "*", "*/")):
        cleaned = text.strip("/* ").strip()
        return ("Note: " + cleaned, "rule") if cleaned else (None, "skip")
    body = re.sub(r"//.*$", "", text).strip().strip("{};").strip()
    if not body or re.fullmatch(r"[)\]}]+[,;)]*", body):
        return None, "skip"
    for pattern, word in JS_RULES:
        if m := pattern.match(body):
            return word(m), "rule"
    words = brace_sentence(body)
    return words, "rough" if words.startswith("Run:") else "rule"


# Input: language (str), line (str)
# Output: str
# Processing: The cache key of one line: the same line anywhere in the project shares its wording.
# Changed by: Claude Code
# Date: 2026-11-18
def line_key(language: str, line: str) -> str:
    return hashlib.sha1(f"{language}\n{line.strip()}".encode("utf-8", "replace")).hexdigest()[:16]


# Input: ctx (Context)
# Output: dict[str, str]
# Processing: The saved AI wordings of lines.
# Changed by: Claude Code
# Date: 2026-11-18
def load_translations(ctx: Context) -> dict[str, str]:
    path = ctx.root / STATE_DIR / TRANSLATIONS
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


# Input: ctx (Context), cache (dict[str, str])
# Output: None
# Processing: Save the AI wordings of lines (the state folder is git-ignored, so each person has their own).
# Changed by: Claude Code
# Date: 2026-11-18
def save_translations(ctx: Context, cache: dict[str, str]) -> None:
    folder = ctx.root / STATE_DIR
    folder.mkdir(exist_ok=True)
    (folder / TRANSLATIONS).write_text(json.dumps(cache, indent=0, sort_keys=True), encoding="utf-8")


# Input: ctx (Context), file (str), start (Any), end (Any), provider (str | None)
# Output: dict[str, Any]
# Processing: Put a plain-English sentence beside each line: rules first, the cache next, an AI last.
# Changed by: Claude Code
# Date: 2026-11-18
@operation("code.translate", command="/translate", tool="plovane_translate", group="plovane_code", mutates=False)
def translate(ctx: Context, file: str, start: Any = None, end: Any = None,
              provider: str | None = None) -> dict[str, Any]:
    """Translates a file line by line into plain English; rules are free, the AI only words the rest."""
    path = project_path(ctx, file)
    ctx.require(path, "read")
    if not (ctx.root / path).is_file():
        raise OpError("NOT_FOUND", f"{path} is not a file in the project")
    text = (ctx.root / path).read_text(encoding="utf-8", errors="replace")
    language = codegen.language_of(path) or "text"
    lines = text.splitlines()
    first = max(1, integer(start, "start")) if start is not None else 1
    last = min(len(lines), integer(end, "end")) if end is not None else len(lines)
    if first > last and lines:
        raise OpError("INVALID_ARGS", f"{path} has lines 1-{len(lines)}")
    tree = python_lines(text) if language == "python" else {}
    cache = load_translations(ctx)
    rows: list[dict[str, Any]] = []
    for number in range(first, last + 1):
        raw = lines[number - 1]
        if not raw.strip():
            rows.append({"line": number, "code": raw, "english": None, "source": "blank"})
            continue
        english, source = tree.get(number, (None, "unknown"))
        if english is None or source == "unknown":
            english, source = rule_line(raw, language)
        if source in ("rough", "unknown") and line_key(language, raw) in cache:
            english, source = cache[line_key(language, raw)], "cache"
        rows.append({"line": number, "code": raw, "english": english, "source": source})
    usage: dict[str, Any] = {}
    wanted = list(dict.fromkeys(r["code"].strip() for r in rows if r["source"] in ("rough", "unknown")))
    if provider and wanted:
        worded: dict[str, str] = {}
        for begin in range(0, len(wanted), AI_LINES):
            chunk = wanted[begin:begin + AI_LINES]
            numbered = "\n".join(f"{i + 1}. {line}" for i, line in enumerate(chunk))
            reply = ctx.ws.llm_result(
                f"These are {len(chunk)} lines from {path} ({language}). Say what each line does in one short "
                f"plain-English sentence for a developer. Reply with only a JSON array of {len(chunk)} strings.\n\n"
                + numbered, "You explain code line by line in plain English. You reply with JSON only.",
                provider=provider)
            for key, value in reply.get("usage", {}).items():
                usage[key] = usage.get(key, 0) + (value if isinstance(value, int) else 0)
            reply_text = reply.get("text", "")
            try:
                sentences = json.loads(reply_text[reply_text.index("["): reply_text.rindex("]") + 1])
            except ValueError:
                ctx.notes.append("the AI's wording could not be read; kept the rule wording")
                continue
            if len(sentences) == len(chunk):
                worded.update({line: str(s).strip() for line, s in zip(chunk, sentences)})
        for row in rows:
            if row["source"] in ("rough", "unknown") and row["code"].strip() in worded:
                row["english"], row["source"] = worded[row["code"].strip()], "ai"
                cache[line_key(language, row["code"])] = row["english"]
        if worded and not ctx.dry_run:
            save_translations(ctx, cache)
    counts: dict[str, int] = {}
    for row in rows:
        counts[row["source"]] = counts.get(row["source"], 0) + 1
    worded_lines = sum(v for k, v in counts.items() if k not in ("blank", "skip", "continues"))
    free = sum(counts.get(k, 0) for k in ("rule", "cache"))
    return {"file": path, "language": language, "lines": rows, "counts": counts, "usage": usage,
            "free_share": round(free / worded_lines, 2) if worded_lines else 1.0,
            "needs_ai": len(wanted) if not provider else 0}
