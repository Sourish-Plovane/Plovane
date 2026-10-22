"""Security and health: scans, alerts, broken connections, failing tests, live runs."""

from __future__ import annotations

import ast
import re
import shlex
import subprocess
import sys
from typing import Any

from whiteboard import codegen
from whiteboard.engine import Context
from whiteboard.model import Block
from whiteboard.registry import OpError, flag, integer, operation

SEVERITIES = ("low", "medium", "high")

SECRET_PATTERNS = (
    (re.compile(r"AKIA[0-9A-Z]{16}"), "AWS access key id"),
    (re.compile(r"-----BEGIN (RSA |EC |OPENSSH )?PRIVATE KEY-----"), "private key"),
    (re.compile(r"gh[pousr]_[A-Za-z0-9]{36,}"), "GitHub token"),
    (re.compile(r"sk-[A-Za-z0-9_-]{20,}"), "API secret key"),
    (re.compile(r"""(?i)\b(password|passwd|secret|api_key|apikey|token)\s*[:=]\s*['"][^'"]{6,}['"]"""),
     "hard-coded credential"),
)

JS_PATTERNS = (
    (re.compile(r"\beval\s*\("), "high", "eval runs arbitrary code"),
    (re.compile(r"\.innerHTML\s*="), "medium", "innerHTML assignment can inject script (XSS)"),
    (re.compile(r"child_process.*\bexec\s*\(|\bexecSync\s*\("), "high", "shell command execution"),
    (re.compile(r"rejectUnauthorized\s*:\s*false"), "high", "TLS certificate checks disabled"),
)


# Input: tree (ast.AST)
# Output: list[tuple[int, str, str]]
# Processing: Risky Python calls: (line, severity, message).
# Changed by: Sourish
# Date: 2026-10-22
def python_findings(tree: ast.AST) -> list[tuple[int, str, str]]:
    found = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        name = ast.unparse(node.func)
        keywords = {k.arg: k.value for k in node.keywords if k.arg}
        if name in ("eval", "exec"):
            found.append((node.lineno, "high", f"{name} runs arbitrary code"))
        elif name in ("pickle.loads", "pickle.load", "marshal.loads"):
            found.append((node.lineno, "high", f"{name} on untrusted data can run code"))
        elif name in ("yaml.load",) and "Loader" not in keywords:
            found.append((node.lineno, "medium", "yaml.load without a safe Loader"))
        elif name.startswith("subprocess.") and isinstance(keywords.get("shell"), ast.Constant) \
                and keywords["shell"].value is True:
            found.append((node.lineno, "high", f"{name} with shell=True allows command injection"))
        elif name in ("os.system", "os.popen"):
            found.append((node.lineno, "high", f"{name} runs a shell command"))
        elif isinstance(keywords.get("verify"), ast.Constant) and keywords["verify"].value is False:
            found.append((node.lineno, "high", "TLS certificate checks disabled (verify=False)"))
        elif name in ("hashlib.md5", "hashlib.sha1"):
            found.append((node.lineno, "low", f"{name} is weak; do not use it for passwords or signatures"))
    return found


# Input: ctx (Context), path (str)
# Output: list[dict[str, Any]]
# Processing: Security findings in one file.
# Changed by: Sourish
# Date: 2026-10-22
def scan_file(ctx: Context, path: str) -> list[dict[str, Any]]:
    text = ctx.fs.read(path)
    language = codegen.language_of(path)
    findings: list[tuple[int, str, str]] = []
    for number, line in enumerate(text.splitlines(), start=1):
        for pattern, label in SECRET_PATTERNS:
            if pattern.search(line):
                findings.append((number, "high", f"{label} in source"))
    if language == "python":
        try:
            findings += python_findings(ast.parse(text))
        except SyntaxError as exc:
            findings.append((exc.lineno or 1, "low", "file does not parse, so it was only scanned for secrets"))
    elif language in ("javascript", "typescript"):
        for number, line in enumerate(text.splitlines(), start=1):
            for pattern, severity, message in JS_PATTERNS:
                if pattern.search(line):
                    findings.append((number, severity, message))
    return [{"file": path, "line": n, "severity": s, "message": m} for n, s, m in findings]


# Input: ctx (Context), target (str)
# Output: dict[str, Any]
# Processing: Place a security block over an area and scan its code.
# Changed by: Sourish
# Date: 2026-10-22
@operation("health.scan", command="/scan", tool="plovane_scan", group="plovane_code")
def scan(ctx: Context, target: str) -> dict[str, Any]:
    """Places a security detection block over an area and scans it."""
    try:
        block = ctx.board.block(target)
        path = block.path or block.meta.get("file")
    except OpError:
        block, path = None, target.strip("/")
    if not path or not ctx.fs.exists(path):
        raise OpError("NOT_FOUND", f"nothing on disk to scan for {target!r}")
    ctx.require(path, "read")
    files = [path] if not ctx.fs.is_dir(path) else [f for f in ctx.fs.files_under(path) if codegen.language_of(f)]
    findings = [finding for file in files for finding in scan_file(ctx, file)]
    known = {(a["file"], a["line"], a["message"]) for a in ctx.board.alerts}
    for finding in findings:
        if (finding["file"], finding["line"], finding["message"]) not in known:
            ctx.board.alerts.append(finding | {"id": f"alert-{ctx.counter('alert')}", "scan": path})
    area = ctx.new_block("scan", f"scan: {path}", layer=block.layer if block else None,
                         parent=block.parent if block else None,
                         meta={"path": path, "findings": len(findings)})
    if block:
        area.x, area.y, area.w, area.h = block.x - 10, block.y - 10, block.w + 20, block.h + 20
    area.status = "alert" if findings else "clean"
    return {"scanned": path, "files": len(files), "findings": findings, "scan_block": area.id}


# Input: ctx (Context), severity (str | None)
# Output: dict[str, Any]
# Processing: Active alerts, optionally at or above a severity.
# Changed by: Sourish
# Date: 2026-10-22
@operation("health.alerts", command="/alerts", tool="plovane_alerts", group="plovane_code", mutates=False)
def alerts(ctx: Context, severity: str | None = None) -> dict[str, Any]:
    """Lists active alerts on the diagram."""
    if severity is not None and severity not in SEVERITIES:
        raise OpError("INVALID_ARGS", f"severity is one of {list(SEVERITIES)}")
    floor = SEVERITIES.index(severity) if severity else 0
    shown = [a for a in ctx.board.alerts if SEVERITIES.index(a["severity"]) >= floor]
    return {"alerts": shown, "count": len(shown)}


# Input: ctx (Context), run_tests (Any)
# Output: dict[str, Any]
# Processing: Mark blocks that are missing, broken or failing tests.
# Changed by: Sourish
# Date: 2026-10-22
@operation("health.check", command="/health", tool="plovane_health", group="plovane_code")
def health(ctx: Context, run_tests: Any = False) -> dict[str, Any]:
    """Marks blocks with failing tests or broken connections."""
    problems: list[dict[str, Any]] = []
    for block in ctx.board.blocks.values():
        block.meta.pop("health", None)
        if block.kind in ("file", "folder") and block.path and not ctx.fs.exists(block.path):
            problems.append({"block": block.id, "problem": f"{block.path} is missing on disk"})
    for arrow in ctx.board.arrows.values():
        if arrow.source not in ctx.board.blocks or arrow.target not in ctx.board.blocks:
            problems.append({"arrow": arrow.id, "problem": "an end of this arrow no longer exists"})
        elif arrow.type == "import" and arrow.meta.get("import"):
            code = arrow.meta["import"]
            text = ctx.fs.read(code["file"]) if ctx.fs.exists(code["file"]) else ""
            if code["line"] not in [l.strip() for l in text.splitlines()]:
                problems.append({"arrow": arrow.id, "block": arrow.source,
                                 "problem": f"{code['file']} no longer has `{code['line']}`"})
        elif arrow.type == "api" and arrow.endpoint and arrow.endpoint not in ctx.board.endpoints:
            problems.append({"arrow": arrow.id, "block": arrow.source,
                             "problem": f"endpoint {arrow.endpoint} no longer exists"})
    failing: list[str] = []
    if flag(run_tests):
        done = ctx.run([sys.executable, "-m", "pytest", "-q", "--no-header", "-rf", "-p", "no:cacheprovider"],
                       timeout=900)
        for line in done.stdout.splitlines():
            if line.startswith("FAILED "):
                test_file = line.split()[1].split("::")[0]
                failing.append(test_file)
        for test_file in sorted(set(failing)):
            stem = test_file.rsplit("/", 1)[-1].removeprefix("test_").removesuffix("_test.py").removesuffix(".py")
            for block in ctx.board.blocks.values():
                if block.path and (block.path == test_file or block.path.endswith(f"/{stem}.py")
                                   or block.path == f"{stem}.py"):
                    problems.append({"block": block.id, "problem": f"tests failing in {test_file}"})
    for problem in problems:
        if problem.get("block") in ctx.board.blocks:
            ctx.board.blocks[problem["block"]].meta["health"] = problem["problem"]
        if problem.get("arrow") in ctx.board.arrows:
            ctx.board.arrows[problem["arrow"]].status = "failing"
    return {"healthy": not problems, "problems": problems, "tests_run": flag(run_tests),
            "failing_test_files": sorted(set(failing))}


# Input: ctx (Context), action (str), target (str | None), cmd (str | None), lines (Any)
# Output: dict[str, Any]
# Processing: Start, stop or read a running process whose output shows on a block.
# Changed by: Sourish
# Date: 2026-10-22
@operation("health.monitor", command="/monitor", tool="plovane_monitor", group="plovane_code", mutates=False)
def monitor(ctx: Context, action: str, target: str | None = None, cmd: str | None = None,
            lines: Any = 20) -> dict[str, Any]:
    """Runs the project and shows live values on blocks and arrows.

    Output lines shaped ``key=value`` become the block's live values."""
    if action not in ("start", "stop", "status"):
        raise OpError("INVALID_ARGS", "action is start, stop or status")
    running = ctx.board.view.setdefault("monitors", {})
    if action == "start":
        if not target:
            raise OpError("INVALID_ARGS", "give the block to run, e.g. /monitor start checkout")
        block: Block = ctx.board.block(target)
        ctx.require(block)
        if block.id in running and block.id in ctx.ws.processes and ctx.ws.processes[block.id].poll() is None:
            raise OpError("CONFLICT", f"{block.id} is already running; /monitor stop {block.id}")
        if cmd:
            argv = shlex.split(cmd)
        elif block.kind == "file" and block.path and block.path.endswith(".py"):
            argv = [sys.executable, block.path]
        elif block.kind == "file" and block.path and block.path.endswith(".js"):
            argv = ["node", block.path]
        else:
            raise OpError("INVALID_ARGS", f"say how to run {block.id} with --cmd \"...\"")
        ctx.ws.store.monitor_dir.mkdir(parents=True, exist_ok=True)
        log_path = ctx.ws.store.monitor_dir / f"{block.id}.log"
        handle = log_path.open("w", encoding="utf-8")
        try:
            process = subprocess.Popen(argv, cwd=ctx.root, stdout=handle, stderr=subprocess.STDOUT)
        except FileNotFoundError:
            raise OpError("UNAVAILABLE", f"{argv[0]} is not installed") from None
        finally:
            handle.close()  # the child keeps its own copy of the file
        ctx.ws.processes[block.id] = process
        running[block.id] = {"pid": process.pid, "argv": argv, "log": str(log_path.relative_to(ctx.root))}
        return {"started": block.id, "pid": process.pid, "command": argv}
    block_ids = [ctx.board.block(target).id] if target else list(running)
    report = {}
    for block_id in block_ids:
        info = running.get(block_id)
        if info is None:
            raise OpError("NOT_FOUND", f"{block_id} is not being monitored")
        process = ctx.ws.processes.get(block_id)
        if action == "stop":
            if process and process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
            running.pop(block_id)
            ctx.ws.processes.pop(block_id, None)
            report[block_id] = {"stopped": True}
            continue
        log_path = ctx.root / info["log"]
        tail = log_path.read_text(encoding="utf-8", errors="replace").splitlines()[-integer(lines, "lines"):] \
            if log_path.exists() else []
        values = {}
        for line in tail:
            for key, value in re.findall(r"(\w+)=(\S+)", line):
                values[key] = value
        if block_id in ctx.board.blocks:
            ctx.board.blocks[block_id].meta["live"] = values
        state = "running" if process and process.poll() is None else (
            f"exited {process.returncode}" if process else "unknown (started by another session)")
        report[block_id] = {"state": state, "output": tail, "values": values}
    return {"action": action, "monitors": report}
