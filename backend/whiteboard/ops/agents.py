"""Agents and permissions: which paths and canvas regions each AI agent may touch.

Agents use the same commands as people, but every operation an agent runs is
checked against its grants (see ``Context.require``). Only a person can add
an agent or change its access, and changing access always asks first.
"""

from __future__ import annotations

from typing import Any

from backend.whiteboard.engine import MODES, Context
from backend.whiteboard.model import Agent
from backend.whiteboard.ops.placement import apply_position
from backend.whiteboard.registry import OpError, integer, number, operation


# Input: ctx (Context), agent (str)
# Output: Agent
# Processing: An agent by name, or NOT_FOUND.
# Changed by: Sourish
# Date: 2026-10-22
def get(ctx: Context, agent: str) -> Agent:
    if agent not in ctx.board.agents:
        raise OpError("NOT_FOUND", f"no agent {agent!r}; /agent add creates one")
    return ctx.board.agents[agent]


# Input: mode (str)
# Output: str
# Processing: Validate an access mode.
# Changed by: Sourish
# Date: 2026-10-22
def check_mode(mode: str) -> str:
    if mode not in MODES:
        raise OpError("INVALID_ARGS", f"mode is one of {list(MODES)}")
    return mode


# Input: ctx (Context), name (str), at (Any)
# Output: dict[str, Any]
# Processing: Place an agent on the whiteboard, with no access yet.
# Changed by: Sourish
# Date: 2026-10-22
@operation("agent.add", command="/agent add", tool="plovane_agent_add", group="plovane_agent", agents=False)
def add(ctx: Context, name: str, at: Any = None) -> dict[str, Any]:
    """Places an agent on the whiteboard."""
    if name in ctx.board.agents or name == "user":
        raise OpError("CONFLICT", f"an agent is already called {name!r}")
    block = ctx.new_block("agent", name, id_hint=f"agent-{name}")
    apply_position(ctx, block, at=at)
    ctx.board.agents[name] = Agent(name=name, block=block.id)
    ctx.notes.append(f"{name} has no access yet; grant it with /agent grant {name} PATH --mode read")
    return {"agent": name, "block": block.id}


# Input: ctx (Context), agent (str), path (str), mode (str)
# Output: dict[str, Any]
# Processing: Give an agent access to a path.
# Changed by: Sourish
# Date: 2026-10-22
@operation("agent.grant", command="/agent grant", tool="plovane_agent_grant", group="plovane_agent",
           confirm=True, agents=False)
def grant(ctx: Context, agent: str, path: str, mode: str = "read") -> dict[str, Any]:
    """Gives an agent access to a path or region."""
    target = get(ctx, agent)
    prefix = path.strip().strip("/")
    if prefix not in ("", ".") and not ctx.fs.exists(prefix):
        raise OpError("NOT_FOUND", f"no file or folder {prefix!r}")
    target.grants = [g for g in target.grants if g["path"].strip("/") != prefix]
    target.grants.append({"path": prefix, "mode": check_mode(mode)})
    return {"agent": agent, "grants": target.grants}


# Input: ctx (Context), agent (str), path (str)
# Output: dict[str, Any]
# Processing: Remove an agent's access to a path, carving it out of wider grants.
# Changed by: Sourish
# Date: 2026-10-22
@operation("agent.revoke", command="/agent revoke", tool="plovane_agent_revoke", group="plovane_agent",
           confirm=True, agents=False)
def revoke(ctx: Context, agent: str, path: str) -> dict[str, Any]:
    """Removes an agent's access."""
    target = get(ctx, agent)
    prefix = path.strip().strip("/")
    before = len(target.grants)
    target.grants = [g for g in target.grants if g["path"].strip("/") != prefix]
    wider = [g for g in target.grants if g["mode"] != "none" and
             (g["path"] in ("", ".") or prefix.startswith(g["path"].strip("/") + "/"))]
    if wider:
        # A wider grant still covers this path, so record an explicit "none".
        target.grants.append({"path": prefix, "mode": "none"})
    elif len(target.grants) == before:
        raise OpError("NOT_FOUND", f"{agent} has no access to {prefix!r}")
    return {"agent": agent, "grants": target.grants}


# Input: ctx (Context), agent (str), area (Any), mode (str), layer (Any)
# Output: dict[str, Any]
# Processing: Grant access to the blocks inside a canvas rectangle.
# Changed by: Sourish
# Date: 2026-10-22
@operation("agent.region", command="/agent region", tool="plovane_agent_region", group="plovane_agent",
           confirm=True, agents=False)
def region(ctx: Context, agent: str, area: Any, mode: str = "read", layer: Any = None) -> dict[str, Any]:
    """Grants access by canvas coordinates."""
    target = get(ctx, agent)
    parts = area if isinstance(area, (list, tuple)) else str(area).replace(" ", "").split(",")
    if len(parts) != 4:
        raise OpError("INVALID_ARGS", "area looks like x1,y1,x2,y2")
    x1, y1, x2, y2 = (number(p, "area") for p in parts)
    if x2 <= x1 or y2 <= y1:
        raise OpError("INVALID_ARGS", "area needs x2 > x1 and y2 > y1")
    entry = {"area": [x1, y1, x2, y2], "mode": check_mode(mode),
             "layer": None if layer is None else integer(layer, "layer")}
    target.regions.append(entry)
    return {"agent": agent, "regions": target.regions}


# Input: ctx (Context), agent (str)
# Output: dict[str, Any]
# Processing: What an agent can see and change, highlighted.
# Changed by: Sourish
# Date: 2026-10-22
@operation("agent.show", command="/agent show", tool="plovane_agent_show", group="plovane_agent", mutates=False)
def show(ctx: Context, agent: str) -> dict[str, Any]:
    """Highlights what an agent can see and change."""
    target = get(ctx, agent)
    viewer = Context(ctx.ws, ctx.fs, agent, dry_run=True, confirmed=False)
    readable, writable = [], []
    for block in ctx.board.blocks.values():
        level = viewer.access(block)
        if level >= MODES["read-write"]:
            writable.append(block.id)
        elif level >= MODES["read"]:
            readable.append(block.id)
    ctx.board.view["highlight"] = sorted(readable + writable)
    return {"agent": agent, "grants": target.grants, "regions": target.regions,
            "can_change": sorted(writable), "can_read": sorted(readable)}


# Input: ctx (Context), agent (str), last (Any)
# Output: dict[str, Any]
# Processing: Everything an agent has done, newest last.
# Changed by: Sourish
# Date: 2026-10-22
@operation("agent.log", command="/agent log", tool="plovane_agent_log", group="plovane_agent", mutates=False)
def log(ctx: Context, agent: str, last: Any = 20) -> dict[str, Any]:
    """Shows everything an agent has done."""
    get(ctx, agent)
    return {"agent": agent, "entries": ctx.ws.store.read_log(actor=agent, last=integer(last, "last"))}
