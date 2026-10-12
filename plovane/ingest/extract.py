"""Pulling candidate entries out of a source file.

Python goes through ``ast``, which is exact: it gives real function boundaries,
signatures and docstrings. Everything else uses a brace-matching heuristic,
which is *not* exact and is documented as such -- it will mis-slice a function
containing a brace inside a string or a macro, and it cannot see through
templates or preprocessor conditionals. That is acceptable for a seeding
pipeline whose output a human reviews, and unacceptable as a silent source of
truth, so every non-Python extraction is marked ``needs_review``.

Nothing here judges whether code is *good*. A harvested candidate is a
proposal: the spec expects entries to carry mutation-tested tests and a
generated description, and neither exists at this stage.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass

MAX_FUNCTION_LINES = 120
"""Beyond this a function is not a reusable helper, it is a program."""

MIN_FUNCTION_LINES = 3
"""Below this it is a one-liner nobody needs recommended to them."""

BRACE_LANGUAGES = {"javascript", "typescript", "cpp", "cuda"}

EXTENSION_LANGUAGE: dict[str, str] = {
    ".py": "python",
    ".js": "javascript",
    ".mjs": "javascript",
    ".jsx": "javascript",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".cpp": "cpp",
    ".cc": "cpp",
    ".cxx": "cpp",
    ".hpp": "cpp",
    ".h": "cpp",
    ".cu": "cuda",
    ".cuh": "cuda",
    ".css": "css",
    ".scss": "css",
    ".html": "html",
    ".htm": "html",
}

SKIP_PATH_MARKERS = (
    "/test/", "/tests/", "/__tests__/", "_test.", "test_", ".spec.", ".test.",
    "/node_modules/", "/vendor/", "/third_party/", "/dist/", "/build/",
    "/.github/", "/docs/", "/examples/", "/example/", "/benchmark",
    ".min.js", ".generated.", "_pb2.py", "/migrations/",
)
"""Paths a harvest should not mine.

Tests and examples are the tempting mistake: they are full of short, clean,
self-contained functions that look ideal and are usually assertions or demos.
Vendored and generated trees are worse -- their licence is not the repository's.
"""


# Input: path (str)
# Output: str | None
# Processing: Language for path
# Changed by: Claude Code
# Date: 2026-10-12
def language_for_path(path: str) -> str | None:
    # Anchored with a leading slash so a root-level directory matches the same
    # markers as a nested one. GitHub's tree API returns paths without one, so
    # without this "node_modules/x.js" at the repo root slips through -- and
    # vendored code carries its own licence, not the host repository's, which
    # would attach the wrong attribution to it.
    lowered = "/" + path.lower().lstrip("/")
    for marker in SKIP_PATH_MARKERS:
        if marker in lowered:
            return None
    for extension, language in EXTENSION_LANGUAGE.items():
        if lowered.endswith(extension):
            return language
    return None


@dataclass
class ExtractedUnit:
    """One candidate: a function, or a whole file as a template."""

    name: str
    code: str
    language: str
    start_line: int
    end_line: int
    signature: str | None = None
    docstring: str = ""
    is_template: bool = False
    needs_review: bool = False

    # Input: none
    # Output: int
    # Processing: Line count
    # Changed by: Claude Code
    # Date: 2026-10-12
    @property
    def line_count(self) -> int:
        return self.end_line - self.start_line + 1


# Input: node (ast.FunctionDef | ast.AsyncFunctionDef)
# Output: str
# Processing: Python signature
# Changed by: Claude Code
# Date: 2026-10-12
def _python_signature(node: ast.FunctionDef | ast.AsyncFunctionDef) -> str:
    args = [a.arg for a in node.args.posonlyargs + node.args.args]
    if node.args.vararg:
        args.append(f"*{node.args.vararg.arg}")
    args += [a.arg for a in node.args.kwonlyargs]
    if node.args.kwarg:
        args.append(f"**{node.args.kwarg.arg}")
    returns = f" -> {ast.unparse(node.returns)}" if node.returns else ""
    return f"({', '.join(args)}){returns}"


# Input: source (str)
# Output: list[ExtractedUnit]
# Processing: Exact extraction of module-level functions via the AST.
# Changed by: Claude Code
# Date: 2026-10-12
def extract_python(source: str) -> list[ExtractedUnit]:
    """Exact extraction of module-level functions via the AST.

    Module level only: a method pulled out of a class is not independently
    usable, and recommending one produces code that does not run.
    """
    try:
        tree = ast.parse(source)
    except SyntaxError:
        # A file we cannot parse is a file we cannot slice safely.
        return []

    units: list[ExtractedUnit] = []
    for node in tree.body:
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if node.name.startswith("_"):
            continue  # private by convention
        start, end = node.lineno, (node.end_lineno or node.lineno)
        if not MIN_FUNCTION_LINES <= (end - start + 1) <= MAX_FUNCTION_LINES:
            continue
        units.append(
            ExtractedUnit(
                name=node.name,
                code=ast.get_source_segment(source, node) or "",
                language="python",
                start_line=start,
                end_line=end,
                signature=_python_signature(node),
                docstring=ast.get_docstring(node) or "",
            )
        )
    return units


# Input: source (str), language (str)
# Output: list[ExtractedUnit]
# Processing: Heuristic extraction for brace languages. Marked ``needs_review``.
# Changed by: Claude Code
# Date: 2026-10-12
def extract_braced(source: str, language: str) -> list[ExtractedUnit]:
    """Heuristic extraction for brace languages. Marked ``needs_review``.

    Walks from a plausible declaration to its matching closing brace, skipping
    string and comment content. It is right often enough to seed a library and
    wrong often enough that nothing it produces should ship unreviewed.
    """
    import re

    declaration = re.compile(
        r"^(?:export\s+)?(?:async\s+)?(?:function\s+(?P<fname>\w+)\s*\((?P<fargs>[^)]*)\)"
        r"|(?:__global__|__device__|inline|static|template\s*<[^>]*>)?\s*"
        r"[\w:<>,\*&\s]+?\s+(?P<cname>\w+)\s*\((?P<cargs>[^)]*)\)\s*(?:const)?)\s*\{",
        re.MULTILINE,
    )
    lines = source.splitlines()
    units: list[ExtractedUnit] = []

    for match in declaration.finditer(source):
        name = match.group("fname") or match.group("cname")
        args = match.group("fargs") if match.group("fname") else match.group("cargs")
        if not name or name in {"if", "for", "while", "switch", "catch", "return"}:
            continue

        depth, index, in_string, quote = 0, match.end() - 1, False, ""
        while index < len(source):
            char = source[index]
            if in_string:
                if char == "\\":
                    index += 2
                    continue
                if char == quote:
                    in_string = False
            elif char in "\"'`":
                in_string, quote = True, char
            elif char == "{":
                depth += 1
            elif char == "}":
                depth -= 1
                if depth == 0:
                    break
            index += 1
        else:
            continue  # unbalanced: skip rather than guess

        start_line = source[: match.start()].count("\n") + 1
        end_line = source[:index].count("\n") + 1
        if not MIN_FUNCTION_LINES <= (end_line - start_line + 1) <= MAX_FUNCTION_LINES:
            continue
        units.append(
            ExtractedUnit(
                name=name,
                code="\n".join(lines[start_line - 1 : end_line]),
                language=language,
                start_line=start_line,
                end_line=end_line,
                signature=f"({args.strip()})" if args else "()",
                needs_review=True,
            )
        )
    return units


# Input: source (str), path (str), language (str), max_lines (int)
# Output: ExtractedUnit | None
# Processing: Treat a whole file as a template candidate.
# Changed by: Claude Code
# Date: 2026-10-12
def extract_file_as_template(
    source: str, path: str, language: str, *, max_lines: int = 400
) -> ExtractedUnit | None:
    """Treat a whole file as a template candidate.

    How HTML, CSS and scaffolding enter the library: there is no function to
    slice, the file *is* the unit. No signature, per the spec.
    """
    lines = source.splitlines()
    if not MIN_FUNCTION_LINES <= len(lines) <= max_lines:
        return None
    return ExtractedUnit(
        name=path.rsplit("/", 1)[-1],
        code=source,
        language=language,
        start_line=1,
        end_line=len(lines),
        is_template=True,
        needs_review=True,
    )


# Input: source (str), path (str), want_template (bool)
# Output: list[ExtractedUnit]
# Processing: Extract candidates from one file, dispatching on its language.
# Changed by: Claude Code
# Date: 2026-10-12
def extract(source: str, path: str, *, want_template: bool = False) -> list[ExtractedUnit]:
    """Extract candidates from one file, dispatching on its language."""
    language = language_for_path(path)
    if language is None:
        return []
    if want_template or language in {"html", "css"}:
        unit = extract_file_as_template(source, path, language)
        return [unit] if unit else []
    if language == "python":
        return extract_python(source)
    if language in BRACE_LANGUAGES:
        return extract_braced(source, language)
    return []
