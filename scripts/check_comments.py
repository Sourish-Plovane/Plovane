"""Check Python functions carry the repository's five-field comment block.

    python scripts/check_comments.py ml_models mcp_server whiteboard

The standard (.claude/agents/README.md) puts, directly above every ``def``
(above its decorators, if any), the lines ``# Input:``, ``# Output:``,
``# Processing:``, ``# Changed by:`` and ``# Date: YYYY-MM-DD``, in that
order. Exits 1 and lists each function that lacks it.
"""

from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

FIELDS = ("# Input:", "# Output:", "# Processing:", "# Changed by:", "# Date:")
DATE = re.compile(r"^# Date: \d{4}-\d{2}-\d{2}$")


# Input: lines (list[str]), node (ast.FunctionDef | ast.AsyncFunctionDef)
# Output: str | None
# Processing: What is wrong with one function's comment block, or None.
# Changed by: Claude Code
# Date: 2026-10-22
def problem(lines: list[str], node: ast.FunctionDef | ast.AsyncFunctionDef) -> str | None:
    first = min([node.lineno, *(d.lineno for d in node.decorator_list)])
    block = [lines[i].strip() for i in range(first - 1 - len(FIELDS), first - 1) if i >= 0]
    if len(block) != len(FIELDS) or not all(b.startswith(f) for b, f in zip(block, FIELDS)):
        return "missing or out-of-order comment block"
    if any(b == f for b, f in zip(block, FIELDS)):
        return "a field is empty"
    if not DATE.match(block[-1]):
        return "Date is not YYYY-MM-DD"
    return None


# Input: path (Path)
# Output: list[str]
# Processing: Every violation in one file.
# Changed by: Claude Code
# Date: 2026-10-22
def check_file(path: Path) -> list[str]:
    source = path.read_text(encoding="utf-8")
    lines = source.splitlines()
    found = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            issue = problem(lines, node)
            if issue:
                found.append(f"{path}:{node.lineno} {node.name}: {issue}")
    return found


# Input: argv (list[str] | None)
# Output: int
# Processing: CLI entry point.
# Changed by: Claude Code
# Date: 2026-10-22
def main(argv: list[str] | None = None) -> int:
    targets = [Path(a) for a in (sys.argv[1:] if argv is None else argv)]
    files = [f for t in targets for f in ([t] if t.is_file() else sorted(t.rglob("*.py")))]
    found = [issue for f in files for issue in check_file(f)]
    print("\n".join(found) or f"OK  {len(files)} files")
    return 1 if found else 0


if __name__ == "__main__":
    raise SystemExit(main())
