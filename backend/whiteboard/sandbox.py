"""Run a project's tests against proposed changes, without touching the project.

``/compile`` and ``/block replace`` show a diff and the result of the tests it
affects before anything is written. The project is copied to a temporary
folder, the proposed files are written there, and pytest runs in the copy.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

from backend.whiteboard import codegen

SKIP = shutil.ignore_patterns(".git", ".plovane", "node_modules", "__pycache__", ".venv", "venv",
                              ".pytest_cache", ".mypy_cache")


# Input: root (Path), files (list[str])
# Output: list[str]
# Processing: Python test files that cover the given source files.
# Changed by: Sourish
# Date: 2026-10-22
def affected_tests(root: Path, files: list[str]) -> list[str]:
    """A test covers a file when it is named after it (``test_orders.py`` for
    ``orders.py``) or imports it. A changed test file covers itself."""
    tests = []
    for path in root.rglob("*.py"):
        rel = path.relative_to(root).as_posix()
        if any(part in (".git", ".plovane", "node_modules", ".venv", "venv") for part in path.parts):
            continue
        name = path.name
        if name.startswith("test_") or name.endswith("_test.py"):
            tests.append(rel)
    found = []
    for file in files:
        if codegen.language_of(file) != "python":
            continue
        stem = Path(file).stem
        module = codegen.module_name(file)
        if Path(file).name.startswith("test_"):
            found.append(file)
            continue
        for test in tests:
            text = (root / test).read_text(encoding="utf-8", errors="replace")
            if Path(test).stem in (f"test_{stem}", f"{stem}_test") or f"import {module}" in text \
                    or f"from {module} import" in text:
                found.append(test)
    return sorted(set(found))


# Input: root (Path), proposed (dict[str, str | None]), timeout (int)
# Output: dict[str, Any]
# Processing: Run the affected tests in a copy of the project with the proposed files.
# Changed by: Sourish
# Date: 2026-10-22
def run_affected(root: Path, proposed: dict[str, str | None], timeout: int = 300) -> dict[str, Any]:
    """``proposed`` maps a path to its new text, or None to delete it.
    ``passed`` is None when no test covers the change."""
    tests = affected_tests(root, list(proposed))
    if not tests:
        return {"ran": False, "passed": None, "tests": [], "reason": "no tests cover these files"}
    with tempfile.TemporaryDirectory(prefix="plovane-sandbox-") as temp:
        copy = Path(temp) / "project"
        shutil.copytree(root, copy, ignore=SKIP)
        for rel, text in proposed.items():
            target = copy / rel
            if text is None:
                target.unlink(missing_ok=True)
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(text, encoding="utf-8")
        env = dict(os.environ, PYTHONPATH=str(copy))
        try:
            done = subprocess.run([sys.executable, "-m", "pytest", "-q", "--no-header", "-p", "no:cacheprovider",
                                   *tests], cwd=copy, capture_output=True, text=True, timeout=timeout, env=env)
        except subprocess.TimeoutExpired:
            return {"ran": True, "passed": False, "tests": tests, "output": f"timed out after {timeout}s"}
    output = (done.stdout + done.stderr).strip()
    return {"ran": True, "passed": done.returncode == 0, "tests": tests, "output": output[-3000:]}
