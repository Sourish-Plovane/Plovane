"""Tests for the overlay rule engine: AI-written if/else overlays checked on every draw with no AI."""

from __future__ import annotations

import json
import threading
from pathlib import Path

import pytest

from backend.whiteboard import board_ai, llm_agent
from backend.whiteboard.engine import BOARD_AI, USER
from backend.whiteboard.ops import live
from backend.whiteboard.overlay_rules import RuleError, holds, validate
from backend.whiteboard.overlays import overlay_state
from whiteboard_helpers import project

SHOP = {
    "api/__init__.py": "",
    "api/app.py": (
        "from flask import Flask\n\napp = Flask(__name__)\n\n\n"
        "@app.post('/checkout')\ndef checkout():\n    return {'ok': True}\n"),
    "web/cart.js": "export async function pay(order) {\n  return fetch('/checkout', {method: 'POST'})\n}\n",
}

FAILING = {"label": "Failing API calls", "rules": [
    {"on": "arrow", "if": {"type": "api", "live.ok": False},
     "show": {"color": "red", "message": "last call failed ({live.status})"}},
    {"on": "arrow", "if": {"type": "api"}, "show": {"color": "green", "message": "working"}}]}


# Input: tmp_path (Path)
# Output: Workspace
# Processing: A small shop project imported onto the board, with its /checkout route and the client call to it.
# Changed by: Claude Code
# Date: 2026-11-25
def shop(tmp_path: Path):
    ws = project(tmp_path, SHOP)
    assert ws.command("/project import", confirm=True)["ok"]
    assert any(a.type == "api" for a in ws.board.arrows.values()), "the route scan should draw the API arrow"
    return ws


# Input: none
# Output: None
# Processing: Badly formed rules are refused with a message that says what to fix.
# Changed by: Claude Code
# Date: 2026-11-25
def test_validate_explains_mistakes() -> None:
    for bad, words in (([], "non-empty"), ([{"on": "node", "show": {"color": "red"}}], "'on'"),
                       ([{"on": "arrow", "if": {"live.ok": {"~": 1}}, "show": {"color": "red"}}], "operator"),
                       ([{"on": "arrow", "show": {"color": "pink"}}], "color"),
                       ([{"on": "block", "if": {"Bad Key": 1}, "show": {"badge": "x"}}], "fact name")):
        with pytest.raises(RuleError, match=words):
            validate(bad)
    assert validate(FAILING["rules"])[0]["show"]["color"] == "red"


# Input: none
# Output: None
# Processing: Conditions: equality, operators, all/any/not, and missing facts counting as false.
# Changed by: Claude Code
# Date: 2026-11-25
def test_conditions() -> None:
    facts = {"type": "api", "live.fails": 3, "path": "src/billing/charge.py", "live.ok": None}
    assert holds({"type": "api", "live.fails": {">": 2}}, facts)
    assert holds({"any": [{"type": "import"}, {"path": {"matches": "src/billing/*"}}]}, facts)
    assert holds({"not": {"live.status": {"exists": True}}}, facts)
    assert not holds({"live.status": {">": 499}}, facts)
    assert holds({}, facts)


# Input: tmp_path (Path)
# Output: None
# Processing: The arrow for an API route turns red once a request to it fails, and green while it works.
# Changed by: Claude Code
# Date: 2026-11-25
def test_api_arrow_turns_red_when_its_request_fails(tmp_path: Path) -> None:
    ws = shop(tmp_path)
    made = ws.command(f"/overlay rule failing_calls --data '{json.dumps(FAILING)}'", actor=USER)
    assert made["ok"] and made["data"]["result"]["shown_to_person"] and made["data"]["result"]["channel"] == "arrows"
    api_arrow = next(a for a in ws.board.arrows.values() if a.type == "api")

    live.record(ws, {"method": "POST", "path": "/checkout", "status": 200})
    looks = overlay_state(ws, ws.board, USER)["arrows"]["failing_calls"]
    assert looks[api_arrow.id]["color"] == "green"

    live.record(ws, {"method": "POST", "path": "/checkout", "status": 502})
    looks = overlay_state(ws, ws.board, USER)["arrows"]["failing_calls"]
    assert looks[api_arrow.id] == {"source": api_arrow.source, "target": api_arrow.target,
                                   "endpoint": api_arrow.endpoint, "color": "red",
                                   "message": "last call failed (502)"}
    assert all(a.type == "api" for a in ws.board.arrows.values() if a.id in looks)  # import arrows untouched


# Input: tmp_path (Path)
# Output: None
# Processing: Block rules can read premade analyzers and log errors, fill messages, and fall through to an else.
# Changed by: Claude Code
# Date: 2026-11-25
def test_block_rules_use_premade_counts_and_logs(tmp_path: Path) -> None:
    ws = shop(tmp_path)
    (tmp_path / ".plovane" / "log_errors.json").write_text(json.dumps(
        {"files": {"api/app.py": {"count": 4, "lines": {"8": 4}}}}))
    rules = {"label": "Hot files", "rules": [
        {"on": "block", "if": {"kind": "file", "log.count": {">": 2}},
         "show": {"color": "red", "message": "{log.count} errors, worst line {log.worst_line}"}},
        {"on": "block", "if": {"kind": "file", "premade.api_endpoints": {">": 0}}, "show": {"badge": "api"}},
        {"on": "block", "if": {"kind": "file"}, "show": {"color": "gray", "message": "quiet"}}]}
    assert ws.command(f"/overlay rule hot_files --data '{json.dumps(rules)}'")["ok"]
    results = overlay_state(ws, ws.board, USER)["results"]["hot_files"]
    app = ws.board.block_for_path("api/app.py")
    cart = ws.board.block_for_path("web/cart.js")
    assert results[app.id]["items"][0] == {"message": "4 errors, worst line 8", "color": "red"}
    assert results[cart.id]["items"][0]["message"] == "quiet"
    assert results[app.parent]["count"] >= 1 and not results[app.parent]["own"]  # rolls up to the folder


# Input: tmp_path (Path)
# Output: None
# Processing: Only the person and the whiteboard AI may change the person's view; a premade overlay id cannot be overwritten.
# Changed by: Claude Code
# Date: 2026-11-25
def test_person_view_and_premade_ids_are_protected(tmp_path: Path) -> None:
    ws = shop(tmp_path)
    board_ai.ensure_board_ai(ws)
    ws.command("/agent add helper")
    ws.command("/agent grant helper . --mode read-write", confirm=True)
    assert ws.command("/overlay on cyber_security --person", actor="helper")["error"]["code"] == "FORBIDDEN"
    assert ws.command("/overlay on cyber_security --person", actor=BOARD_AI)["ok"]
    from backend.whiteboard.overlays import active

    assert "leaked_secrets" in active(ws.board, USER)
    clash = ws.command(f"/overlay rule log_errors --data '{json.dumps(FAILING)}'")
    assert clash["error"]["code"] == "CONFLICT"


# Input: tmp_path (Path), monkeypatch (pytest.MonkeyPatch)
# Output: None
# Processing: The first failing API call is a new kind of signal: the watcher asks the whiteboard AI, which writes a rule overlay once.
# Changed by: Claude Code
# Date: 2026-11-25
def test_new_signal_makes_the_ai_add_an_overlay(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    tasks: list[str] = []

    # Input: task (str), client (Any), **_ (Any)
    # Output: dict
    # Processing: Stand in for the model: write the failing-calls overlay.
    # Changed by: Claude Code
    # Date: 2026-11-25
    def fake_run(task, *, client, **_):
        tasks.append(task)
        reply = client.run_command(f"/overlay rule failing_calls --data '{json.dumps(FAILING)}'")
        return {"status": "done", "summary": "added failing_calls", "commands": [{"ok": reply["ok"]}]}

    monkeypatch.setattr(llm_agent, "run", fake_run)
    ws = shop(tmp_path)
    watcher = board_ai.ProjectWatcher(ws, threading.RLock(), quiet=1.0)
    assert watcher.poll(now=1.0) is None
    live.record(ws, {"method": "POST", "path": "/checkout", "status": 500})
    result = watcher.poll(now=2.0)
    assert set(result["overlays"]["new"]) == {"api_traffic", "api_failures"}
    assert "failing" in tasks[0] and ws.board.overlays["failing_calls"]["author"] == BOARD_AI
    assert ws.board.overlays["failing_calls"]["created_by"] == "ai"
    live.record(ws, {"method": "POST", "path": "/checkout", "status": 500})
    assert watcher.poll(now=3.0) is None and len(tasks) == 1  # not a new kind: no second AI call


# Input: tmp_path (Path)
# Output: None
# Processing: A rule overlay is written to the .plo file, comes back when the project is reopened, and matches the schema.
# Changed by: Claude Code
# Date: 2026-11-25
def test_rule_overlay_travels_in_the_plo_file(tmp_path: Path) -> None:
    from backend.whiteboard.engine import Workspace

    ws = shop(tmp_path)
    assert ws.command(f"/overlay rule failing_calls --data '{json.dumps(FAILING)}'")["ok"]
    assert "/overlay put failing_calls" in (tmp_path / "whiteboard.plo").read_text()
    again = Workspace(tmp_path)
    assert again.board.overlays["failing_calls"]["rules"] == ws.board.overlays["failing_calls"]["rules"]
    jsonschema = pytest.importorskip("jsonschema")
    schema = json.loads((Path(__file__).resolve().parents[3] / "spec" / "whiteboard" / "plo-v1.schema.json").read_text())
    validator = jsonschema.Draft202012Validator({"$defs": schema["$defs"], "$ref": "#/$defs/overlay"})
    assert [e.message for e in validator.iter_errors(ws.board.overlays["failing_calls"])] == []


# Input: none
# Output: None
# Processing: Every premade template fills in to valid rules; unknown templates, unknown blanks and empty required blanks are refused.
# Changed by: Claude Code
# Date: 2026-11-25
def test_every_template_fills_in_to_valid_rules() -> None:
    from backend.whiteboard.overlay_templates import TEMPLATES, instantiate

    needed = {"uses_library": {"module": "stripe"}, "area": {"path": "src/billing/*", "label": "Payments"}}
    for tid in TEMPLATES:
        spec = instantiate(tid, needed.get(tid, {}))
        assert validate(spec["rules"]) and "{" not in spec["label"], tid
    assert instantiate("outside_edits", {"minutes": 5})["rules"][0]["if"]["change.age"] == {"<": 300.0}
    for template, given, words in (("nope", {}, "no template"), ("big_files", {"size": 1}, "no blank"),
                                   ("area", {"label": "x"}, "needs 'path'"), ("big_files", {"lines": "many"}, "number")):
        with pytest.raises(ValueError, match=words):
            instantiate(template, given)


# Input: tmp_path (Path)
# Output: None
# Processing: Code facts: a file calling an outside API with no error handling is flagged; the README's area template marks its folder.
# Changed by: Claude Code
# Date: 2026-11-25
def test_templates_read_the_code(tmp_path: Path) -> None:
    ws = project(tmp_path, {**SHOP, "api/payments.py": (
        "import requests\n\n\ndef charge(amount):\n"
        "    return requests.post('https://api.stripe.com/v1/charges', data={'amount': amount})\n"),
        "api/safe.py": ("import requests\n\n\ndef ping():\n    try:\n"
                        "        return requests.get('https://status.example.com/ping')\n    except Exception:\n        return None\n")})
    assert ws.command("/project import", confirm=True)["ok"]
    made = ws.command("/overlay from unguarded_outside_calls")
    assert made["ok"] and ws.board.overlays["unguarded_outside_calls"]["template"] == "unguarded_outside_calls"
    assert ws.command("/overlay from area --id web_area --set '{\"path\": \"web/*\", \"label\": \"Shop front\"}'")["ok"]
    calls = {"label": "Calls Stripe", "rules": [
        {"on": "block", "if": {"code.calls": {"contains": "requests.post"}, "code.text": {"regex": "api\\.stripe\\.com"}},
         "show": {"badge": "stripe"}}]}
    assert ws.command(f"/overlay rule calls_stripe --data '{json.dumps(calls)}'")["ok"]
    state = overlay_state(ws, ws.board, USER)["results"]
    payments, safe = ws.board.block_for_path("api/payments.py"), ws.board.block_for_path("api/safe.py")
    cart = ws.board.block_for_path("web/cart.js")
    assert payments.id in state["unguarded_outside_calls"] and safe.id not in state["unguarded_outside_calls"]
    assert state["web_area"][cart.id]["items"][0]["message"] == "part of Shop front"
    assert payments.id not in state["web_area"]
    assert list(k for k, v in state["calls_stripe"].items() if v["own"]) == [payments.id]


# Input: tmp_path (Path)
# Output: None
# Processing: Change facts: an edit made outside the whiteboard (picked up by the watcher) shows up through the outside_edits template; a person's edit does not.
# Changed by: Claude Code
# Date: 2026-11-25
def test_outside_edits_show_through_rules(tmp_path: Path) -> None:
    ws = shop(tmp_path)
    assert ws.command("/overlay from outside_edits --set '{\"minutes\": 10}'")["ok"]
    watcher = board_ai.ProjectWatcher(ws, threading.RLock(), quiet=0.5)
    (tmp_path / "api" / "app.py").write_text((tmp_path / "api" / "app.py").read_text() + "\n# tuned by Claude\n")
    watcher.poll(now=1.0)
    watcher.poll(now=2.0)
    assert ws.command("/file save web/cart.js --content 'export const pay = () => 1\n'")["ok"]
    results = overlay_state(ws, ws.board, USER)["results"]["outside_edits"]
    app, cart = ws.board.block_for_path("api/app.py"), ws.board.block_for_path("web/cart.js")
    assert results[app.id]["items"][0]["badge"] == "just changed"
    assert cart.id not in results


# Input: tmp_path (Path), monkeypatch (pytest.MonkeyPatch)
# Output: None
# Processing: Build hands the README and architecture docs to the whiteboard AI along with the templates.
# Changed by: Claude Code
# Date: 2026-11-25
def test_build_reads_the_readme_and_architecture(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    seen: list[tuple[str, str]] = []

    # Input: task (str), client (Any), system (str), **_ (Any)
    # Output: dict
    # Processing: Record what the whiteboard AI was told, and mark the README's checkout area.
    # Changed by: Claude Code
    # Date: 2026-11-25
    def fake_run(task, *, client, system, **_):
        seen.append((task, system))
        reply = client.run_command("/overlay from area --id checkout --set '{\"path\": \"api/*\", \"label\": \"Checkout\"}'")
        return {"status": "done", "summary": "ok", "commands": [{"ok": reply["ok"]}]}

    monkeypatch.setattr(llm_agent, "run", fake_run)
    ws = project(tmp_path, {**SHOP, "README.md": "# Shop\nThe checkout service takes card payments.\n",
                            "docs/architecture.md": "Web front end -> checkout API -> Stripe.\n"})
    result = board_ai.build(ws)
    task, system = seen[0]
    assert "checkout service takes card payments" in task and "docs/architecture.md" in task
    assert "area (path, label, color)" in system and result["organise"]["status"] == "done"
    assert ws.board.overlays["checkout"]["template"] == "area"
