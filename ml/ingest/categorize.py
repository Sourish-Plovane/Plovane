"""Pick a library category for a harvested function from its name, path and text.

The harvest used to stamp every entry ``data-processing``, so the library could
not be browsed by kind. This guesses one of ``quota.CATEGORIES`` from plain
signals: the language, the file path, the function name and its first
docstring line. It is a deterministic rule list, not a model: the first rule
whose words appear wins, and anything unmatched stays ``data-processing``.
"""

from __future__ import annotations

import re

from ml.ingest.quota import CATEGORIES

DEFAULT = "data-processing"

# (category, words). A word matches a whole token of the name (split on case and
# underscores), a path segment, or a word of the description.
RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("testing", ("test", "tests", "mock", "fixture", "assert", "pytest")),
    ("auth-security", ("auth", "token", "password", "hash", "csrf", "jwt", "crypto", "secret", "signature",
                       "sanitize", "escape", "xss", "permission", "credential", "cookie", "session")),
    ("api-http", ("http", "https", "url", "uri", "request", "response", "fetch", "axios", "header", "headers",
                  "query", "querystring", "route", "routing", "endpoint", "redirect", "status", "mime",
                  "blueprint", "wsgi", "xhr", "adapter", "interceptor", "cors")),
    ("validation-parsing", ("is", "valid", "validate", "validator", "validation", "parse", "parser", "schema",
                            "check", "email", "ip", "uuid", "isbn", "iban", "regex", "match", "matches",
                            "serializer", "serialize", "deserialize", "coerce", "json")),
    ("concurrency-async", ("async", "await", "promise", "thread", "threads", "lock", "debounce",
                           "throttle", "delay", "defer", "timeout", "sleep", "concurrent", "retry")),
    ("file-io", ("file", "files", "path", "paths", "dir", "directory", "read", "write", "open", "stream",
                 "fs", "io", "mkdir", "tempfile", "atomic")),
    ("logging-observability", ("log", "logger", "logging", "warn", "warning", "debug", "trace", "profile")),
    ("math-numeric", ("math", "sum", "mean", "median", "round", "clamp", "random", "number", "numbers",
                      "int", "float", "add", "subtract", "multiply", "divide", "min", "max", "range",
                      "inc", "dec", "modulo", "average", "statistics", "sqrt", "prime")),
    ("styling-layout", ("css", "style", "styles", "color", "theme", "layout", "grid", "flex", "spacing")),
    ("ui-components", ("dom", "element", "component", "tooltip", "modal", "dropdown", "button", "carousel",
                       "popover", "collapse", "offcanvas", "scrollspy", "toast", "html")),
    ("build-tooling", ("build", "plugin", "cli", "setup", "deprecate", "deprecated")),
)

# Words that settle the category even when they only appear in the description.
DESCRIPTION_RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("api-http", ("http", "https", "url", "urls", "headers", "querystring", "wsgi")),
    ("auth-security", ("password", "csrf", "jwt", "encrypt", "decrypt", "credentials")),
    ("file-io", ("file", "files", "filename", "directory", "filesystem")),
    ("concurrency-async", ("promise", "asynchronous", "thread", "threads", "debounced", "throttled")),
    ("validation-parsing", ("validate", "validates", "validator", "parse", "parses", "schema")),
)

_SPLIT = re.compile(r"[A-Z]?[a-z]+|[A-Z]+(?![a-z])|\d+")


# Input: name (str)
# Output: set[str]
# Processing: The lowercase words of an identifier (camelCase, snake_case or kebab-case).
# Changed by: Claude Code
# Date: 2026-11-15
def name_words(name: str) -> set[str]:
    return {w.lower() for w in _SPLIT.findall(name or "")}


# Input: name (str), language (str), path (str), description (str | None)
# Output: str
# Processing: The category of one function: CSS goes to styling, then the first rule whose words appear.
# Changed by: Claude Code
# Date: 2026-11-15
def categorize(name: str, language: str, path: str = "", description: str | None = None) -> str:
    if language in ("css", "scss"):
        return "styling-layout"
    words = name_words(name)
    segments = {s.lower() for s in re.split(r"[/\\._-]", path or "") if s}
    text = set(re.findall(r"[a-z]+", (description or "").lower()))
    for category, needles in RULES:
        if words.intersection(needles) or segments.intersection(needles):
            return category
    for category, needles in DESCRIPTION_RULES:  # a weaker signal, so only unmistakable words
        if text.intersection(needles):
            return category
    return DEFAULT if DEFAULT in CATEGORIES else CATEGORIES[0]
