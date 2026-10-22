"""Slash commands: what a person types, parsed into an operation.

    parse('/block add service "auth" --layer 1')
    -> ("block.add", {"kind": "service", "name": "auth", "layer": "1"})

Every command follows ``/noun verb target --options``. Positional words fill
the parameters listed in :data:`COMMANDS`; ``--flag value`` fills the
parameter of that name (dashes become underscores); a ``--flag`` with no
value is ``true``. Two shorthands from the command sheet are understood:
arrows (``checkout -> payments`` or ``checkout->payments``) and endpoints
(``payments:POST /charge``).

    python -m backend.whiteboard.commands --markdown    # the command reference table
"""

from __future__ import annotations

import inspect
import re
import shlex
import sys
from dataclasses import dataclass, field
from typing import Any

from backend.whiteboard.registry import OPERATIONS, OpError

ENDPOINT_HEAD = re.compile(r"^[^:\s]+:(GET|POST|PUT|PATCH|DELETE)$", re.I)


@dataclass(frozen=True)
class Command:
    phrase: str
    """The words after the slash, e.g. ``block add``."""
    op: str
    positionals: tuple[str, ...] = ()
    arrow: tuple[str, str] | None = None
    """Parameter names for the two ends of ``a -> b``."""
    rest: bool = False
    """The last positional takes every remaining word."""
    choices: dict[str, tuple[str, str]] = field(default_factory=dict)
    """Value-less flags that set a parameter, e.g. ``--top`` -> edge=top."""
    aliases: dict[str, str] = field(default_factory=dict)
    """Flag names that map to another parameter, e.g. ``--as`` -> name."""
    extras: str | None = None
    """Parameter that collects unknown flags, e.g. ``params``."""
    example: str = ""


# Input: names (str)
# Output: dict[str, tuple[str, str]]
# Processing: Value-less flags that each set the edge parameter.
# Changed by: Sourish
# Date: 2026-10-22
def _edges(*names: str) -> dict[str, tuple[str, str]]:
    return {n: ("edge", n) for n in names}


COMMANDS: tuple[Command, ...] = (
    # Layers and zoom
    Command("layer define", "layer.define", ("number",), example="/layer define 1 --zoom 10-20 --show services"),
    Command("layer edit", "layer.edit", ("number",), example="/layer edit 2 --zoom 20-40"),
    Command("layer list", "layer.list", example="/layer list"),
    Command("layer rename", "layer.rename", ("number", "name"), rest=True, example='/layer rename 1 "Services"'),
    Command("layer remove", "layer.remove", ("number",), example="/layer remove 4"),
    Command("zoom into", "view.zoom_into", ("block",), example="/zoom into payments-service"),
    Command("zoom out", "view.zoom_out", example="/zoom out"),
    Command("zoom", "view.zoom", ("level",), example="/zoom 15  or  /zoom layer 2"),
    # Project structure
    Command("folder create", "folder.create", ("path",), example="/folder create src/payments --right-of src/auth"),
    Command("file create", "file.create", ("path",),
            example="/file create src/payments/charge.py --template python-module"),
    Command("folder delete", "folder.delete", ("path",), example="/folder delete src/old-cache"),
    Command("project import", "project.import", ("path",), example="/project import"),
    # Blocks
    Command("block add", "block.add", ("kind", "name"), example='/block add service "auth" --layer 1'),
    Command("block move", "block.move", ("block",), example="/block move auth --into backend"),
    Command("block group", "block.group", ("blocks",), aliases={"as": "name"},
            example='/block group auth,users --as "identity"'),
    Command("block split", "block.split", ("block",), example="/block split payments --by file"),
    Command("block merge", "block.merge", ("blocks",), example="/block merge invoice,billing"),
    Command("block rename", "block.rename", ("block", "name"), rest=True, example='/block rename svc1 "checkout"'),
    Command("block delete", "block.delete", ("block",), example="/block delete old-cache"),
    Command("block info", "block.info", ("block",), example="/block info checkout"),
    # English blocks and compile
    Command("blocks view", "file.blocks", ("file",), example="/blocks view src/orders.py --detail 2"),
    Command("block edit", "block.edit_text", ("block", "text"), rest=True,
            example='/block edit step-3 "Charge the card, retry twice on failure"'),
    Command("block replace", "block.replace", ("block",), aliases={"with": "entry"}, extras="params",
            example="/block replace step-3 --with retry_request --times 3"),
    Command("compile apply", "compile.apply", ("compile",), example="/compile apply c_102"),
    Command("compile", "compile", ("direction", "target"), example="/compile blocks-to-code src/orders.py --preview"),
    # Placement and coordinates
    Command("place", "place", ("block",), example="/place auth --at 200,120  or  /place billing --right-of auth --gap 80"),
    Command("align", "align", ("blocks",), choices=_edges("top", "bottom", "left", "right", "center"),
            example="/align auth,users,billing --top"),
    Command("distribute", "distribute", ("blocks",),
            choices={"horizontal": ("direction", "horizontal"), "vertical": ("direction", "vertical")},
            example="/distribute auth,users,billing --horizontal --gap 60"),
    Command("grid", "grid", ("size",), example="/grid 20 --snap on"),
    Command("coords", "coords", ("block",), example="/coords auth"),
    Command("size", "size", ("block", "size"), example="/size auth 240x120  or  /size auth --fit"),
    # Style
    Command("style", "style", ("block",), example="/style auth --preset service"),
    Command("preset define", "preset.define", ("name",),
            example="/preset define service --fill #E8F0FE --border #1A73E8 --radius 12 --icon server"),
    Command("theme", "theme", ("name",), example="/theme dark"),
    # Arrows and connections
    Command("connect", "connect", arrow=("source", "target"),
            example='/connect checkout -> payments --type api --endpoint "POST /charge"'),
    Command("disconnect", "disconnect", arrow=("source", "target"), example="/disconnect checkout -> payments"),
    Command("connections", "connections", ("block",), example="/connections payments"),
    Command("trace", "trace", ("request",), rest=True, example='/trace "POST /checkout"'),
    Command("deps", "deps", ("block",), example="/deps payments --reverse"),
    Command("arrow style", "arrow.style", arrow=("source", "target"),
            example="/arrow style checkout->payments --width 2 --dash 4"),
    Command("arrow route", "arrow.route", arrow=("source", "target"),
            example="/arrow route checkout->payments --curve orthogonal"),
    # API endpoints
    Command("endpoint add", "endpoint.add", ("service", "method", "path"),
            example="/endpoint add payments POST /charge --request ChargeRequest --response Receipt"),
    Command("endpoint link", "endpoint.link", arrow=("source", "endpoint"),
            example="/endpoint link checkout -> payments:POST /charge"),
    Command("endpoint unlink", "endpoint.unlink", arrow=("source", "endpoint"),
            example="/endpoint unlink checkout -> payments:POST /charge"),
    Command("endpoint list", "endpoint.list", ("service",), example="/endpoint list payments"),
    Command("endpoint info", "endpoint.info", ("endpoint",), example="/endpoint info payments:POST /charge"),
    Command("endpoint edit", "endpoint.edit", ("endpoint",), example="/endpoint edit payments:POST /charge --path /charges"),
    Command("endpoint delete", "endpoint.delete", ("endpoint",), example="/endpoint delete payments:GET /legacy"),
    Command("endpoint test", "endpoint.test", ("endpoint",), example="/endpoint test payments:POST /charge --body sample"),
    Command("endpoint mock", "endpoint.mock", ("endpoint",),
            example="/endpoint mock payments:POST /charge --response sample"),
    Command("endpoint check", "endpoint.check", ("target",), example="/endpoint check all"),
    Command("endpoint import", "endpoint.import", ("spec",), example="/endpoint import openapi.yaml"),
    Command("endpoint export", "endpoint.export", ("service",), example="/endpoint export payments"),
    Command("api external add", "external.add", ("name",), example="/api external add stripe --spec stripe-openapi.yaml"),
    # Templates
    Command("template create", "template.create", ("name",), example='/template create "rest-api-service" --from layer 1'),
    Command("template apply", "template.apply", ("template",), extras="params",
            example="/template apply rest-api-service --layer 1 --at 0,0 --name orders"),
    Command("template list", "template.list", example="/template list --layer services"),
    Command("template preview", "template.preview", ("template",), example="/template preview rest-api-service"),
    Command("template edit", "template.edit", ("template",), example="/template edit rest-api-service --add-slot cache"),
    Command("template fill", "template.fill", ("instance",), example="/template fill orders --from-library"),
    # Batch
    Command("batch", "batch", example="/batch  (one operation per line)"),
    # Agents and permissions
    Command("agent add", "agent.add", ("name",), rest=True, example='/agent add "test-writer"'),
    Command("agent grant", "agent.grant", ("agent", "path"), example="/agent grant test-writer tests/ --mode read-write"),
    Command("agent revoke", "agent.revoke", ("agent", "path"), example="/agent revoke test-writer payments/"),
    Command("agent region", "agent.region", ("agent",),
            example="/agent region reviewer --area 100,200,400,500 --mode read"),
    Command("agent show", "agent.show", ("agent",), example="/agent show test-writer"),
    Command("agent log", "agent.log", ("agent",), example="/agent log test-writer --last 20"),
    # Library
    Command("rank", "library.rank", ("query",), rest=True, example='/rank "retry failed HTTP requests" --k 10'),
    Command("insert", "library.insert", ("entry",), example="/insert hf_0412 --into data/"),
    Command("palette", "library.palette", ("category",), example="/palette ui-components"),
    Command("suggest", "library.suggest", ("target",), example="/suggest checkout"),
    Command("save", "library.save", ("block",), example="/save normalize_batch --private"),
    Command("cite", "library.cite", ("block",), example="/cite checkout"),
    # Code
    Command("open", "code.open", ("target",), example="/open checkout.ts"),
    Command("diff", "code.diff", ("block",), example="/diff payments --since yesterday"),
    Command("explain", "code.explain", ("target",), example="/explain charge.ts"),
    # Security and health
    Command("scan", "health.scan", ("target",), example="/scan payments/"),
    Command("alerts", "health.alerts", example="/alerts --severity high"),
    Command("health", "health.check", example="/health"),
    Command("monitor", "health.monitor", ("action", "target"), example="/monitor start checkout"),
    # History and Git
    Command("undo", "history.undo", example="/undo"),
    Command("redo", "history.redo", example="/redo"),
    Command("snapshot", "history.snapshot", ("name",), rest=True, example='/snapshot "before refactor"'),
    Command("history", "history.show", ("block",), example="/history payments"),
    Command("git status", "git.status", example="/git status"),
    Command("git sync", "git.sync", choices={"pull": ("direction", "pull"), "push": ("direction", "push")},
            example="/git sync --push"),
    # View
    Command("find", "view.find", ("text",), rest=True, example='/find "stripe"'),
    Command("focus", "view.focus", ("block",), example="/focus payments"),
    Command("layout", "view.layout", ("direction",), example="/layout left-to-right"),
    Command("filter", "view.filter", example="/filter --arrows api"),
    Command("board", "board.read", example="/board  or  /board --layer 3  or  /board --block payments"),
)

BY_PHRASE = {c.phrase: c for c in COMMANDS}


# Input: tokens (list[str])
# Output: list[str]
# Processing: Join "payments:POST" "/charge" into one endpoint token, split "a->b".
# Changed by: Sourish
# Date: 2026-10-22
def normalise(tokens: list[str]) -> list[str]:
    out: list[str] = []
    index = 0
    while index < len(tokens):
        token = tokens[index]
        if ENDPOINT_HEAD.match(token) and index + 1 < len(tokens) and tokens[index + 1].startswith("/"):
            out.append(f"{token} {tokens[index + 1]}")
            index += 2
            continue
        if "->" in token and token != "->" and not token.startswith("--"):
            left, _, right = token.partition("->")
            out.extend(part for part in (left, "->", right) if part)
        else:
            out.append(token)
        index += 1
    return out


# Input: text (str)
# Output: tuple[str, dict[str, Any]]
# Processing: Parse one slash command into an operation name and arguments.
# Changed by: Sourish
# Date: 2026-10-22
def parse(text: str) -> tuple[str, dict[str, Any]]:
    stripped = text.strip()
    if not stripped:
        raise OpError("INVALID_COMMAND", "empty command")
    body = stripped[1:] if stripped.startswith("/") else stripped
    first_line, _, remainder = body.partition("\n")
    if first_line.strip().split(" ", 1)[0] == "batch":
        lines = [first_line.strip()[len("batch"):].strip(), *remainder.splitlines()]
        return "batch", {"commands": [line.strip() for line in lines if line.strip()]}
    try:
        tokens = normalise(shlex.split(body))
    except ValueError as exc:
        raise OpError("INVALID_COMMAND", f"could not read the command: {exc}") from None
    command = None
    for length in (3, 2, 1):
        phrase = " ".join(tokens[:length])
        if phrase in BY_PHRASE:
            command, tokens = BY_PHRASE[phrase], tokens[length:]
            break
    if command is None:
        guess = [c.phrase for c in COMMANDS if c.phrase.split()[0] == (tokens[0] if tokens else "")]
        raise OpError("UNKNOWN_COMMAND", f"no command /{' '.join(tokens[:2])}"
                                         + (f"; did you mean {['/' + g for g in guess]}" if guess else ""))
    args: dict[str, Any] = {}
    words: list[str] = []
    index = 0
    while index < len(tokens):
        token = tokens[index]
        if token.startswith("--") and len(token) > 2:
            name = token[2:].replace("-", "_")
            if name in command.choices:
                key, value = command.choices[name]
                args[key] = value
                index += 1
                continue
            has_value = index + 1 < len(tokens) and not tokens[index + 1].startswith("--")
            value: Any = tokens[index + 1] if has_value else True
            # "--from layer 1" (template create) reads as from_layer=1.
            if name == "from" and value == "layer" and index + 2 < len(tokens):
                args["from_layer"] = tokens[index + 2]
                index += 3
                continue
            if name == "from" and value == "selection":
                index += 2
                continue
            args[command.aliases.get(name, name)] = value
            index += 2 if has_value else 1
        else:
            words.append(token)
            index += 1
    if command.op == "view.zoom" and words[:1] == ["layer"]:
        words = words[1:]
        args["layer"] = words.pop(0) if words else None
    if command.arrow:
        if "->" not in words:
            raise OpError("INVALID_COMMAND", f"/{command.phrase} needs FROM -> TO, e.g. {command.example}")
        split = words.index("->")
        left, right = " ".join(words[:split]), " ".join(words[split + 1:])
        args[command.arrow[0]], args[command.arrow[1]] = left, right
        words = []
    names = list(command.positionals)
    for position, name in enumerate(names):
        if not words:
            break
        if command.rest and position == len(names) - 1:
            args[name] = " ".join(words)
            words = []
        else:
            args[name] = words.pop(0)
    if words:
        raise OpError("INVALID_COMMAND", f"unexpected {words}; usage: {command.example}")
    if command.extras:
        handler = OPERATIONS[command.op].handler if command.op in OPERATIONS else None
        known = set(inspect.signature(handler).parameters) if handler else set()
        extra = {k: v for k, v in args.items() if k not in known}
        if extra:
            args = {k: v for k, v in args.items() if k in known}
            args[command.extras] = extra
    return command.op, args


# Input: none
# Output: str
# Processing: The command reference as a Markdown table.
# Changed by: Sourish
# Date: 2026-10-22
def markdown() -> str:
    import backend.whiteboard.ops  # noqa: F401  (registers operations)

    rows = ["| Command | Operation | MCP tool | Confirms | What it does | Example |", "|---|---|---|---|---|---|"]
    for command in COMMANDS:
        spec = OPERATIONS[command.op]
        confirm = "yes" if spec.confirm is True else ("sometimes" if callable(spec.confirm) else "")
        rows.append(f"| `/{command.phrase}` | `{spec.name}` | `{spec.group}` | {confirm} | {spec.summary} "
                    f"| `{command.example.replace('|', '/')}` |")
    return "\n".join(rows)


if __name__ == "__main__":
    if sys.argv[1:] == ["--markdown"]:
        print(markdown())
    else:
        for line in sys.argv[1:] or sys.stdin.read().splitlines():
            print(parse(line))
