"""The whiteboard as MCP tools: one tool per noun, not one per command.

An agent sees every tool's description on every turn, so 89 tools would cost
about 10-15k tokens before it did anything. Grouping by noun keeps it to 16:
each takes an ``action`` (e.g. ``add`` on ``plovane_block``) and its
``args``. ``plovane_command`` takes the slash command text instead. Both
run the same operations a person runs.

The agent identity comes from ``PLOVANE_AGENT`` and is checked against its
grants. Commands that change access (``/agent grant`` and friends) are never
available through MCP: only a person runs them, in the whiteboard.
"""

from __future__ import annotations

import os
from collections import defaultdict
from typing import Any

from whiteboard.engine import USER, Workspace
from whiteboard.registry import OPERATIONS

_workspace: Workspace | None = None


# Input: none
# Output: Workspace
# Processing: The project's workspace, opened on first use.
# Changed by: Claude Code
# Date: 2026-10-22
def workspace() -> Workspace:
    global _workspace
    if _workspace is None:
        _workspace = Workspace(os.environ.get("PLOVANE_PROJECT") or os.getcwd())
    return _workspace


# Input: ws (Workspace | None)
# Output: None
# Processing: Replace the shared workspace (tests).
# Changed by: Claude Code
# Date: 2026-10-22
def set_workspace(ws: Workspace | None) -> None:
    global _workspace
    _workspace = ws


# Input: group (str), op (str)
# Output: str
# Processing: The action name an operation has inside its grouped tool.
# Changed by: Claude Code
# Date: 2026-10-22
def action_name(group: str, op: str) -> str:
    noun = group.removeprefix("plovane_")
    return op[len(noun) + 1:] if op.startswith(noun + ".") else op.replace(".", "_")


# Input: none
# Output: dict[str, dict[str, str]]
# Processing: Every grouped tool with its actions mapped to operations.
# Changed by: Claude Code
# Date: 2026-10-22
def groups() -> dict[str, dict[str, str]]:
    import whiteboard.ops  # noqa: F401  (registers operations)

    found: dict[str, dict[str, str]] = defaultdict(dict)
    for spec in OPERATIONS.values():
        if spec.agents:
            found[spec.group][action_name(spec.group, spec.name)] = spec.name
    return dict(found)


# Input: group (str), actions (dict[str, str])
# Output: str
# Processing: A grouped tool's description listing its actions and arguments.
# Changed by: Claude Code
# Date: 2026-10-22
def describe(group: str, actions: dict[str, str]) -> str:
    import inspect

    lines = [f"Plovane whiteboard: {group.removeprefix('plovane_')} operations. "
             "Pass action and args; destructive actions answer needs_confirmation until confirm=true; "
             "dry_run=true previews. Actions:"]
    for action, op in sorted(actions.items()):
        spec = OPERATIONS[op]
        params = [p for p in inspect.signature(spec.handler).parameters if p != "ctx"]
        lines.append(f"- {action}({', '.join(params)}): {spec.summary}")
    return "\n".join(lines)


# Input: op (str), args (dict | None), confirm (bool), dry_run (bool)
# Output: dict[str, Any]
# Processing: Run one operation as the configured agent.
# Changed by: Claude Code
# Date: 2026-10-22
def run(op: str, args: dict | None, confirm: bool, dry_run: bool) -> dict[str, Any]:
    spec = OPERATIONS.get(op)
    if spec is not None and not spec.agents:
        return {"ok": False, "data": None, "error": {"code": "FORBIDDEN",
                "message": f"{spec.command} changes access; a person runs it in the whiteboard"}}
    return workspace().execute(op, args or {}, actor=os.environ.get("PLOVANE_AGENT") or USER,
                               confirm=confirm, dry_run=dry_run)


# Input: mcp (MCPServer)
# Output: list[str]
# Processing: Register the grouped whiteboard tools and plovane_command on a server.
# Changed by: Claude Code
# Date: 2026-10-22
def register(mcp: Any) -> list[str]:
    names = []
    for group, actions in sorted(groups().items()):
        # Input: actions (dict[str, str]), group (str)
        # Output: Callable
        # Processing: The tool function for one group, bound to its actions.
        # Changed by: Claude Code
        # Date: 2026-10-22
        def make(actions: dict[str, str], group: str):
            # Input: action (str), args (dict | None), confirm (bool), dry_run (bool)
            # Output: dict[str, Any]
            # Processing: Run one action of this group.
            # Changed by: Claude Code
            # Date: 2026-10-22
            def tool(action: str, args: dict | None = None, confirm: bool = False,
                     dry_run: bool = False) -> dict[str, Any]:
                if action not in actions:
                    return {"ok": False, "data": None, "error": {
                        "code": "UNKNOWN_ACTION", "message": f"{group} actions: {sorted(actions)}"}}
                return run(actions[action], args, confirm, dry_run)

            tool.__name__ = group
            return tool

        mcp.tool(name=group, description=describe(group, actions))(make(actions, group))
        names.append(group)

    # Input: command (str), confirm (bool), dry_run (bool)
    # Output: dict[str, Any]
    # Processing: Run a slash command as typed in the whiteboard.
    # Changed by: Claude Code
    # Date: 2026-10-22
    def plovane_command(command: str, confirm: bool = False, dry_run: bool = False) -> dict[str, Any]:
        """Run a whiteboard slash command exactly as a person types it, e.g.
        '/block add service "auth" --layer 1' or a /batch with one command per line.
        The full list is in whiteboard/README.md."""
        from whiteboard.commands import parse
        from whiteboard.registry import OpError

        try:
            op, args = parse(command)
        except OpError as exc:
            return {"ok": False, "data": None, "error": {"code": exc.code, "message": exc.message}}
        return run(op, args, confirm, dry_run)

    mcp.tool()(plovane_command)
    return [*names, "plovane_command"]
