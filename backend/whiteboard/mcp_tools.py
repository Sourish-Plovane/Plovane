"""The whiteboard as MCP tools: one tool per noun, not one per command.

An agent sees every tool's description on every turn, so 89 tools would cost
about 10-15k tokens before it did anything. Grouping by noun keeps it to 16:
each takes an ``action`` (e.g. ``add`` on ``plovane_block``) and its
``args``. ``plovane_command`` takes the slash command text instead. Both
run the same operations a person runs.

Where the calls go:

- **A running whiteboard server** (the normal case, and what ``plovane mcp``
  sets up): when ``PLOVANE_URL`` and ``PLOVANE_AGENT_TOKEN`` are set, every
  tool call is sent to that server with the agent's token. The server is the
  one owner of the board, holds the agent to its grants and shows each change
  live in the open whiteboard.
- **No server**: the tools edit the project in this process. The board is
  still saved to .plovane/board.json under the project lock, and a whiteboard
  started later, or one already watching that file, picks the change up.

Either way the caller is an agent, never the person: ``PLOVANE_AGENT`` names
it, and it defaults to ``claude``. An agent a person has not added and granted
access to can do nothing. Commands that change access (``/agent grant`` and
friends) are never available through MCP: only a person runs them.
"""

from __future__ import annotations

import os
from collections import defaultdict
from typing import Any

from backend.whiteboard.engine import Workspace
from backend.whiteboard.registry import OPERATIONS

_workspace: Workspace | None = None
DEFAULT_AGENT = "claude"


# Input: none
# Output: str
# Processing: The agent this MCP server acts as: PLOVANE_AGENT, or "claude".
# Changed by: Claude Code
# Date: 2026-11-09
def agent_name() -> str:
    return os.environ.get("PLOVANE_AGENT") or DEFAULT_AGENT


# Input: path (str), payload (dict[str, Any])
# Output: dict[str, Any] | None
# Processing: POST to the running whiteboard server as this agent, or None when no server is configured.
# Changed by: Claude Code
# Date: 2026-11-09
def remote(path: str, payload: dict[str, Any]) -> dict[str, Any] | None:
    url, token = os.environ.get("PLOVANE_URL"), os.environ.get("PLOVANE_AGENT_TOKEN")
    if not url or not token:
        return None
    from backend.whiteboard.llm_agent import http_json

    try:
        reply = http_json(f"{url.rstrip('/')}/api/v1/whiteboard/{path}",
                          {"authorization": f"Bearer {token}"}, payload, timeout=600)
    except OSError as exc:
        return {"ok": False, "data": None, "error": {"code": "SERVER_UNREACHABLE", "message":
                f"the Plovane whiteboard at {url} is not answering ({exc}); run 'plovane open'"}}
    reply.pop("_http_status", None)
    return reply


# Input: none
# Output: Workspace
# Processing: The project's workspace, opened on first use.
# Changed by: Sourish
# Date: 2026-10-22
def workspace() -> Workspace:
    global _workspace
    if _workspace is None:
        _workspace = Workspace(os.environ.get("PLOVANE_PROJECT") or os.getcwd())
    return _workspace


# Input: ws (Workspace | None)
# Output: None
# Processing: Replace the shared workspace (tests).
# Changed by: Sourish
# Date: 2026-10-22
def set_workspace(ws: Workspace | None) -> None:
    global _workspace
    _workspace = ws


# Input: group (str), op (str)
# Output: str
# Processing: The action name an operation has inside its grouped tool.
# Changed by: Sourish
# Date: 2026-10-22
def action_name(group: str, op: str) -> str:
    noun = group.removeprefix("plovane_")
    return op[len(noun) + 1:] if op.startswith(noun + ".") else op.replace(".", "_")


# Input: none
# Output: dict[str, dict[str, str]]
# Processing: Every grouped tool with its actions mapped to operations.
# Changed by: Sourish
# Date: 2026-10-22
def groups() -> dict[str, dict[str, str]]:
    import backend.whiteboard.ops  # noqa: F401  (registers operations)

    found: dict[str, dict[str, str]] = defaultdict(dict)
    for spec in OPERATIONS.values():
        if spec.agents:
            found[spec.group][action_name(spec.group, spec.name)] = spec.name
    return dict(found)


# Input: group (str), actions (dict[str, str])
# Output: str
# Processing: A grouped tool's description listing its actions and arguments.
# Changed by: Sourish
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


# Input: op (str), args (dict | None), confirm (bool), dry_run (bool), command (str | None)
# Output: dict[str, Any]
# Processing: Run one operation as the configured agent: on the running server when there is one, else here.
# Changed by: Claude Code
# Date: 2026-11-09
def run(op: str, args: dict | None, confirm: bool, dry_run: bool, command: str | None = None) -> dict[str, Any]:
    spec = OPERATIONS.get(op)
    if spec is not None and not spec.agents:
        return {"ok": False, "data": None, "error": {"code": "FORBIDDEN",
                "message": f"{spec.command} changes access; a person runs it in the whiteboard"}}
    if command is not None:
        sent = remote("command", {"command": command, "confirm": confirm, "dry_run": dry_run})
    else:
        sent = remote("op", {"op": op, "args": args or {}, "confirm": confirm, "dry_run": dry_run})
    if sent is not None:
        return sent
    ws = workspace()
    ws.current_command = command
    try:
        return ws.execute(op, args or {}, actor=agent_name(), confirm=confirm, dry_run=dry_run)
    finally:
        ws.current_command = None


# Input: mcp (MCPServer)
# Output: list[str]
# Processing: Register the grouped whiteboard tools and plovane_command on a server.
# Changed by: Sourish
# Date: 2026-10-22
def register(mcp: Any) -> list[str]:
    names = []
    for group, actions in sorted(groups().items()):
        # Input: actions (dict[str, str]), group (str)
        # Output: Callable
        # Processing: The tool function for one group, bound to its actions.
        # Changed by: Sourish
        # Date: 2026-10-22
        def make(actions: dict[str, str], group: str):
            # Input: action (str), args (dict | None), confirm (bool), dry_run (bool)
            # Output: dict[str, Any]
            # Processing: Run one action of this group.
            # Changed by: Sourish
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
    # Changed by: Sourish
    # Date: 2026-10-22
    def plovane_command(command: str, confirm: bool = False, dry_run: bool = False) -> dict[str, Any]:
        """Run a whiteboard slash command exactly as a person types it, e.g.
        '/block add service "auth" --layer 1' or a /batch with one command per line.
        The full list is in whiteboard/README.md."""
        from backend.whiteboard.commands import parse
        from backend.whiteboard.registry import OpError

        try:
            op, args = parse(command)
        except OpError as exc:
            return {"ok": False, "data": None, "error": {"code": exc.code, "message": exc.message}}
        return run(op, args, confirm, dry_run, command=command)

    mcp.tool()(plovane_command)
    return [*names, "plovane_command"]
