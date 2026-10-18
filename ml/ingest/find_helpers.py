"""Find the helper functions inside one code file.

Runs the same extractor that builds the library over a single file and
reports each top-level function as either a **helper** -- it runs on its own
given only the imports listed, so it can be lifted out and reused -- or a
**near helper**, which would be one if it did not lean on the names listed in
``needs`` (a module constant, a sibling function, a class).

    python -m ml.ingest.find_helpers path/to/file.py
    python -m ml.ingest.find_helpers path/to/file.ts --helpers-only

Prints JSON. Python is analysed through its syntax tree. JavaScript,
TypeScript and C++ are read with the brace-language extractor, which is less
exact (issue 26). The library's rules apply: functions of 3 to 120 lines,
top level only, not ``_private``; class methods are not reported.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from ml.ingest.extract import ExtractedUnit, extract, language_for_path


# Input: unit (ExtractedUnit)
# Output: dict[str, Any]
# Processing: One function as a JSON-ready record.
# Changed by: Claude Code
# Date: 2026-10-18
def describe(unit: ExtractedUnit) -> dict[str, Any]:
    """One function as a JSON-ready record."""
    return {
        "name": unit.name,
        "signature": unit.signature,
        "docstring": unit.docstring,
        "start_line": unit.start_line,
        "end_line": unit.end_line,
        "imports": list(unit.required_imports),
        "needs": list(unit.unresolved_names),
        # With its imports prepended, ready to paste elsewhere.
        "code": unit.insertable_code,
    }


# Input: source (str), path (str)
# Output: dict[str, Any]
# Processing: Split a file's functions into helpers and near helpers.
# Changed by: Claude Code
# Date: 2026-10-18
def find_helpers(source: str, path: str) -> dict[str, Any]:
    """Split a file's functions into helpers and near helpers.

    A unit whose dependencies the extractor could not analyse is neither: it
    is listed under ``unchecked`` rather than guessed clean, the same rule the
    library applies (``ExtractedUnit.dependencies_analysed``).
    """
    language = language_for_path(path)
    report: dict[str, Any] = {
        "file": path,
        "language": language,
        "helpers": [],
        "near_helpers": [],
        "unchecked": [],
    }
    if language is None:
        report["error"] = "unsupported file type"
        return report
    # Typed overloads (``@overload`` stubs) come out as several units with one
    # name; the last definition is the one that runs, so keep only it.
    units = {u.name: u for u in extract(source, path) if not u.is_template}
    for unit in sorted(units.values(), key=lambda u: u.start_line):
        if not unit.dependencies_analysed:
            report["unchecked"].append(describe(unit))
        elif unit.self_contained:
            report["helpers"].append(describe(unit))
        else:
            report["near_helpers"].append(describe(unit))
    return report


# Input: argv (list[str] | None)
# Output: int
# Processing: CLI entry point.
# Changed by: Claude Code
# Date: 2026-10-18
def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("file", help="a .py, .js, .ts or C++ file")
    parser.add_argument(
        "--helpers-only", action="store_true", help="report only functions that stand alone"
    )
    args = parser.parse_args(argv)

    path = Path(args.file)
    report = find_helpers(path.read_text(errors="replace"), path.name)
    if args.helpers_only:
        report = {k: v for k, v in report.items() if k not in ("near_helpers", "unchecked")}
    json.dump(report, sys.stdout, indent=2)
    print()
    return 1 if "error" in report else 0


if __name__ == "__main__":
    raise SystemExit(main())
