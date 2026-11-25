"""Trace: run a test file and record which lines and branches actually ran.

``/trace run <file>`` runs the file under coverage.py in a subprocess, then
reads the SQLite data file it leaves behind. The result is a per-file map of
line numbers that ran and branches that didn't, saved to
``.plovane/trace.json`` so the whiteboard can colour them: green for lines
that ran, grey for lines that never did. Risky lines (from
``security.scan``) stay red on top of that.

This is the smallest working slice: one pytest file at a time. Later slices
can run many files, remember several traces, and colour arrows on the board.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

from backend.whiteboard.engine import Context
from backend.whiteboard.registry import OpError, operation
from backend.whiteboard.store import STATE_DIR

TRACE_FILE = "trace.json"
COVERAGE_DATA = ".plovane_coverage"
SKIP_PARTS = frozenset({".git", ".plovane", "node_modules", ".venv", "venv", "__pycache__", "dist", "build"})


# Input: root (Path), path (str)
# Output: Path
# Processing: Resolve a project-relative path and refuse to escape the project.
# Changed by: Claude Code
# Date: 2026-11-23
def project_path(root: Path, path: str) -> Path:
    candidate = (root / path).resolve()
    try:
        candidate.relative_to(root.resolve())
    except ValueError:
        raise OpError("INVALID_ARGS", f"{path!r} is outside the project") from None
    return candidate


# Input: root (Path)
# Output: list[str]
# Processing: The omit patterns that keep state and build folders out of the coverage.
# Changed by: Claude Code
# Date: 2026-11-23
def omit_patterns(root: Path) -> list[str]:
    return [f"{part}/*" for part in SKIP_PARTS] + ["*/__pycache__/*", "*.pyc"]


# Input: ctx (Context), target (str), timeout (int)
# Output: dict[str, Any]
# Processing: Run a pytest file under coverage.py; return the ran and missed lines per project file and save the result.
# Changed by: Claude Code
# Date: 2026-11-23
@operation("trace.run", command="/trace run", tool="plovane_trace_run", group="plovane_code")
def run(ctx: Context, target: str, timeout: Any = 120) -> dict[str, Any]:
    """Runs a test file with coverage and records what ran; the panel colours those lines green."""
    try:
        import coverage  # noqa: F401
    except ImportError:
        raise OpError("UNAVAILABLE", "coverage.py is not installed. Run: pip install 'plovane[trace]'") from None

    file = project_path(ctx.root, target)
    if not file.is_file():
        raise OpError("NOT_FOUND", f"{target!r} is not a file in the project")
    ctx.require(str(file.relative_to(ctx.root)), "read")

    data_file = ctx.root / STATE_DIR / COVERAGE_DATA
    data_file.parent.mkdir(exist_ok=True)
    if data_file.exists():
        data_file.unlink()

    # Run pytest on the one file, under coverage, from the project root. ``--source=.`` is the one
    # pattern that reliably matches every module the test imports; the per-folder omit keeps
    # ``.plovane`` and virtualenvs out of the result.
    env = {**os.environ, "COVERAGE_FILE": str(data_file)}
    omits = ",".join(omit_patterns(ctx.root))
    cmd = [sys.executable, "-m", "coverage", "run", "--branch", "--source=.", f"--omit={omits}",
           "-m", "pytest", "-q", "--no-header", "-rf", "-p", "no:cacheprovider",
           str(file)]
    # ctx.run doesn't take env, so run it directly here. Coverage needs the env variable to find its data file.
    import subprocess
    try:
        result = subprocess.run(cmd, cwd=ctx.root, env=env, capture_output=True, text=True, timeout=int(timeout))
    except FileNotFoundError:
        raise OpError("UNAVAILABLE", "python is not available") from None
    except subprocess.TimeoutExpired:
        raise OpError("TIMEOUT", f"the trace took longer than {timeout}s; raise --timeout") from None

    if not data_file.exists():
        # pytest failed to even start, or coverage had nothing to measure.
        message = (result.stderr or result.stdout or "").strip().splitlines()[-1:] or ["no trace produced"]
        raise OpError("INVALID_ARGS", f"trace failed: {message[0]}")

    import coverage
    cov = coverage.Coverage(data_file=str(data_file))
    cov.load()
    data = cov.get_data()

    files: dict[str, dict[str, Any]] = {}
    counts = {"ran": 0, "missed": 0, "branches": 0, "missed_branches": 0}
    for measured in sorted(data.measured_files()):
        try:
            rel = str(Path(measured).resolve().relative_to(ctx.root.resolve()))
        except ValueError:
            continue
        if any(part in SKIP_PARTS for part in Path(rel).parts):
            continue
        lines = sorted(data.lines(measured) or [])
        analysis = cov.analysis2(measured)
        executable = sorted(analysis[1])
        excluded = set(analysis[2])
        missed = [n for n in executable if n not in lines and n not in excluded]
        arcs = sorted(data.arcs(measured) or [], key=lambda a: (a[0], a[1]))
        branch_missed: list[list[int]] = []
        try:
            missing_arcs = cov.analysis2(measured)
            # analysis2 returns (statements, excluded, missing, missing_formatted) and in branch mode the data object exposes missing arcs.
            missing_arcs = data.missing_arcs(measured) if hasattr(data, "missing_arcs") else []
            branch_missed = [[a, b] for a, b in sorted(missing_arcs)]
        except Exception:  # noqa: BLE001
            branch_missed = []
        files[rel] = {
            "ran": lines,
            "missed": missed,
            "arcs": [[a, b] for a, b in arcs],
            "branches_missed": branch_missed,
        }
        counts["ran"] += len(lines)
        counts["missed"] += len(missed)
        counts["branches"] += len(arcs)
        counts["missed_branches"] += len(branch_missed)

    record = {
        "format": "plovane-trace", "version": 1,
        "target": str(file.relative_to(ctx.root)),
        "exit_code": result.returncode,
        "pytest_summary": pytest_summary(result.stdout),
        "counts": counts,
        "files": files,
    }

    # The trace is a cached artefact, not a project file: write it directly under .plovane/, which
    # the FileJournal refuses to touch. The path has already been created above.
    trace_path = ctx.root / STATE_DIR / TRACE_FILE
    trace_path.write_text(json.dumps(record, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    data_file.unlink(missing_ok=True)

    note = (f"{counts['ran']} lines ran, {counts['missed']} not reached"
            f"; {len(files)} files traced. Saved to .plovane/{TRACE_FILE}.")
    ctx.notes.append(note)
    return {
        "target": record["target"],
        "exit_code": record["exit_code"],
        "pytest_summary": record["pytest_summary"],
        "counts": counts,
        "files": {path: {"ran": len(info["ran"]), "missed": len(info["missed"])} for path, info in files.items()},
    }


# Input: output (str)
# Output: str
# Processing: The final short line pytest prints ("3 passed, 1 failed in 0.5s"), when it's there.
# Changed by: Claude Code
# Date: 2026-11-23
def pytest_summary(output: str) -> str:
    for line in reversed((output or "").splitlines()):
        line = line.strip()
        if line.startswith("=") and ("passed" in line or "failed" in line or "error" in line):
            return line.strip("= ").strip()
    return ""


# Input: ctx (Context)
# Output: dict[str, Any]
# Processing: Return the saved trace (or an empty one) so the panel can colour lines without a new run.
# Changed by: Claude Code
# Date: 2026-11-23
@operation("trace.read", command="/trace show", tool="plovane_trace_show", group="plovane_code", mutates=False)
def read(ctx: Context) -> dict[str, Any]:
    """Returns the last recorded trace, used by the whiteboard panel."""
    trace_path = ctx.root / STATE_DIR / TRACE_FILE
    if not trace_path.exists():
        return {"files": {}, "counts": {}, "target": None}
    try:
        record = json.loads(trace_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        raise OpError("INVALID_ARGS", f".plovane/{TRACE_FILE} is not readable: {exc}") from None
    return record
