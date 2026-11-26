"""Premade overlay templates: rule overlays with blanks the whiteboard AI fills in for each project.

``/overlay template list`` shows them; ``/overlay from <template> --set '{...}'``
fills the blanks and saves the result as a normal rule overlay (``/overlay
rule``), which the AI or a person can then edit like any other. For example
the README names a "payments" area, so the AI runs::

    /overlay from area --id payments_area --set '{"path": "src/billing/*", "label": "Payments", "color": "purple"}'

A blank is written ``{name}`` inside the template. A string that is exactly
``{name}`` takes the value as it is (a number stays a number); a blank inside
longer text is filled in as text.
"""

from __future__ import annotations

import copy
from typing import Any

TEMPLATES: dict[str, dict[str, Any]] = {
    "failing_api_calls": {
        "description": "API arrows turn red when their last request failed and green while they work.",
        "params": {"fail_color": "red", "ok_color": "green"},
        "spec": {"label": "Failing API calls", "channel": "arrows", "priority": 90, "rules": [
            {"on": "arrow", "if": {"type": "api", "live.ok": False},
             "show": {"color": "{fail_color}", "message": "last call failed ({live.status})"}},
            {"on": "arrow", "if": {"type": "api", "live.ok": True},
             "show": {"color": "{ok_color}", "message": "working ({live.count} calls)"}}]},
    },
    "slow_api_calls": {
        "description": "API arrows turn orange when requests take longer than a threshold.",
        "params": {"ms": 800},
        "spec": {"label": "Slow API calls", "channel": "arrows", "priority": 70, "rules": [
            {"on": "arrow", "if": {"type": "api", "live.ms": {">": "{ms}"}},
             "show": {"color": "orange", "message": "slow: {live.ms} ms"}}]},
    },
    "unguarded_outside_calls": {
        "description": "Files that call an outside API with no error handling around it.",
        "params": {},
        "spec": {"label": "Outside calls without error handling", "channel": "border", "priority": 80, "rules": [
            {"on": "block", "if": {"kind": "file", "code.outside_calls": {">": 0}, "code.handles_errors": False},
             "show": {"color": "orange", "badge": "no error handling",
                      "message": "{code.outside_calls} outside call(s) and no try/except or .catch"}}]},
    },
    "outside_edits": {
        "description": "Files changed outside the whiteboard (Claude Code, an editor) in the last few minutes.",
        "params": {"minutes": 30},
        "spec": {"label": "Changed outside the whiteboard", "channel": "badge", "priority": 60, "rules": [
            {"on": "block", "if": {"kind": "file", "change.outside": True, "change.age": {"<": "{minutes*60}"}},
             "show": {"color": "blue", "badge": "just changed", "message": "changed outside the whiteboard {change.age}s ago"}}]},
    },
    "ai_edits": {
        "description": "Files an AI agent on the board changed recently, with who did it.",
        "params": {"minutes": 60},
        "spec": {"label": "Recent AI changes", "channel": "badge", "priority": 55, "rules": [
            {"on": "block", "if": {"kind": "file", "change.by": {"exists": True}, "change.age": {"<": "{minutes*60}"},
                                   "not": {"change.by": {"in": ["user", "outside"]}}},
             "show": {"color": "purple", "badge": "AI edit", "message": "changed by {change.by} {change.age}s ago"}}]},
    },
    "untested_changes": {
        "description": "Recently changed files that no test covers.",
        "params": {"minutes": 120},
        "spec": {"label": "Changed but untested", "channel": "border", "priority": 75, "rules": [
            {"on": "block", "if": {"kind": "file", "test.status": {"in": ["untested", "failing"]},
                                   "change.age": {"<": "{minutes*60}"}},
             "show": {"color": "orange", "badge": "untested change", "message": "changed {change.age}s ago, test: {test.status}"}}]},
    },
    "error_hot_spots": {
        "description": "Files the logs blame most, red above a threshold.",
        "params": {"threshold": 5},
        "spec": {"label": "Error hot spots", "channel": "border", "priority": 85, "rules": [
            {"on": "block", "if": {"kind": "file", "log.count": {">=": "{threshold}"}},
             "show": {"color": "red", "badge": "hot", "message": "{log.count} errors in the logs, worst at line {log.worst_line}"}},
            {"on": "block", "if": {"kind": "file", "log.count": {">": 0}},
             "show": {"color": "orange", "message": "{log.count} errors in the logs"}}]},
    },
    "never_ran": {
        "description": "Files whose code mostly never ran in the last trace.",
        "params": {"lines": 10},
        "spec": {"label": "Code that never ran", "channel": "badge", "priority": 45, "rules": [
            {"on": "block", "if": {"kind": "file", "trace.missed": {">=": "{lines}"}},
             "show": {"color": "gray", "badge": "never ran", "message": "{trace.missed} lines never ran in the last trace"}}]},
    },
    "todo_markers": {
        "description": "Files with TODO or FIXME notes.",
        "params": {},
        "spec": {"label": "TODOs", "channel": "badge", "priority": 30, "rules": [
            {"on": "block", "if": {"kind": "file", "code.todos": {">": 0}},
             "show": {"color": "gray", "badge": "TODO", "message": "{code.todos} TODO/FIXME"}}]},
    },
    "big_files": {
        "description": "Files longer than a line count, which are hard to read.",
        "params": {"lines": 400},
        "spec": {"label": "Big files", "channel": "badge", "priority": 25, "rules": [
            {"on": "block", "if": {"kind": "file", "code.lines": {">": "{lines}"}},
             "show": {"color": "gray", "badge": "big", "message": "{code.lines} lines"}}]},
    },
    "uses_library": {
        "description": "Files that import a given library (e.g. stripe, boto3, react).",
        "params": {"module": "", "color": "blue"},
        "spec": {"label": "Uses {module}", "channel": "badge", "priority": 40, "rules": [
            {"on": "block", "if": {"kind": "file", "code.imports": {"contains": "{module}"}},
             "show": {"color": "{color}", "badge": "{module}", "message": "imports {module}"}}]},
    },
    "area": {
        "description": "One part of the product (from the README or architecture docs), marked on every file in it; path is a folder or a pattern.",
        "params": {"path": "", "label": "", "color": "purple"},
        "spec": {"label": "{label}", "channel": "border", "priority": 20, "rules": [
            {"on": "block", "if": {"any": [{"path": {"matches": "{path}"}}, {"path": {"matches": "{path}/*"}}]},
             "show": {"color": "{color}", "badge": "{label}", "message": "part of {label}"}}]},
    },
}


# Input: none
# Output: list[dict[str, Any]]
# Processing: Every template with its description and blanks (and their defaults), for /overlay template list and the AI's prompt.
# Changed by: Claude Code
# Date: 2026-11-25
def listing() -> list[dict[str, Any]]:
    return [{"template": tid, "description": t["description"], "params": dict(t["params"])} for tid, t in TEMPLATES.items()]


# Input: value (Any), params (dict[str, Any])
# Output: Any
# Processing: Fill {blanks} in one template value; "{x*60}" multiplies a number blank (minutes to seconds).
# Changed by: Claude Code
# Date: 2026-11-25
def fill(value: Any, params: dict[str, Any]) -> Any:
    if isinstance(value, dict):
        return {k: fill(v, params) for k, v in value.items()}
    if isinstance(value, list):
        return [fill(v, params) for v in value]
    if not isinstance(value, str):
        return value
    for name, given in params.items():
        whole, scaled = "{" + name + "}", "{" + name + "*60}"
        if value == whole:
            return given
        if value == scaled:
            return float(given) * 60
        value = value.replace(whole, str(given))
    return value


# Input: template (str), given (dict[str, Any])
# Output: dict[str, Any]
# Processing: A template's overlay spec with its blanks filled from the given values and the defaults; ValueError for an unknown template, an unknown blank or a required blank left empty.
# Changed by: Claude Code
# Date: 2026-11-25
def instantiate(template: str, given: dict[str, Any]) -> dict[str, Any]:
    if template not in TEMPLATES:
        raise ValueError(f"no template {template!r}; templates: {sorted(TEMPLATES)}")
    found = TEMPLATES[template]
    unknown = sorted(set(given) - set(found["params"]))
    if unknown:
        raise ValueError(f"{template} has no blank {unknown[0]!r}; its blanks: {sorted(found['params'])}")
    params = {**found["params"], **given}
    for name, default in found["params"].items():
        if isinstance(default, (int, float)) and not isinstance(params[name], (int, float)):
            try:
                params[name] = float(params[name]) if "." in str(params[name]) else int(params[name])
            except ValueError:
                raise ValueError(f"{name} is a number") from None
    empty = [name for name, value in params.items() if value in ("", None)]
    if empty:
        raise ValueError(f"{template} needs {empty[0]!r}")
    return fill(copy.deepcopy(found["spec"]), params)
