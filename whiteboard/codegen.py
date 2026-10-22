"""The code the whiteboard writes into a project, and how it finds it again.

Generated code sits between marker comments so a later command can update or
remove exactly what an earlier one wrote, and nothing else:

    # plovane:begin endpoint payments:POST /charge
    ...
    # plovane:end endpoint payments:POST /charge

Everything generated follows the repository's five-field comment standard,
so a project built with the whiteboard passes the same check as this one.
"""

from __future__ import annotations

import os
import re
from datetime import date
from typing import Any

from ml.ingest.extract import EXTENSION_LANGUAGE
from whiteboard.registry import OpError

LINE_COMMENT = {"python": "#", "javascript": "//", "typescript": "//", "cpp": "//"}
AUTHOR = "Plovane whiteboard"


# Input: path (str)
# Output: str | None
# Processing: The language of a source file from its extension.
# Changed by: Sourish
# Date: 2026-10-22
def language_of(path: str) -> str | None:
    """By extension only. The library's ``language_for_path`` also skips
    tests, docs, examples and vendored folders, which is right for harvesting
    and wrong here: a project's tests are part of the project."""
    lowered = path.lower()
    if lowered.endswith((".jsx", ".mjs", ".cjs")):
        return "javascript"
    if lowered.endswith(".tsx"):
        return "typescript"
    for extension, language in EXTENSION_LANGUAGE.items():
        if lowered.endswith(extension):
            return language
    return None


# Input: language (str | None)
# Output: str
# Processing: The line-comment prefix for a language.
# Changed by: Sourish
# Date: 2026-10-22
def comment(language: str | None) -> str:
    return LINE_COMMENT.get(language or "", "#")


# Input: language (str | None), inputs (str), output (str), processing (str), indent (str)
# Output: str
# Processing: The five-field comment block in a language's comment syntax.
# Changed by: Sourish
# Date: 2026-10-22
def standard_block(language: str | None, inputs: str, output: str, processing: str, indent: str = "") -> str:
    c = comment(language)
    if language in ("javascript", "typescript", "cpp"):
        return (f"{indent}/**\n{indent} * Input: {inputs}\n{indent} * Output: {output}\n"
                f"{indent} * Processing: {processing}\n{indent} * Changed by: {AUTHOR}\n"
                f"{indent} * Date: {date.today().isoformat()}\n{indent} */\n")
    return (f"{indent}{c} Input: {inputs}\n{indent}{c} Output: {output}\n{indent}{c} Processing: {processing}\n"
            f"{indent}{c} Changed by: {AUTHOR}\n{indent}{c} Date: {date.today().isoformat()}\n")


# Input: language (str | None), key (str), body (str)
# Output: str
# Processing: Wrap generated code in begin/end markers.
# Changed by: Sourish
# Date: 2026-10-22
def marked(language: str | None, key: str, body: str) -> str:
    c = comment(language)
    return f"{c} plovane:begin {key}\n{body.rstrip()}\n{c} plovane:end {key}\n"


# Input: text (str), key (str)
# Output: tuple[int, int] | None
# Processing: The first and last line index of a marked block.
# Changed by: Sourish
# Date: 2026-10-22
def find_marked(text: str, key: str) -> tuple[int, int] | None:
    lines = text.splitlines()
    begin = next((i for i, line in enumerate(lines) if line.strip().endswith(f"plovane:begin {key}")), None)
    if begin is None:
        return None
    end = next((i for i in range(begin, len(lines)) if lines[i].strip().endswith(f"plovane:end {key}")), None)
    return None if end is None else (begin, end)


# Input: text (str), key (str), replacement (str | None)
# Output: str
# Processing: Replace or remove a marked block; unchanged text if absent.
# Changed by: Sourish
# Date: 2026-10-22
def replace_marked(text: str, key: str, replacement: str | None) -> str:
    span = find_marked(text, key)
    if span is None:
        return text
    lines = text.splitlines(keepends=True)
    begin, end = span
    new = [] if replacement is None else [replacement if replacement.endswith("\n") else replacement + "\n"]
    if replacement is None:
        # Drop the blank lines that separated the block, so removal leaves no gap.
        while begin > 0 and not lines[begin - 1].strip():
            begin -= 1
    return "".join(lines[:begin] + new + lines[end + 1:])


# Input: text (str), block (str)
# Output: str
# Processing: Append a block at the end of a file with one blank line between.
# Changed by: Sourish
# Date: 2026-10-22
def append_block(text: str, block: str) -> str:
    stripped = text.rstrip("\n")
    return (stripped + "\n\n\n" if stripped else "") + block.rstrip("\n") + "\n"


# Input: path (str)
# Output: str
# Processing: The dotted Python module name for a .py path.
# Changed by: Sourish
# Date: 2026-10-22
def module_name(path: str) -> str:
    stem = path[:-3] if path.endswith(".py") else path
    parts = [p for p in stem.split("/") if p]
    if parts and parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


# Input: source (str), target (str)
# Output: str
# Processing: The import statement that makes source import target.
# Changed by: Sourish
# Date: 2026-10-22
def import_line(source: str, target: str) -> str:
    language = language_of(source)
    if language == "python":
        if language_of(target) != "python":
            raise OpError("INVALID_ARGS", f"a Python file cannot import {target}")
        return f"import {module_name(target)}"
    if language in ("javascript", "typescript"):
        rel = os.path.relpath(target, os.path.dirname(source) or ".").replace(os.sep, "/")
        rel = re.sub(r"\.(jsx?|tsx?|mjs|cjs)$", "", rel)
        rel = rel if rel.startswith(".") else "./" + rel
        name = re.sub(r"\W", "_", os.path.splitext(os.path.basename(target))[0])
        return f"import * as {name} from '{rel}';"
    if language == "cpp":
        rel = os.path.relpath(target, os.path.dirname(source) or ".").replace(os.sep, "/")
        return f'#include "{rel}"'
    raise OpError("INVALID_ARGS", f"cannot write imports for {source}")


# Input: text (str), line (str), language (str | None)
# Output: str
# Processing: Insert an import after the existing imports, unless present.
# Changed by: Sourish
# Date: 2026-10-22
def add_import(text: str, line: str, language: str | None) -> str:
    lines = text.splitlines(keepends=True)
    if any(existing.strip() == line for existing in lines):
        return text
    if language == "python":
        is_import = lambda s: s.startswith(("import ", "from "))  # noqa: E731
    else:
        is_import = lambda s: s.startswith(("import ", "#include")) or (  # noqa: E731
            "require(" in s and s.startswith(("const ", "let ", "var ")))
    insert_at = 0
    if language == "python":
        # After a module docstring and `from __future__` lines.
        stripped = text.lstrip()
        if stripped.startswith(('"""', "'''")):
            quote = stripped[:3]
            close = text.find(quote, text.find(quote) + 3)
            insert_at = text[: close + 3].count("\n") + 1 if close != -1 else 0
    for index, existing in enumerate(lines):
        if is_import(existing.strip()):
            insert_at = index + 1
    return "".join(lines[:insert_at] + [line + "\n"] + lines[insert_at:])


# Input: text (str), line (str)
# Output: str
# Processing: Remove an import statement if present.
# Changed by: Sourish
# Date: 2026-10-22
def remove_import(text: str, line: str) -> str:
    return "".join(l for l in text.splitlines(keepends=True) if l.strip() != line)


# -- File templates ----------------------------------------------------------------


# Input: name (str), path (str)
# Output: str
# Processing: Starter content for a Python module.
# Changed by: Sourish
# Date: 2026-10-22
def _python_module(name: str, path: str) -> str:
    return f'"""{name}."""\n\nfrom __future__ import annotations\n'


# Input: name (str), path (str)
# Output: str
# Processing: Starter content for a pytest file.
# Changed by: Sourish
# Date: 2026-10-22
def _python_test(name: str, path: str) -> str:
    return (f'"""Tests for {name.removeprefix("test_")}."""\n\n\n'
            + standard_block("python", "none", "None", "Placeholder test; replace with real checks.")
            + "def test_placeholder():\n    assert True\n")


# Input: name (str), path (str)
# Output: str
# Processing: Starter content for a Flask application.
# Changed by: Sourish
# Date: 2026-10-22
def _flask_app(name: str, path: str) -> str:
    return (f'"""{name}: Flask application."""\n\nfrom flask import Flask\n\napp = Flask(__name__)\n\n\n'
            + standard_block("python", "none", "dict", "Health check.")
            + '@app.get("/healthz")\ndef healthz():\n    return {"ok": True}\n')


# Input: name (str), path (str)
# Output: str
# Processing: Starter content for a JavaScript or TypeScript module.
# Changed by: Sourish
# Date: 2026-10-22
def _js_module(name: str, path: str) -> str:
    return f"// {name}\n\nexport {{}};\n"


# Input: name (str), path (str)
# Output: str
# Processing: Starter content for a React component.
# Changed by: Sourish
# Date: 2026-10-22
def _react_component(name: str, path: str) -> str:
    component = "".join(part.capitalize() for part in re.split(r"[^A-Za-z0-9]", name) if part) or "Component"
    return ("import React from 'react';\n\n"
            + standard_block("javascript", "props (object)", "JSX.Element", f"Render {component}.")
            + f"export default function {component}(props) {{\n  return <div>{component}</div>;\n}}\n")


FILE_TEMPLATES = {
    "python-module": _python_module,
    "python-test": _python_test,
    "flask-app": _flask_app,
    "js-module": _js_module,
    "ts-module": _js_module,
    "react-component": _react_component,
    "empty": lambda name, path: "",
}


# Input: path (str), template (str | None)
# Output: str
# Processing: Starter content for a new file, by template or extension.
# Changed by: Sourish
# Date: 2026-10-22
def file_content(path: str, template: str | None) -> str:
    name = os.path.splitext(os.path.basename(path))[0]
    if template is None:
        language = language_of(path)
        template = {"python": "python-test" if name.startswith("test_") else "python-module",
                    "javascript": "js-module", "typescript": "ts-module"}.get(language or "", "empty")
    if template not in FILE_TEMPLATES:
        raise OpError("INVALID_ARGS", f"unknown template {template!r}; templates: {sorted(FILE_TEMPLATES)}")
    return FILE_TEMPLATES[template](name, path)


# -- Endpoints -----------------------------------------------------------------------


# Input: method (str), route (str)
# Output: str
# Processing: A function name for a route, e.g. post_charge.
# Changed by: Sourish
# Date: 2026-10-22
def handler_name(method: str, route: str) -> str:
    words = [w for w in re.split(r"[^A-Za-z0-9]+", route) if w]
    return "_".join([method.lower(), *[w.lower() for w in words]]) or method.lower()


# Input: schema (Any)
# Output: str
# Processing: A schema shown in a comment: its name, or its fields.
# Changed by: Sourish
# Date: 2026-10-22
def describe_schema(schema: Any) -> str:
    if schema is None:
        return "none"
    if isinstance(schema, dict):
        return "{" + ", ".join(f"{k}: {v}" for k, v in schema.items()) + "}"
    return str(schema)


# Input: language (str), service (str)
# Output: str
# Processing: The top of a new routes file.
# Changed by: Sourish
# Date: 2026-10-22
def routes_header(language: str, service: str) -> str:
    if language == "python":
        return (f'"""Routes for the {service} service. Blocks between plovane markers are generated."""\n\n'
                "from flask import Blueprint, jsonify, request\n\n"
                f'bp = Blueprint("{re.sub(r"[^A-Za-z0-9_]", "_", service)}", __name__)\n')
    return (f"// Routes for the {service} service. Blocks between plovane markers are generated.\n"
            "const express = require('express');\n\nconst router = express.Router();\nmodule.exports = router;\n")


# Input: language (str), endpoint_id (str), method (str), route (str), request (Any), response (Any)
# Output: str
# Processing: A marked route handler that answers 501 until implemented.
# Changed by: Sourish
# Date: 2026-10-22
def handler_code(language: str, endpoint_id: str, method: str, route: str, request: Any, response: Any) -> str:
    name = handler_name(method, route)
    doc = standard_block(language, f"request body ({describe_schema(request)})", describe_schema(response),
                         f"Handle {method} {route}.")
    if language == "python":
        body = (doc + f'@bp.route("{route}", methods=["{method}"])\ndef {name}():\n'
                "    payload = request.get_json(silent=True) or {}\n"
                f'    return jsonify({{"error": "{method} {route} is not implemented yet", "received": payload}}), 501\n')
    else:
        body = (doc + f"router.{method.lower()}('{route}', (req, res) => {{\n"
                f"  res.status(501).json({{ error: '{method} {route} is not implemented yet' }});\n}});\n")
    return marked(language, f"endpoint {endpoint_id}", body)


# Input: language (str), endpoint_id (str), service (str), method (str), route (str), request (Any), response (Any)
# Output: str
# Processing: A marked client function that calls an endpoint.
# Changed by: Sourish
# Date: 2026-10-22
def client_code(language: str, endpoint_id: str, service: str, method: str, route: str, request: Any, response: Any) -> str:
    name = "call_" + re.sub(r"\W", "_", service) + "_" + handler_name(method, route)
    env = re.sub(r"\W", "_", service).upper() + "_URL"
    if language == "python":
        doc = standard_block("python", f"payload (dict, {describe_schema(request)})",
                             f"dict ({describe_schema(response)})", f"Call {method} {route} on the {service} service.")
        body = (doc + f"def {name}(payload: dict | None = None) -> dict:\n"
                "    import json\n    import os\n    import urllib.request\n\n"
                f'    base = os.environ.get("{env}", "http://127.0.0.1:5000")\n'
                "    data = None if payload is None else json.dumps(payload).encode()\n"
                f'    req = urllib.request.Request(base + "{route}", data=data, method="{method}",\n'
                '                                 headers={"content-type": "application/json"})\n'
                "    with urllib.request.urlopen(req) as response:\n"
                "        return json.loads(response.read() or b\"{}\")\n")
    else:
        camel = re.sub(r"_(\w)", lambda m: m.group(1).upper(), name)
        doc = standard_block(language, f"payload (object, {describe_schema(request)})",
                             f"Promise<object> ({describe_schema(response)})",
                             f"Call {method} {route} on the {service} service.")
        body = (doc + f"export async function {camel}(payload) {{\n"
                f"  const base = process.env.{env} || 'http://127.0.0.1:5000';\n"
                f"  const res = await fetch(base + '{route}', {{\n    method: '{method}',\n"
                "    headers: { 'content-type': 'application/json' },\n"
                "    body: payload === undefined ? undefined : JSON.stringify(payload),\n  });\n"
                "  return res.json();\n}\n")
    return marked(language, f"call {endpoint_id}", body)


# Input: schema (Any)
# Output: Any
# Processing: A sample request body built from a schema's fields.
# Changed by: Sourish
# Date: 2026-10-22
def sample_for(schema: Any) -> Any:
    if not isinstance(schema, dict):
        return {}
    samples = {"string": "example", "str": "example", "integer": 1, "int": 1, "number": 1.5,
               "float": 1.5, "boolean": True, "bool": True, "array": [], "list": [], "object": {}}
    return {field: samples.get(str(kind).lower(), "example") for field, kind in schema.items()}
