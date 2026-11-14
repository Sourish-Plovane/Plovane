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
import builtins
import re
import sys
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
    ".min.", ".generated.", "_pb2.py", "/migrations/",
)
"""Paths a harvest should not mine.

Tests and examples are the tempting mistake: they are full of short, clean,
self-contained functions that look ideal and are usually assertions or demos.
Vendored and generated trees are worse -- their licence is not the repository's.

``.min.`` rather than ``.min.js``: a minified stylesheet is as much a build
artefact as a minified script, and the narrower marker let 119 ``.min.css``
files into a harvest. They pass every other gate -- a minified file is one
long self-contained line -- so nothing downstream would have caught them.
"""


# Input: path (str)
# Output: str | None
# Processing: Language for path
# Changed by: Sourish
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
    dependencies_analysed: bool = False
    """Whether an extractor actually resolved this unit's free names.

    False by default, and only the Python extractor sets it. Without it
    ``self_contained`` read ``not unresolved_names``, so a brace-language unit
    with an empty tuple reported itself clean by construction: 1,030 of 1,242
    harvested records passed a check that never ran. Same principle as the
    licence gate -- unknown is rejected, not assumed good.
    """

    required_imports: tuple[str, ...] = ()
    """Import statements the function needs, taken from its source module."""

    annotation_only_names: tuple[str, ...] = ()
    """Unresolvable names that appear only in type annotations.

    Harmless at run time under ``from __future__ import annotations``, which
    ``insertable_code`` adds when any are present. Recorded rather than
    ignored, because the annotation is now a string the user's type checker
    cannot resolve.
    """

    unresolved_names: tuple[str, ...] = ()
    """Free names that resolve to something other than an import.

    A module-level constant, a sibling function, a class defined alongside it.
    Naming them beats a bare boolean: a reviewer can see what is missing.
    """

    # Input: none
    # Output: int
    # Processing: Line count
    # Changed by: Sourish
    # Date: 2026-10-12
    @property
    def line_count(self) -> int:
        return self.end_line - self.start_line + 1

    # Input: none
    # Output: bool
    # Processing: Whether the code plus required_imports runs on its own
    # Changed by: Sourish
    # Date: 2026-10-12
    @property
    def self_contained(self) -> bool:
        """Whether the code plus ``required_imports`` runs on its own.

        The property the library actually needs. The spec has the server copy
        the exact code into a user's project, so a unit depending on names left
        behind in its source module is not an entry, however well it was
        extracted or attributed.

        An unanalysed unit is not self-contained. It is unknown, and unknown is
        rejected here for the same reason an unknown licence is.
        """
        return self.dependencies_analysed and not self.unresolved_names

    # Input: none
    # Output: str
    # Processing: The code with its imports prepended, ready to paste
    # Changed by: Sourish
    # Date: 2026-10-12
    @property
    def insertable_code(self) -> str:
        """The code with its required imports prepended.

        A ``from __future__ import annotations`` line leads when any annotation
        name could not be resolved, which is what keeps those units runnable:
        the annotation is never evaluated, so it cannot raise ``NameError``.
        """
        header = list(self.required_imports)
        if self.annotation_only_names:
            header.insert(0, "from __future__ import annotations")
        if not header:
            return self.code
        return "\n".join([*header, "", self.code])


# Input: node (ast.FunctionDef | ast.AsyncFunctionDef)
# Output: str
# Processing: Python signature
# Changed by: Sourish
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


# Input: tree (ast.Module)
# Output: dict[str, str]
# Processing: Map each name a module imports to the statement that binds it
# Changed by: Sourish
# Date: 2026-10-12
def _module_imports(tree: ast.Module) -> dict[str, str]:
    """Map each imported name to the statement that binds it.

    Module level only: an import inside another function is not in scope for
    the one being extracted.
    """
    bindings: dict[str, str] = {}
    for node in tree.body:
        if isinstance(node, ast.Import):
            for alias in node.names:
                bound = alias.asname or alias.name.split(".")[0]
                bindings[bound] = (
                    f"import {alias.name} as {alias.asname}" if alias.asname else f"import {alias.name}"
                )
        elif isinstance(node, ast.ImportFrom):
            module = ("." * node.level) + (node.module or "")
            for alias in node.names:
                bound = alias.asname or alias.name
                piece = f"{alias.name} as {alias.asname}" if alias.asname else alias.name
                bindings[bound] = f"from {module} import {piece}"
    return bindings


# Input: fn (ast.FunctionDef | ast.AsyncFunctionDef)
# Output: set[str]
# Processing: Every name the function binds itself, so it is not free
# Changed by: Sourish
# Date: 2026-10-12
def _bound_locally(fn: ast.FunctionDef | ast.AsyncFunctionDef) -> set[str]:
    """Every name the function binds itself.

    Parameters, assignments, comprehension and loop targets, ``with`` and
    ``except`` aliases, nested definitions, and imports inside the body.
    """
    bound: set[str] = {"self", "cls"}
    args = fn.args
    for arg in [*args.posonlyargs, *args.args, *args.kwonlyargs]:
        bound.add(arg.arg)
    if args.vararg:
        bound.add(args.vararg.arg)
    if args.kwarg:
        bound.add(args.kwarg.arg)

    for node in ast.walk(fn):
        if isinstance(node, ast.Name) and isinstance(node.ctx, (ast.Store, ast.Del)):
            bound.add(node.id)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            bound.add(node.name)
        elif isinstance(node, ast.ExceptHandler) and node.name:
            bound.add(node.name)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                bound.add(alias.asname or alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            for alias in node.names:
                bound.add(alias.asname or alias.name)
        elif isinstance(node, (ast.Global, ast.Nonlocal)):
            bound.update(node.names)
    return bound


# Input: fn (ast.FunctionDef | ast.AsyncFunctionDef)
# Output: set[str]
# Processing: Names the function reads but does not bind
# Changed by: Sourish
# Date: 2026-10-12
def _free_names(fn: ast.FunctionDef | ast.AsyncFunctionDef) -> tuple[set[str], set[str]]:
    """Free names, split into ``(runtime, annotation_only)``.

    The split is what makes a large share of real library code usable. A name
    used only in a type annotation is never evaluated at run time once
    ``from __future__ import annotations`` is in effect -- annotations become
    strings. So a helper annotated with a type it cannot import still *runs*,
    while one that calls a sibling function does not. Treating both as the same
    failure rejected 58 pydantic helpers over ``CoreSchema`` alone.

    Decorators count as runtime: they execute at definition time, so an
    unimported ``@cache`` breaks the paste like a missing import.
    """
    bound = _bound_locally(fn)
    builtin_names = set(dir(builtins))

    # Input: node (ast.AST)
    # Output: set[str]
    # Processing: Free names loaded in a subtree, minus locals and builtins
    # Changed by: Sourish
    # Date: 2026-10-12
    def names_in(node: ast.AST) -> set[str]:
        found: set[str] = set()
        for sub in ast.walk(node):
            if isinstance(sub, ast.Name) and isinstance(sub.ctx, ast.Load):
                if sub.id not in bound and sub.id not in builtin_names:
                    found.add(sub.id)
        return found

    runtime: set[str] = set()
    for statement in fn.body:
        runtime |= names_in(statement)
    for dec in fn.decorator_list:
        runtime |= names_in(dec)

    annotations: set[str] = set()
    args = fn.args
    for arg in [*args.posonlyargs, *args.args, *args.kwonlyargs, args.vararg, args.kwarg]:
        if arg is not None and arg.annotation is not None:
            annotations |= names_in(arg.annotation)
    if fn.returns is not None:
        annotations |= names_in(fn.returns)

    # A name in both places is a runtime name: the annotation is incidental.
    return runtime, annotations - runtime


# Input: fn (ast.FunctionDef | ast.AsyncFunctionDef), tree (ast.Module)
# Output: tuple[tuple[str, ...], tuple[str, ...]]
# Processing: Return (required_imports, unresolved_names) for one function
# Changed by: Sourish
# Date: 2026-10-12
def analyse_dependencies(
    fn: ast.FunctionDef | ast.AsyncFunctionDef, tree: ast.Module
) -> tuple[tuple[str, ...], tuple[str, ...], tuple[str, ...]]:
    """Return ``(required_imports, unresolved_names, annotation_only_names)``."""
    imports = _module_imports(tree)
    required: set[str] = set()
    unresolved: set[str] = set()
    annotation_only: set[str] = set()

    runtime_names, annotation_names = _free_names(fn)

    for name in sorted(runtime_names):
        if name in imports:
            required.add(imports[name])
        elif name == fn.name:
            continue  # direct recursion is fine
        else:
            # An import would have resolved it. Anything else -- a sibling
            # function, a module constant, a class alongside it -- is left
            # behind when the function is lifted out.
            unresolved.add(name)

    for name in sorted(annotation_names):
        if name in imports:
            required.add(imports[name])
        elif name != fn.name:
            annotation_only.add(name)

    return tuple(sorted(required)), tuple(sorted(unresolved)), tuple(sorted(annotation_only))


# Input: source (str), library_rules (bool)
# Output: list[ExtractedUnit]
# Processing: Exact extraction of module-level functions via the AST.
# Changed by: Sourish
# Date: 2026-10-22
def extract_python(source: str, *, library_rules: bool = True) -> list[ExtractedUnit]:
    """Exact extraction of module-level functions via the AST.

    Module level only: a method pulled out of a class is not independently
    usable, and recommending one produces code that does not run.
    ``library_rules=False`` keeps private and very short or long functions
    too, for callers that map a whole file rather than pick
    library candidates.
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
        if library_rules and node.name.startswith("_"):
            continue  # private by convention
        start, end = node.lineno, (node.end_lineno or node.lineno)
        if library_rules and not MIN_FUNCTION_LINES <= (end - start + 1) <= MAX_FUNCTION_LINES:
            continue
        required, unresolved, annotation_only = analyse_dependencies(node, tree)
        units.append(
            ExtractedUnit(
                name=node.name,
                code=ast.get_source_segment(source, node) or "",
                language="python",
                start_line=start,
                end_line=end,
                signature=_python_signature(node),
                docstring=ast.get_docstring(node) or "",
                dependencies_analysed=True,
                required_imports=required,
                unresolved_names=unresolved,
                annotation_only_names=annotation_only,
                needs_review=bool(annotation_only),
            )
        )
    return units


# Input: source (str), line (int)
# Output: str
# Processing: Read the doc comment immediately above a 1-indexed line
# Changed by: Sourish
# Date: 2026-10-12
def doc_comment_above(source: str, line: int) -> str:
    """Pull the description out of the comment block above a declaration.

    Handles both shapes these languages use: a ``/** ... */`` block and a run
    of ``//`` lines. Leading asterisks, JSDoc tags and blank padding are
    stripped, and only the prose is kept -- a description full of ``@param``
    noise embeds badly, so the tags are dropped rather than indexed.

    Returns an empty string when there is no comment, which the scorer treats
    as a blocker rather than a shrug.
    """
    lines = source.splitlines()
    index = line - 2  # the line directly above the declaration, 0-indexed
    collected: list[str] = []

    while index >= 0 and not lines[index].strip():
        index -= 1
    if index < 0:
        return ""

    stripped = lines[index].strip()

    if stripped.endswith("*/"):
        # Walk back to the opening delimiter.
        block: list[str] = []
        while index >= 0:
            current = lines[index].strip()
            block.append(current)
            if current.startswith("/*"):
                break
            index -= 1
        else:
            return ""
        for raw in reversed(block):
            text = raw.removeprefix("/**").removeprefix("/*").removesuffix("*/")
            text = text.strip().removeprefix("*").strip()
            if text and not text.startswith("@"):
                collected.append(text)
    elif stripped.startswith("//"):
        while index >= 0 and lines[index].strip().startswith("//"):
            text = lines[index].strip().removeprefix("///").removeprefix("//").strip()
            if text and not text.startswith("@"):
                collected.append(text)
            index -= 1
        collected.reverse()
    else:
        return ""

    return "\n".join(collected).strip()


_BRACE_IMPORT_PATTERNS = (
    # import defaultName from "mod"  /  import * as ns from "mod"
    re.compile(r"""^\s*import\s+(?:\*\s+as\s+)?(?P<name>\w+)\s+from\s+['"][^'"]+['"]""", re.M),
    # import { a, b as c } from "mod"
    re.compile(r"""^\s*import\s*\{(?P<names>[^}]+)\}\s*from\s+['"][^'"]+['"]""", re.M),
    # const x = require("mod")  /  const { a, b } = require("mod")
    re.compile(r"""^\s*(?:const|let|var)\s+(?:\{(?P<names2>[^}]+)\}|(?P<name2>\w+))\s*=\s*require\(""", re.M),
)

_BRACE_DECL_PATTERNS = (
    re.compile(r"^\s*(?:export\s+)?(?:async\s+)?function\s+(\w+)", re.M),
    re.compile(r"^\s*(?:export\s+)?(?:const|let|var)\s+(\w+)", re.M),
    re.compile(r"^\s*(?:export\s+)?class\s+(\w+)", re.M),
    re.compile(r"^\s*#define\s+(\w+)", re.M),
    re.compile(r"^\s*(?:typedef\s+)?(?:struct|class|enum)\s+(\w+)", re.M),
)

_IDENTIFIER = re.compile(r"\b[A-Za-z_]\w*\b")

_FREE_IDENTIFIER = re.compile(r"(?<![.\w])(?<!->)(?<!::)([A-Za-z_]\w*)")
"""An identifier that is not a member or scope access.

``obj.size()``, ``p->next`` and ``std::vector`` all resolve through whatever
owns them, so the name after the operator says nothing about what the unit
needs from its surroundings. Counting those as free names reported ``size``
and ``length`` as missing on almost every unit."""

_BRACE_LOCAL_DECL = re.compile(
    r"\b(?:const\s+|constexpr\s+|static\s+)*"
    # The type slot must not be a statement keyword, or `return data_;` reads
    # as a declaration of `data_` and the member reference it really is
    # disappears.
    r"(?!(?:return|throw|new|delete|case|else|do|typedef|using|namespace|sizeof|"
    r"co_return|co_await|co_yield|export|default|goto|break|continue)\b)"
    r"(?:let|var|auto|int|unsigned|float|double|char|bool|size_t|long|short|"
    r"std::\w+|[A-Za-z_]\w*(?:::\w+)*)"
    r"(?:\s*<[^;{}<>]*>)?"          # template arguments: std::vector<int>
    r"(?:\s*[*&]+)?"                 # pointer or reference: const char* p
    r"\s+([a-z_]\w*)\s*(?=[=;),\[])"
)
"""A variable declared inside the unit: bound by the code as pasted.

Has to see past the type's template arguments and pointer markers, or
``std::vector<int> v;`` leaves ``v`` looking like a name borrowed from the
surrounding module."""

_CPP_QUALIFIERS = frozenset(
    "const volatile static constexpr inline unsigned signed struct class "
    "typename enum union register mutable restrict".split()
)
"""Words that can precede a type without being one."""

_BRACE_PARAM_LIST = re.compile(r"^[^(){]*\(([^)]*)\)", re.S)

_BRACE_INNER_PARAMS = re.compile(
    r"\(([^()]*)\)\s*(?::\s*[\w<>\[\],.\s|]+?)?\s*(?:=>|\{)"
)
"""Any parameter list in the body, not just the outer signature.

Callbacks and arrows declare their own parameters, and binding only the outer
signature reported those as dependencies on the module: ``baseArity`` was
rejected over ``a`` and ``b``, which its own inner function declares."""

_BRACE_ARROW_SINGLE = re.compile(r"(?<![.\w])([A-Za-z_]\w*)\s*=>")
"""``x => x * 2`` -- a single-parameter arrow with no parentheses."""

_BRACE_DESTRUCTURE = re.compile(r"(?:const|let|var)\s*[{\[]([^}\]]*)[}\]]")
"""``const { a, b } = opts`` binds a and b."""

_BRACE_CATCH = re.compile(r"catch\s*\(\s*([A-Za-z_]\w*)")
_BRACE_TEMPLATE_PARAMS = re.compile(r"template\s*<([^>]*)>")

# Language keywords and globals a lifted function can rely on existing.
_BRACE_AMBIENT = frozenset(
    """
    if else for while do switch case break continue return function class const let var new
    delete typeof instanceof void this super null undefined true false try catch finally throw
    async await yield of in static get set extends implements interface type enum export import
    default from as public private protected readonly namespace declare abstract
    Object Array String Number Boolean Math JSON Date RegExp Error TypeError RangeError Promise
    Map Set WeakMap WeakSet Symbol BigInt Infinity NaN isNaN parseInt parseFloat encodeURIComponent
    decodeURIComponent console window document globalThis process module exports require
    int float double char bool void long short unsigned signed size_t auto struct enum union
    template typename namespace using public private protected virtual override final inline
    static_cast dynamic_cast reinterpret_cast const_cast sizeof nullptr std operator friend
    explicit constexpr noexcept mutable decltype
    __global__ __device__ __host__ __shared__ __constant__ __restrict__
    threadIdx blockIdx blockDim gridDim warpSize
    and or not xor bitand bitor compl and_eq or_eq xor_eq not_eq
    arguments
    true_type false_type string string_view wstring size_type value_type difference_type
    iterator const_iterator vector array basic_string char_traits allocator
    uint8_t uint16_t uint32_t uint64_t int8_t int16_t int32_t int64_t intptr_t uintptr_t
    ptrdiff_t nullptr_t FILE
    number boolean any unknown never object symbol bigint
    Record Partial Required Readonly Pick Omit Exclude Extract NonNullable ReturnType
    Iterable Iterator AsyncIterable ArrayLike
    Uint8Array Uint16Array Uint32Array Int8Array Int16Array Int32Array
    Float32Array Float64Array BigInt64Array BigUint64Array ArrayBuffer SharedArrayBuffer
    DataView Buffer Blob File FormData URL URLSearchParams TextEncoder TextDecoder
    btoa atob setTimeout clearTimeout setInterval clearInterval queueMicrotask
    structuredClone fetch Headers Request Response AbortController AbortSignal
    Proxy Reflect Intl escape unescape isFinite Function arguments
    """.split()
)


# Input: source (str)
# Output: set[str]
# Processing: Names a brace-language module imports or defines at top level
# Changed by: Sourish
# Date: 2026-10-12
def _brace_module_names(source: str) -> tuple[set[str], set[str]]:
    """Return ``(imported, defined_locally)`` for a brace-language module.

    Imported names are satisfiable by shipping the import alongside the code.
    Locally defined ones are not: lifting the function leaves them behind.
    """
    imported: set[str] = set()
    for pattern in _BRACE_IMPORT_PATTERNS:
        for match in pattern.finditer(source):
            groups = match.groupdict()
            for key in ("name", "name2"):
                if groups.get(key):
                    imported.add(groups[key])
            for key in ("names", "names2"):
                if groups.get(key):
                    for piece in groups[key].split(","):
                        bound = piece.split(" as ")[-1].strip()
                        if bound:
                            imported.add(bound)

    defined: set[str] = set()
    for pattern in _BRACE_DECL_PATTERNS:
        defined.update(match.group(1) for match in pattern.finditer(source))

    return imported, defined - imported


# Input: code (str)
# Output: str
# Processing: Blank out string literals and comments, keeping offsets
# Changed by: Sourish
# Date: 2026-10-12
def _strip_literals(code: str) -> str:
    """Replace the contents of strings and comments with spaces.

    Required before any identifier scan: the words inside a literal are not
    names. Without this, ``fs.readFileSync(path, "utf8")`` reports ``utf8`` as
    an unresolved dependency, and a unit holding an error message or a regex
    is rejected over its own prose.

    Characters are replaced one for one rather than removed, so every offset
    into the result still matches the original -- the signature split on the
    first ``{`` depends on that.
    """
    out = list(code)
    index = 0
    length = len(code)
    while index < length:
        char = code[index]
        if char in "\"'`":
            quote = char
            index += 1
            while index < length:
                if code[index] == "\\":
                    out[index] = " "
                    if index + 1 < length:
                        out[index + 1] = " "
                    index += 2
                    continue
                if code[index] == quote:
                    break
                if code[index] != "\n":
                    out[index] = " "
                index += 1
            index += 1
            continue
        if char == "/" and index + 1 < length and code[index + 1] == "/":
            while index < length and code[index] != "\n":
                out[index] = " "
                index += 1
            continue
        if char == "/" and index + 1 < length and code[index + 1] == "*":
            while index < length and not (code[index] == "*" and index + 1 < length and code[index + 1] == "/"):
                if code[index] != "\n":
                    out[index] = " "
                index += 1
            for offset in range(2):
                if index + offset < length:
                    out[index + offset] = " "
            index += 2
            continue
        index += 1
    return "".join(out)


# Input: parameter_text (str), language (str)
# Output: set[str]
# Processing: Names bound by a brace-language parameter list
# Changed by: Sourish
# Date: 2026-10-12
def _brace_parameter_names(parameter_text: str, language: str) -> set[str]:
    """Parameter *names* only, not the types beside them.

    Binding every identifier in the list would bind the types too, and an
    unresolvable parameter type breaks pasted code just as surely as an
    unresolvable call does.

    Which identifier is the name depends on the language, and the three
    conventions disagree:

    * C++ and CUDA write ``Type name``, so the name is the chunk's last
      identifier -- and a chunk with only one (``const Char*``, ``int&``)
      declares an unnamed parameter and binds nothing.
    * TypeScript writes ``name: Type``, so the name is the *first*.
    * JavaScript has no types, so a chunk's single identifier is the name.

    Reading all three the C++ way bound ``string`` from ``a: string`` and left
    plain ``a`` unbound, which reported a function's own parameters as
    dependencies on its module.

    The rule errs toward binding too little, which rejects a usable unit. That
    is the safer direction: the opposite error admits one that does not
    compile.
    """
    names: set[str] = set()
    depth = 0
    chunk: list[str] = []
    chunks: list[str] = []
    for char in parameter_text:
        if char in "<([{":
            depth += 1
        elif char in ">)]}":
            depth -= 1
        if char == "," and depth <= 0:
            chunks.append("".join(chunk))
            chunk = []
            continue
        chunk.append(char)
    chunks.append("".join(chunk))

    for piece in chunks:
        # A default value is not part of the name.
        identifiers = _IDENTIFIER.findall(piece.split("=")[0])
        if not identifiers:
            continue
        if language in {"cpp", "cuda"}:
            # Qualifiers are not the type, so `const Char*` holds one type
            # name and no parameter name -- counting `const` toward the two
            # bound `Char`, hiding a type the pasted code still has to resolve.
            identifiers = [n for n in identifiers if n not in _CPP_QUALIFIERS]
            if len(identifiers) >= 2:
                names.add(identifiers[-1])
        else:
            names.add(identifiers[0])
    return names


# Input: source (str), code (str), own_name (str), language (str)
# Output: tuple[tuple[str, ...], tuple[str, ...]]
# Processing: Heuristic dependency split for a brace-language unit
# Changed by: Sourish
# Date: 2026-10-12
def analyse_brace_dependencies(
    source: str, code: str, own_name: str, language: str = "javascript"
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """``(required_imports, unresolved_names)`` for a brace-language unit.

    A heuristic, not an analysis. There is no AST here, so this works over
    identifiers and module-level declarations, and it cannot see through a
    dynamic ``require``, a macro, or a template instantiation. Every unit it
    admits is marked ``needs_review`` for that reason -- the guarantee is
    weaker than the Python path's and the record should say so rather than
    presenting the two as equivalent.
    """
    imported, _defined = _brace_module_names(source)

    # Every identifier scan below runs on the literal-free text.
    code = _strip_literals(code)

    # Names the unit binds itself: its declarations, parameters, template
    # parameters and locals. Anything bound here travels with the pasted code.
    bound = {own_name}
    for pattern in _BRACE_DECL_PATTERNS:
        bound.update(match.group(1) for match in pattern.finditer(code))
    bound.update(_BRACE_LOCAL_DECL.findall(code))

    head = code.split("{", 1)[0]
    for match in _BRACE_TEMPLATE_PARAMS.finditer(head):
        bound.update(_IDENTIFIER.findall(match.group(1)))
    params = _BRACE_PARAM_LIST.search(head)
    if params:
        bound.update(_brace_parameter_names(params.group(1), language))
    # Parameters declared anywhere inside the body: callbacks, arrows, nested
    # functions. These are bound by the pasted code, not borrowed from it.
    for match in _BRACE_INNER_PARAMS.finditer(code):
        bound.update(_brace_parameter_names(match.group(1), language))
    bound.update(_BRACE_ARROW_SINGLE.findall(code))
    for match in _BRACE_DESTRUCTURE.finditer(code):
        bound.update(_IDENTIFIER.findall(match.group(1)))
    bound.update(_BRACE_CATCH.findall(code))
    # Deliberately *not* binding every identifier in the signature. Doing that
    # hid the return type and any macro in front of it, and those have to
    # resolve for the pasted code to compile: it let
    # `FMT_CONSTEXPR void on_text(...)` through as self-contained while
    # FMT_CONSTEXPR is a macro from the source project's own headers.

    required: set[str] = set()
    unresolved: set[str] = set()
    for name in sorted(set(_FREE_IDENTIFIER.findall(code))):
        if name in bound or name in _BRACE_AMBIENT:
            continue
        if name in imported:
            required.add(name)
        else:
            # Everything else is unresolved -- including a name this module
            # never declared at top level. The earlier version reported only
            # names it could find in the module's own declarations and silently
            # dropped the rest, so a member variable (``data_``), a macro
            # (``FMT_THROW``), a namespace (``detail``) or a type from a header
            # all vanished and the unit reported itself self-contained. 223 of
            # 303 brace-language entries carried at least one such name. It is
            # the same false pass ``dependencies_analysed`` was added to stop,
            # one level further in: the check ran, and did not look at
            # everything it needed to.
            unresolved.add(name)

    # Imports are reported as names, not statements: reconstructing the exact
    # import line needs the module path, and guessing it would be worse than
    # naming what is needed and letting the insert step resolve it.
    return tuple(sorted(required)), tuple(sorted(unresolved))


# Input: source (str), language (str), library_rules (bool)
# Output: list[ExtractedUnit]
# Processing: Heuristic extraction for brace languages. Marked ``needs_review``.
# Changed by: Sourish
# Date: 2026-10-22
def extract_braced(source: str, language: str, *, library_rules: bool = True) -> list[ExtractedUnit]:
    """Heuristic extraction for brace languages. Marked ``needs_review``.

    Walks from a plausible declaration to its matching closing brace, skipping
    string and comment content. It is right often enough to seed a library and
    wrong often enough that nothing it produces should ship unreviewed.
    """
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
        if library_rules and not MIN_FUNCTION_LINES <= (end_line - start_line + 1) <= MAX_FUNCTION_LINES:
            continue
        body = "\n".join(lines[start_line - 1 : end_line])
        required, unresolved = analyse_brace_dependencies(source, body, name, language)
        units.append(
            ExtractedUnit(
                name=name,
                code=body,
                language=language,
                start_line=start_line,
                end_line=end_line,
                signature=f"({args.strip()})" if args else "()",
                docstring=doc_comment_above(source, start_line),
                dependencies_analysed=True,
                required_imports=required,
                unresolved_names=unresolved,
                needs_review=True,
            )
        )
    return units


# External references a markup or stylesheet file can make. A template is not
# a closed unit just because it has no function calls: an HTML file pulling in
# a script, or a stylesheet importing another, is as incomplete as a Python
# helper missing an import.
_HTML_TAG = re.compile(r"<(?P<tag>[A-Za-z][\w-]*)(?P<attrs>[^>]*)>")
_HTML_ATTR = re.compile(
    r"""(?P<name>src|href)\s*=\s*(?P<quote>["'])(?P<target>[^"']+)(?P=quote)""",
    re.IGNORECASE,
)
_CSS_IMPORT = re.compile(
    r"""@(?:import|use|forward)\s+(?:url\()?\s*(?P<quote>["']?)(?P<target>[^"')\s;]+)(?P=quote)""",
    re.IGNORECASE,
)
"""``@use`` and ``@forward`` as well as ``@import``.

The Sass module syntax replaced ``@import``, so checking only the old keyword
passed 136 stylesheets as self-contained while they pulled in siblings --
including barrel files whose entire content is a list of ``@forward`` lines.
A bare target like ``@forward "button"`` is a relative module reference, so it
lands in the local bucket and is rejected, which is correct: the file is a
re-export of code the library does not carry."""
_CSS_URL = re.compile(
    r"""url\(\s*(?P<quote>["']?)(?P<target>[^"')]+)(?P=quote)\s*\)""", re.IGNORECASE
)

_NON_ASSET_SCHEMES = ("data:", "mailto:", "tel:", "javascript:", "about:", "#")


# Input: target (str)
# Output: bool
# Processing: Whether a reference points at a fetchable remote asset
# Changed by: Sourish
# Date: 2026-10-12
def _is_remote_reference(target: str) -> bool:
    """True for an absolute URL, including a protocol-relative one.

    A remote reference is a dependency the entry can *declare* -- a CDN
    stylesheet or font still resolves wherever the template is pasted. A
    relative one cannot: it names a file sitting next to the original that the
    library does not ship.
    """
    lowered = target.strip().lower()
    return lowered.startswith(("http://", "https://", "//"))


# Input: source (str), language (str)
# Output: tuple[tuple[str, ...], tuple[str, ...]]
# Processing: Split a template's external references into declarable and unresolvable
# Changed by: Sourish
# Date: 2026-10-12
def analyse_template_dependencies(source: str, language: str) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Return ``(remote_references, local_references)`` for a whole-file template.

    The same question the Python and brace analysers answer, asked of markup:
    does this unit depend on something that will not travel with it?

    Remote references resolve from anywhere, so they are dependencies to
    declare. Relative references name a file beside the original -- a local
    stylesheet, a sibling script, an image in the repository -- and the
    library copies code, not directories, so they do not travel. They are
    returned as unresolved and the self-containment gate rejects them, exactly
    as it rejects a helper calling a function left behind in its module.

    Data URIs, fragments and ``mailto:`` are not assets and are ignored.
    """
    remote: list[str] = []
    local: list[str] = []

    # Input: target (str)
    # Output: None
    # Processing: File one reference into the remote or local bucket
    # Changed by: Sourish
    # Date: 2026-10-12
    def record(target: str) -> None:
        target = target.strip()
        if not target or target.lower().startswith(_NON_ASSET_SCHEMES):
            return
        bucket = remote if _is_remote_reference(target) else local
        if target not in bucket:
            bucket.append(target)

    if language == "html":
        for tag in _HTML_TAG.finditer(source):
            name = tag.group("tag").lower()
            for attr in _HTML_ATTR.finditer(tag.group("attrs")):
                # ``src`` always names an asset the template needs rendered.
                # ``href`` only does on <link>; on <a> and <area> it is a
                # navigation target, and a template that links to a page it
                # does not carry is complete -- rejecting a navbar for
                # pointing at "/" would be the annotation-blocker mistake
                # again, in markup.
                if attr.group("name").lower() == "src" or name == "link":
                    record(attr.group("target"))
    elif language == "css":
        for pattern in (_CSS_IMPORT, _CSS_URL):
            for match in pattern.finditer(source):
                record(match.group("target"))

    return tuple(remote), tuple(local)


# Input: source (str), path (str), language (str), max_lines (int)
# Output: ExtractedUnit | None
# Processing: Treat a whole file as a template candidate.
# Changed by: Sourish
# Date: 2026-10-12
def extract_file_as_template(
    source: str, path: str, language: str, *, max_lines: int = 400
) -> ExtractedUnit | None:
    """Treat a whole file as a template candidate.

    How HTML, CSS and scaffolding enter the library: there is no function to
    slice, the file *is* the unit. No signature, per the spec.

    The dependency analysis here is why HTML and CSS can enter the library at
    all. ``dependencies_analysed`` defaults to False and
    :attr:`ExtractedUnit.self_contained` rejects anything unanalysed, so for as
    long as this extractor set neither flag, every template it produced was
    dropped by the harvester and the library held nothing but helpers. Setting
    the flag without doing the work would have rebuilt the false-pass defect
    the flag exists to prevent -- a template really can depend on a script or
    a stylesheet it does not carry -- so the references are resolved instead.
    """
    lines = source.splitlines()
    if not MIN_FUNCTION_LINES <= len(lines) <= max_lines:
        return None
    remote, local = analyse_template_dependencies(source, language)
    return ExtractedUnit(
        name=path.rsplit("/", 1)[-1],
        code=source,
        language=language,
        start_line=1,
        end_line=len(lines),
        is_template=True,
        needs_review=True,
        dependencies_analysed=True,
        required_imports=remote,
        unresolved_names=local,
    )


# Node's built-in modules. A builtin is not a dependency: "fs" needs no
# install, so reporting it as one would make every Node helper look like it
# carries friction it does not have. Kept explicit because there is no
# runtime list to query the way Python has sys.stdlib_module_names.
NODE_BUILTINS = frozenset(
    {
        "assert", "async_hooks", "buffer", "child_process", "cluster", "console",
        "constants", "crypto", "dgram", "diagnostics_channel", "dns", "domain",
        "events", "fs", "http", "http2", "https", "inspector", "module", "net",
        "os", "path", "perf_hooks", "process", "punycode", "querystring",
        "readline", "repl", "stream", "string_decoder", "sys", "timers", "tls",
        "trace_events", "tty", "url", "util", "v8", "vm", "wasi", "worker_threads",
        "zlib",
    }
)


# Input: statement (str)
# Output: str | None
# Processing: Top-level package a Python import statement names
# Changed by: Sourish
# Date: 2026-10-12
def _python_import_root(statement: str) -> str | None:
    """The installable package an import statement refers to, or None.

    Returns None for a relative import and for anything in the standard
    library, since neither is something a project has to install.
    """
    parts = statement.split()
    if not parts:
        return None
    if parts[0] == "from":
        module = parts[1] if len(parts) > 1 else ""
    elif parts[0] == "import":
        module = parts[1] if len(parts) > 1 else ""
    else:
        return None
    if module.startswith("."):
        return None  # relative: inside the source project, not installable
    root = module.split(".", 1)[0]
    if not root or root == "__future__" or root in sys.stdlib_module_names:
        return None
    return root


# Input: required_imports (Sequence[str]), language (str), local_modules (Collection[str])
# Output: tuple[str, ...]
# Processing: Installable package names behind a unit's imports
# Changed by: Sourish
# Date: 2026-10-12
def dependency_names(
    required_imports, language: str, *, local_modules=()
) -> tuple[str, ...]:
    """The packages a project must install to use this unit. Python only.

    Distinct from ``required_imports``, which is the literal text to paste.
    This is the list the ranker reads: ``Entry.dependencies`` feeds
    :func:`ml.retrieval.filters.classify_dependency_compat`, which compares it
    against what the requesting project already has. Until this existed the
    field was never populated, so ``dependency_compat`` was constant across
    the whole library and the feature could not be learned from.

    Three exclusions, each because including it would record a guess as a fact:

    **Standard library and builtins.** They need no install, so counting them
    would report friction that is not there.

    **First-party modules.** A repository's own top-level module -- ``utils``,
    ``release`` -- is an absolute import that resolves inside the project and
    is not installable. ``local_modules`` carries the names found in the source
    repository so they can be dropped; without it a harvest reports ``utils``
    as a package 14 times.

    **Every language but Python.** The brace analyser documents that it
    reports imports "as names, not statements", because recovering the module
    path would mean guessing. Those names are *bindings*: ``AxiosError`` comes
    from ``./core/AxiosError`` and ``_`` is a local alias for lodash. Treating
    them as package names produced exactly the fabrications that comment warns
    about. C++ and CUDA capture no includes at all. For those languages the
    package list is unknown rather than empty, and ``missing_fields`` records
    it as such.
    """
    if language != "python":
        return ()

    excluded = {name.lower() for name in local_modules}
    names: list[str] = []
    for statement in required_imports:
        package = _python_import_root(statement)
        if not package or package.lower() in excluded:
            continue
        if package not in names:
            names.append(package)
    return tuple(names)


# Input: source (str), path (str), want_template (bool), library_rules (bool)
# Output: list[ExtractedUnit]
# Processing: Extract candidates from one file, dispatching on its language.
# Changed by: Sourish
# Date: 2026-10-22
def extract(source: str, path: str, *, want_template: bool = False, library_rules: bool = True) -> list[ExtractedUnit]:
    """Extract candidates from one file, dispatching on its language.

    ``library_rules=False`` returns every top-level function, not only those
    the library would accept (see :func:`extract_python`)."""
    language = language_for_path(path)
    if language is None:
        return []
    if want_template or language in {"html", "css"}:
        unit = extract_file_as_template(source, path, language)
        return [unit] if unit else []
    if language == "python":
        return extract_python(source, library_rules=library_rules)
    if language in BRACE_LANGUAGES:
        return extract_braced(source, language, library_rules=library_rules)
    return []
