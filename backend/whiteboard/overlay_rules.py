"""The overlay rule engine: custom overlays as if/else rules, checked on every draw with no AI.

The whiteboard AI (or a person) writes an overlay once as data:

    {"label": "Failing API calls", "channel": "arrows", "rules": [
        {"on": "arrow", "if": {"type": "api", "live.ok": false},
         "show": {"color": "red", "message": "last call failed ({live.status})"}},
        {"on": "arrow", "if": {"type": "api", "live.ok": true}, "show": {"color": "green"}}]}

For every block or arrow, the rules for its kind are tried in order and the
first whose ``if`` holds decides what is shown: an if / else-if chain. A rule
with ``"if": {}`` always holds, so it works as the final else.

A condition maps fact names to a value (equal) or to ``{operator: value}``
with ``= != > >= < <= in contains exists matches``. Several keys must all
hold. ``{"all": [...]}``, ``{"any": [...]}`` and ``{"not": {...}}`` combine
conditions. A fact a block or arrow does not have makes a comparison false.

Facts come from the board and from what Plovane already measures (see
:func:`block_facts` and :func:`arrow_facts`): live API status, log errors,
trace coverage, which agent is working on a file, and the count every premade
analyzer finds (``premade.code_vulnerabilities`` and so on). Rules are data,
never code, so a wrong rule can only draw the wrong thing.
"""

from __future__ import annotations

import fnmatch
import json
import os
import re
from typing import Any

from backend.whiteboard.store import STATE_DIR

TARGETS = ("block", "arrow")
OPERATORS = ("=", "!=", ">", ">=", "<", "<=", "in", "contains", "exists", "matches")
COLORS = ("red", "orange", "blue", "green", "purple", "gray")
MAX_RULES = 20
MAX_DEPTH = 6
TEMPLATE = re.compile(r"\{([a-z0-9_.]+)\}")
BLOCK_FACTS = ("id", "kind", "name", "path", "folder", "layer", "status", "parent", "language", "summary",
               "log.count", "log.worst_line", "trace.ran", "trace.missed", "live.agent", "premade.<overlay>")
ARROW_FACTS = ("id", "type", "status", "endpoint", "label", "source.name", "source.kind", "source.path",
               "target.name", "target.kind", "target.path", "live.ok", "live.status", "live.fails", "live.count")


class RuleError(ValueError):
    """A rule that does not follow the format; the message says what to fix."""


# Input: cond (Any), depth (int)
# Output: None
# Processing: Check a condition's shape, raising RuleError with the first problem.
# Changed by: Claude Code
# Date: 2026-11-25
def check_condition(cond: Any, depth: int = 0) -> None:
    if depth > MAX_DEPTH:
        raise RuleError("conditions nest too deeply")
    if not isinstance(cond, dict):
        raise RuleError("a condition is an object, e.g. {\"type\": \"api\", \"live.fails\": {\">\": 0}}")
    for key, value in cond.items():
        if key in ("all", "any"):
            if not isinstance(value, list) or not value:
                raise RuleError(f"{key!r} takes a non-empty list of conditions")
            for part in value:
                check_condition(part, depth + 1)
        elif key == "not":
            check_condition(value, depth + 1)
        elif not re.fullmatch(r"[a-z0-9_]+(\.[a-z0-9_]+)*", key):
            raise RuleError(f"{key!r} is not a fact name (lowercase words joined by dots)")
        elif isinstance(value, dict):
            if len(value) != 1 or next(iter(value)) not in OPERATORS:
                raise RuleError(f"{key!r}: use one operator of {list(OPERATORS)}, e.g. {{\">\": 0}}")
            op, operand = next(iter(value.items()))
            if op == "in" and not isinstance(operand, list):
                raise RuleError(f"{key!r}: 'in' takes a list")
            if op == "matches" and not isinstance(operand, str):
                raise RuleError(f"{key!r}: 'matches' takes a pattern like 'src/billing/*'")


# Input: rules (Any)
# Output: list[dict[str, Any]]
# Processing: Check a rule list and return it cleaned (known keys only, messages trimmed); RuleError on the first problem.
# Changed by: Claude Code
# Date: 2026-11-25
def validate(rules: Any) -> list[dict[str, Any]]:
    if not isinstance(rules, list) or not rules:
        raise RuleError("rules is a non-empty list")
    if len(rules) > MAX_RULES:
        raise RuleError(f"at most {MAX_RULES} rules in one overlay")
    clean: list[dict[str, Any]] = []
    for number, rule in enumerate(rules, start=1):
        if not isinstance(rule, dict):
            raise RuleError(f"rule {number} is not an object")
        target = rule.get("on")
        if target not in TARGETS:
            raise RuleError(f"rule {number}: 'on' is one of {list(TARGETS)}")
        cond = rule.get("if", {})
        try:
            check_condition(cond)
        except RuleError as exc:
            raise RuleError(f"rule {number}: {exc}") from None
        show = rule.get("show")
        if not isinstance(show, dict) or not show:
            raise RuleError(f"rule {number}: 'show' says what to draw, e.g. {{\"color\": \"red\", \"message\": \"...\"}}")
        if show.get("color") is not None and show["color"] not in COLORS:
            raise RuleError(f"rule {number}: color is one of {list(COLORS)}")
        kept = {k: str(show[k])[:120] for k in ("color", "message", "badge") if show.get(k) is not None}
        if not kept:
            raise RuleError(f"rule {number}: 'show' needs a color, message or badge")
        clean.append({"on": target, "if": cond, "show": kept})
    return clean


# Input: value (Any), op (str), operand (Any)
# Output: bool
# Processing: Apply one operator; comparisons against a missing fact or of mismatched types are false.
# Changed by: Claude Code
# Date: 2026-11-25
def compare(value: Any, op: str, operand: Any) -> bool:
    if op == "exists":
        return (value is not None) == bool(operand)
    if value is None:
        return op == "!=" and operand is not None
    try:
        if op == "=":
            return value == operand
        if op == "!=":
            return value != operand
        if op == ">":
            return value > operand
        if op == ">=":
            return value >= operand
        if op == "<":
            return value < operand
        if op == "<=":
            return value <= operand
        if op == "in":
            return value in operand
        if op == "contains":
            return str(operand).lower() in str(value).lower()
        if op == "matches":
            return fnmatch.fnmatch(str(value), str(operand))
    except TypeError:
        return False
    return False


# Input: cond (dict[str, Any]), facts (dict[str, Any])
# Output: bool
# Processing: Whether a condition holds for one block's or arrow's facts.
# Changed by: Claude Code
# Date: 2026-11-25
def holds(cond: dict[str, Any], facts: dict[str, Any]) -> bool:
    for key, value in cond.items():
        if key == "all":
            ok = all(holds(part, facts) for part in value)
        elif key == "any":
            ok = any(holds(part, facts) for part in value)
        elif key == "not":
            ok = not holds(value, facts)
        elif isinstance(value, dict):
            op, operand = next(iter(value.items()))
            ok = compare(facts.get(key), op, operand)
        else:
            ok = compare(facts.get(key), "=", value)
        if not ok:
            return False
    return True


# Input: text (str), facts (dict[str, Any])
# Output: str
# Processing: Fill {fact} placeholders in a message; an unknown fact shows as "?".
# Changed by: Claude Code
# Date: 2026-11-25
def render(text: str, facts: dict[str, Any]) -> str:
    return TEMPLATE.sub(lambda m: "?" if facts.get(m.group(1)) is None else str(facts[m.group(1)]), text)


# Input: ws (Workspace), name (str)
# Output: dict[str, Any]
# Processing: A saved signal file in .plovane/ (log_errors.json, trace.json) as data, or empty.
# Changed by: Claude Code
# Date: 2026-11-25
def saved(ws: Any, name: str) -> dict[str, Any]:
    path = ws.root / STATE_DIR / name
    try:
        return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
    except (OSError, json.JSONDecodeError):
        return {}


class Signals:
    """What Plovane measured, read once per draw and looked up per block or arrow."""

    # Input: ws (Workspace), board (Board), rules (list[dict])
    # Output: None
    # Processing: Load live API state, log errors, trace coverage, agent activity and only the premade analyzers the rules name.
    # Changed by: Claude Code
    # Date: 2026-11-25
    def __init__(self, ws: Any, board: Any, rules: list[dict[str, Any]]) -> None:
        from backend.whiteboard.ops import live

        state = live.snapshot(ws)
        self.api = state.get("api") or {}
        self.working = {p: m.get("agent") for p, m in (state.get("files") or {}).items()}
        self.logs = saved(ws, "log_errors.json").get("files") or {}
        self.trace = saved(ws, "trace.json").get("files") or {}
        self.premade: dict[str, dict[str, int]] = {}
        names = {key.split(".", 1)[1] for key in self.fact_names(rules) if key.startswith("premade.")}
        if names:
            from backend.whiteboard.overlays import ANALYZERS

            for name in names & set(ANALYZERS):
                self.premade[name] = {bid: len(items) for bid, items in ANALYZERS[name](ws, board).items()}

    # Input: rules (list[dict])
    # Output: set[str]
    # Processing: Every fact name the rules mention, at any depth.
    # Changed by: Claude Code
    # Date: 2026-11-25
    @staticmethod
    def fact_names(rules: list[dict[str, Any]]) -> set[str]:
        found: set[str] = set()

        # Input: cond (Any)
        # Output: None
        # Processing: Collect fact names from one condition.
        # Changed by: Claude Code
        # Date: 2026-11-25
        def walk(cond: Any) -> None:
            if isinstance(cond, dict):
                for key, value in cond.items():
                    if key in ("all", "any"):
                        for part in value:
                            walk(part)
                    elif key == "not":
                        walk(value)
                    else:
                        found.add(key)

        for rule in rules:
            walk(rule.get("if", {}))
            for text in rule.get("show", {}).values():
                found.update(TEMPLATE.findall(str(text)))
        return found


# Input: block (Block), signals (Signals)
# Output: dict[str, Any]
# Processing: The facts a rule can test on one block.
# Changed by: Claude Code
# Date: 2026-11-25
def block_facts(block: Any, signals: Signals) -> dict[str, Any]:
    path = (block.path or block.meta.get("file") or "").strip("/") or None
    log = signals.logs.get(path or "", {})
    trace = signals.trace.get(path or "", {})
    lines = log.get("lines") or {}
    facts: dict[str, Any] = {
        "id": block.id, "kind": block.kind, "name": block.name, "path": path,
        "folder": os.path.dirname(path) if path else None, "layer": block.layer, "status": block.status,
        "parent": block.parent, "language": block.meta.get("language"), "summary": block.meta.get("summary"),
        "log.count": log.get("count") if log else None,
        "log.worst_line": int(max(lines, key=lines.get)) if lines else None,
        "trace.ran": len(trace.get("ran") or []) if trace else None,
        "trace.missed": len(trace.get("missed") or []) if trace else None,
        "live.agent": signals.working.get(path or ""),
    }
    for name, counts in signals.premade.items():
        facts[f"premade.{name}"] = counts.get(block.id, 0)
    return facts


# Input: arrow (Arrow), board (Board), signals (Signals)
# Output: dict[str, Any]
# Processing: The facts a rule can test on one arrow, including the live state of the API route it carries.
# Changed by: Claude Code
# Date: 2026-11-25
def arrow_facts(arrow: Any, board: Any, signals: Signals) -> dict[str, Any]:
    source, target = board.blocks.get(arrow.source), board.blocks.get(arrow.target)
    state = signals.api.get(arrow.endpoint or "") or {}
    facts: dict[str, Any] = {
        "id": arrow.id, "type": arrow.type, "status": arrow.status, "endpoint": arrow.endpoint,
        "label": arrow.meta.get("label"),
        "live.ok": state.get("ok"), "live.status": state.get("status"),
        "live.fails": state.get("fails"), "live.count": state.get("count"),
    }
    for side, block in (("source", source), ("target", target)):
        facts[f"{side}.name"] = block.name if block else None
        facts[f"{side}.kind"] = block.kind if block else None
        facts[f"{side}.path"] = block.path if block else None
    return facts


# Input: ws (Workspace), board (Board), spec (dict)
# Output: tuple[dict[str, list[dict]], dict[str, dict]]
# Processing: Run an overlay's rules over every block and arrow: per-block items (for roll-up) and per-arrow looks.
# Changed by: Claude Code
# Date: 2026-11-25
def evaluate(ws: Any, board: Any, spec: dict[str, Any]) -> tuple[dict[str, list[dict[str, Any]]], dict[str, dict[str, Any]]]:
    try:
        rules = validate(spec.get("rules"))
    except RuleError:
        return {}, {}
    signals = Signals(ws, board, rules)
    blocks: dict[str, list[dict[str, Any]]] = {}
    arrows: dict[str, dict[str, Any]] = {}
    block_rules = [r for r in rules if r["on"] == "block"]
    arrow_rules = [r for r in rules if r["on"] == "arrow"]
    if block_rules:
        for block in board.blocks.values():
            facts = block_facts(block, signals)
            rule = next((r for r in block_rules if holds(r["if"], facts)), None)
            if rule:
                show = rule["show"]
                blocks[block.id] = [{"message": render(show.get("message") or show.get("badge") or spec.get("label", ""), facts),
                                     **{k: show[k] for k in ("color", "badge") if k in show}}]
    if arrow_rules:
        for arrow in board.arrows.values():
            facts = arrow_facts(arrow, board, signals)
            rule = next((r for r in arrow_rules if holds(r["if"], facts)), None)
            if rule:
                show = rule["show"]
                arrows[arrow.id] = {"source": arrow.source, "target": arrow.target, "endpoint": arrow.endpoint,
                                    "color": show.get("color"),
                                    "message": render(show.get("message") or show.get("badge") or "", facts)}
    return blocks, arrows
