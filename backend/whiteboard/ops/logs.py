"""Find the files a log blames for errors, so the whiteboard can light them up.

``/logs scan <file>`` reads a log file in the project (or a path the person
gives), pulls out Python and JavaScript stack traces and generic
``ERROR/EXCEPTION/Traceback`` lines, maps each to the file and line it points
at, and saves the counts to ``.plovane/log_errors.json``. The
``log_errors`` overlay (``plovane.overlays.log_errors@1``) reads that file
every time the board is drawn and badges each code file with how many errors
mentioned it, with a worst-offender preview.

No LLM, no network. If a log line names a file that exists in the project
but no block has been made for it yet, the hit is kept in the saved record so
a later ``/project import`` can still show it.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from backend.whiteboard.engine import Context
from backend.whiteboard.registry import OpError, operation
from backend.whiteboard.store import STATE_DIR

LOG_FILE = "log_errors.json"
MAX_BYTES = 8_000_000
MAX_HITS_PER_FILE = 10

# Python traceback: `  File "src/billing/charge.py", line 42, in charge`
PY_TRACE = re.compile(r'File "([^"]+?\.py)", line (\d+)')
# JavaScript / TypeScript stack frame: `at foo (src/app.ts:12:4)` or `at src/app.ts:12`
JS_TRACE = re.compile(r'\(?([A-Za-z0-9_./-]+?\.(?:js|jsx|ts|tsx|mjs|cjs)):(\d+)(?::\d+)?\)?')
# Short "ERROR … in file.py" / "Exception … at src/app.ts" lines a logger prints without a stack.
BARE_REFERENCE = re.compile(
    r'(?i)\b(?:error|exception|traceback|fatal|failed)\b[^\n]{0,200}?([A-Za-z0-9_./-]+?\.(?:py|js|jsx|ts|tsx|mjs|cjs))(?::(\d+))?'
)
EXTENSIONS = (".py", ".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs")


# Input: text (str), root (Path)
# Output: dict[str, dict[str, Any]]
# Processing: Group every file-and-line reference in log text by project-relative path, keeping sample lines.
# Changed by: Claude Code
# Date: 2026-11-24
def group_errors(text: str, root: Path) -> dict[str, dict[str, Any]]:
    root = root.resolve()
    hits: dict[str, dict[str, Any]] = {}
    for line_index, line in enumerate(text.splitlines(), start=1):
        for pattern in (PY_TRACE, JS_TRACE, BARE_REFERENCE):
            for match in pattern.finditer(line):
                raw_path = match.group(1)
                line_no = int(match.group(2)) if match.lastindex and match.lastindex >= 2 and match.group(2) else None
                rel = resolve_in_project(raw_path, root)
                if not rel:
                    continue
                entry = hits.setdefault(rel, {"count": 0, "lines": {}, "samples": []})
                entry["count"] += 1
                if line_no is not None:
                    entry["lines"][str(line_no)] = entry["lines"].get(str(line_no), 0) + 1
                if len(entry["samples"]) < 3:
                    short = line.strip()
                    if len(short) > 240:
                        short = short[:237] + "…"
                    entry["samples"].append({"log_line": line_index, "text": short})
    return hits


# Input: raw (str), root (Path)
# Output: str | None
# Processing: Map a path from a log (absolute, relative, or basename) to a file inside the project, when one matches.
# Changed by: Claude Code
# Date: 2026-11-24
def resolve_in_project(raw: str, root: Path) -> str | None:
    raw = raw.strip().lstrip("./")
    if not raw or raw.startswith("<") or raw.startswith("http"):
        return None
    candidate = (root / raw).resolve()
    if candidate.is_file() and _inside(candidate, root):
        return candidate.relative_to(root).as_posix()
    # The log may give an absolute path from a different checkout; match by tail.
    parts = Path(raw).parts
    while len(parts) > 1:
        tail = Path(*parts)
        for match in root.rglob(tail.as_posix()):
            if match.is_file() and _inside(match, root):
                return match.relative_to(root).as_posix()
        parts = parts[1:]
    # Last resort: the basename alone, if there is exactly one such file in the project.
    base = Path(raw).name
    if "/" not in raw and base.endswith(EXTENSIONS):
        matches = [m for m in root.rglob(base) if m.is_file() and _inside(m, root)]
        if len(matches) == 1:
            return matches[0].relative_to(root).as_posix()
    return None


# Input: path (Path), root (Path)
# Output: bool
# Processing: Whether a path is inside the project root.
# Changed by: Claude Code
# Date: 2026-11-24
def _inside(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


# Input: ctx (Context), target (str)
# Output: dict[str, Any]
# Processing: Read a log file, map each file-and-line mention to a project file, and save counts for the overlay.
# Changed by: Claude Code
# Date: 2026-11-24
@operation("logs.scan", command="/logs scan", tool="plovane_logs_scan", group="plovane_code")
def scan(ctx: Context, target: str) -> dict[str, Any]:
    """Finds the files a log file blames for errors and badges them with how many times."""
    rel = str(target).strip().lstrip("/")
    if not rel:
        raise OpError("INVALID_ARGS", "give a log file: /logs scan path/to/server.log")
    ctx.require(rel, "read")
    source = (ctx.root / rel).resolve()
    if not _inside(source, ctx.root.resolve()):
        raise OpError("INVALID_ARGS", f"{target!r} is outside the project")
    if not source.is_file():
        raise OpError("NOT_FOUND", f"{target!r} is not a file in the project")
    try:
        data = source.read_bytes()
    except OSError as exc:
        raise OpError("INVALID_ARGS", f"can't read {target}: {exc}") from None
    if len(data) > MAX_BYTES:
        data = data[-MAX_BYTES:]  # keep the tail of a huge log
    text = data.decode("utf-8", errors="replace")

    hits = group_errors(text, ctx.root)
    for entry in hits.values():
        # Keep only the top lines so the saved file stays small.
        entry["lines"] = dict(sorted(entry["lines"].items(), key=lambda kv: -kv[1])[:MAX_HITS_PER_FILE])
    record = {
        "format": "plovane-log-errors", "version": 1,
        "source": source.relative_to(ctx.root.resolve()).as_posix(),
        "total_errors": sum(e["count"] for e in hits.values()),
        "files": dict(sorted(hits.items(), key=lambda kv: -kv[1]["count"])),
    }
    out = ctx.root / STATE_DIR / LOG_FILE
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps(record, indent=1, sort_keys=True) + "\n", encoding="utf-8")

    ctx.notes.append(
        f"{record['total_errors']} error references in {rel}, across {len(hits)} file(s). "
        f"Switch on the 'Errors in logs' overlay to see them."
    )
    return {
        "source": record["source"],
        "total_errors": record["total_errors"],
        "files": {rel: {"count": e["count"], "lines": len(e["lines"])} for rel, e in record["files"].items()},
    }


# Input: ctx (Context)
# Output: dict[str, Any]
# Processing: Return the last log scan so a person can see it without opening the saved file.
# Changed by: Claude Code
# Date: 2026-11-24
@operation("logs.show", command="/logs show", tool="plovane_logs_show", group="plovane_code", mutates=False)
def show(ctx: Context) -> dict[str, Any]:
    """Returns the last recorded log scan (used by the 'Errors in logs' overlay)."""
    path = ctx.root / STATE_DIR / LOG_FILE
    if not path.exists():
        return {"files": {}, "total_errors": 0, "source": None}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise OpError("INVALID_ARGS", f".plovane/{LOG_FILE} is not readable: {exc}") from None
