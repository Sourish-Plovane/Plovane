"""Every whiteboard operation, registered once, plus argument helpers.

An operation is a function ``handler(ctx, **args) -> dict`` decorated with
:func:`operation`. The decorator records its slash command, the sheet's MCP
tool name, whether it changes anything, whether it must be confirmed, and
whether an AI agent may run it. The engine, the command parser, the HTTP API
and the MCP tools all read this one table.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any


class OpError(Exception):
    """A refusal the caller can act on. ``code`` is machine-readable."""

    # Input: code (str), message (str)
    # Output: None
    # Processing: Keep the error code next to the message.
    # Changed by: Sourish
    # Date: 2026-10-22
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class OpSpec:
    name: str
    """Engine name, e.g. ``block.add``."""
    handler: Callable[..., dict[str, Any]]
    command: str
    """Slash command a person types, e.g. ``/block add``."""
    tool: str
    """The command sheet's MCP tool name, e.g. ``plovane_block_add``."""
    group: str
    """Grouped MCP tool that carries it, e.g. ``plovane_block``."""
    summary: str
    mutates: bool = True
    confirm: bool | Callable[[dict[str, Any]], bool] = False
    agents: bool = True
    """False for operations only a person may run (granting access)."""


OPERATIONS: dict[str, OpSpec] = {}


# Input: name (str), command (str), tool (str), group (str), mutates (bool), confirm (bool | Callable), agents (bool)
# Output: Callable
# Processing: Decorator that registers a handler as an operation.
# Changed by: Sourish
# Date: 2026-10-22
def operation(
    name: str, *, command: str, tool: str, group: str, mutates: bool = True,
    confirm: bool | Callable[[dict[str, Any]], bool] = False, agents: bool = True,
) -> Callable:
    # Input: handler (Callable)
    # Output: Callable
    # Processing: Record the handler under its operation name.
    # Changed by: Sourish
    # Date: 2026-10-22
    def register(handler: Callable) -> Callable:
        summary = (handler.__doc__ or "").strip().splitlines()[0] if handler.__doc__ else name
        OPERATIONS[name] = OpSpec(name, handler, command, tool, group, summary, mutates, confirm, agents)
        return handler

    return register


# -- Argument helpers ------------------------------------------------------------
# Arguments arrive as strings from a typed command and as JSON from the API and
# MCP. These accept both, so a handler never has to care which.


# Input: value (Any), name (str)
# Output: float
# Processing: A number from a string or number argument.
# Changed by: Sourish
# Date: 2026-10-22
def number(value: Any, name: str) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError):
        raise OpError("INVALID_ARGS", f"{name} must be a number, got {value!r}") from None
    return int(result) if result.is_integer() else result


# Input: value (Any), name (str)
# Output: int
# Processing: An integer argument.
# Changed by: Sourish
# Date: 2026-10-22
def integer(value: Any, name: str) -> int:
    result = number(value, name)
    if not isinstance(result, int):
        raise OpError("INVALID_ARGS", f"{name} must be a whole number, got {value!r}")
    return result


# Input: value (Any), name (str)
# Output: tuple[float, float]
# Processing: An "x,y" pair from "200,120" or [200, 120].
# Changed by: Sourish
# Date: 2026-10-22
def pair(value: Any, name: str, sep: str = ",") -> tuple[float, float]:
    parts = value if isinstance(value, (list, tuple)) else str(value).replace(" ", "").split(sep)
    if len(parts) != 2:
        raise OpError("INVALID_ARGS", f"{name} must look like 200{sep}120, got {value!r}")
    return number(parts[0], name), number(parts[1], name)


# Input: value (Any), name (str)
# Output: tuple[float, float]
# Processing: A "min-max" range from "10-20" or [10, 20].
# Changed by: Sourish
# Date: 2026-10-22
def span(value: Any, name: str) -> tuple[float, float]:
    low, high = pair(value, name, sep="-")
    if low > high:
        raise OpError("INVALID_ARGS", f"{name}: {low} is above {high}")
    return low, high


# Input: value (Any)
# Output: list[str]
# Processing: A list from "a,b,c" or a JSON list.
# Changed by: Sourish
# Date: 2026-10-22
def items(value: Any) -> list[str]:
    if value is None or value == "":
        return []
    if isinstance(value, (list, tuple)):
        return [str(v).strip() for v in value if str(v).strip()]
    return [part.strip() for part in str(value).split(",") if part.strip()]


# Input: value (Any)
# Output: bool
# Processing: A boolean from true/false/on/off/yes/no or a bool.
# Changed by: Sourish
# Date: 2026-10-22
def flag(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).lower() in ("1", "true", "on", "yes", "y")
