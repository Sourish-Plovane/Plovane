"""Live view, readable code and whiteboards built for one job (ops/live.py, readable.py, views.py)."""

from __future__ import annotations

import json
import urllib.request

from backend.whiteboard.ops import live
from backend.whiteboard.ops.readable import rule_line
from whiteboard_helpers import ScriptedLLM, fails, ok, project

FILES = {
    "src/payments/routes.py": '"""Payment routes: charge a card and refund it."""\nimport sqlite3\n\n\n'
                              "def charge(amount, user):\n    total = amount * 100\n    if total <= 0:\n"
                              '        raise ValueError("bad")\n'
                              '    db.execute(f"SELECT * FROM users WHERE id = {user}")\n    return total\n',
    "src/payments/email.py": "def send(to):\n    return True\n",
    "web/index.html": '<title>Shop</title><a href="/cart.html">cart</a>',
    "web/cart.html": '<title>Cart</title><a href="/">home</a>',
    "README.md": "# Shop\n\nA tiny shop backend that charges cards.\n",
}


# Input: tmp_path
# Output: Workspace
# Processing: An imported project with a payments service, its endpoint, and an agent allowed into src.
# Changed by: Claude Code
# Date: 2026-11-18
def shop(tmp_path, llm=None):
    ws = project(tmp_path, FILES, llm=llm)
    ok(ws, "/project import")
    ok(ws, "/block add service payments")
    ok(ws, "/endpoint add payments POST /charge/{id}")
    ok(ws, "/agent add claude")
    ok(ws, "/agent grant claude src --mode read-write", confirm=True)
    return ws


# Input: tmp_path
# Output: None
# Processing: An agent's changes mark the files it touched; a folder orchestration counts them; done clears both.
# Changed by: Claude Code
# Date: 2026-11-18
def test_agent_work_and_orchestration_show_live(tmp_path):
    ws = shop(tmp_path)
    events = []
    ws.listeners.append(events.append)
    ok(ws, '/agent working src/payments "add refunds"', actor="claude")
    ok(ws, "/file create src/payments/refund.py", actor="claude")
    view = live.snapshot(ws)
    assert view["files"]["src/payments/refund.py"]["agent"] == "claude"
    job = view["orchestration"]["src/payments"]
    assert (job["agent"], job["task"], job["done"], job["total"]) == ("claude", "add refunds", 1, 3)
    assert any(e["kind"] == "live" and e.get("what") == "orchestration" for e in events)
    assert fails(ws, '/agent working web "peek"', actor="claude") == "FORBIDDEN"  # no access to web
    assert fails(ws, "/agent working src") == "INVALID_ARGS"  # a person must name the agent
    ok(ws, "/agent done", actor="claude")
    view = live.snapshot(ws)
    assert not view["files"] and not view["orchestration"]
    assert "live" not in (tmp_path / "whiteboard.plo").read_text()  # live state is never saved in the file


# Input: tmp_path, monkeypatch
# Output: None
# Processing: Marks fade after the quiet period, and people's own changes never mark anything.
# Changed by: Claude Code
# Date: 2026-11-18
def test_work_marks_fade_and_people_are_not_marked(tmp_path, monkeypatch):
    ws = shop(tmp_path)
    ok(ws, "/file create src/payments/mine.py")
    assert not live.snapshot(ws)["files"]
    ok(ws, "/file create src/payments/theirs.py", actor="claude")
    assert "src/payments/theirs.py" in live.snapshot(ws)["files"]
    later = live.time.time() + live.ACTIVITY_TTL + 1
    monkeypatch.setattr(live.time, "time", lambda: later)
    assert not live.snapshot(ws)["files"]


# Input: tmp_path
# Output: None
# Processing: Reported requests turn an endpoint red or green; unknown routes and attacks are noted.
# Changed by: Claude Code
# Date: 2026-11-18
def test_traffic_sets_api_status_and_catches_attacks(tmp_path):
    ws = shop(tmp_path, llm=ScriptedLLM("The amount arrived as text; convert it to a number first."))
    ep = "payments:POST /charge/{id}"
    ok(ws, "/traffic POST /charge/42 500")
    assert live.snapshot(ws)["api"][ep]["ok"] is False
    rows = ok(ws, "/api status")["endpoints"]
    assert rows == [{"endpoint": ep, "state": "fail", "status": 500, "count": 1, "fails": 1}]
    assert "convert it to a number" in ok(ws, f"/api explain '{ep}'")["explanation"]
    ok(ws, "/traffic POST /charge/43 201")
    assert live.snapshot(ws)["api"][ep]["ok"] is True
    reply = ws.execute("traffic.record", {"requests": [
        {"method": "GET", "path": "/search?q=1'%20OR%201=1--", "status": 200, "ip": "1.2.3.4"},
        {"method": "GET", "path": "/files?name=../../etc/passwd", "status": 404},
        {"method": "GET", "path": "/.env", "status": 404},
        *[{"method": "POST", "path": "/login", "status": 401, "ip": "9.9.9.9"}] * 5]})
    found = [kind for r in reply["data"]["result"]["results"] for kind in r["threats"]]
    assert found == ["sql_injection", "path_traversal", "scanner", "brute_force"]
    view = live.snapshot(ws)
    assert {"/search", "/files", "/.env", "/login"} <= set(view["threats"])
    assert [u["path"] for u in view["unmatched"]][:1] == ["/search"]
    assert (tmp_path / ".plovane" / "security.log").read_text().count("\n") == 4
    assert ok(ws, "/security --clear")["events"]


# Input: tmp_path
# Output: None
# Processing: The code scan finds weak spots by rule, and the board shows them per file.
# Changed by: Claude Code
# Date: 2026-11-18
def test_security_scan_finds_weak_spots(tmp_path):
    ws = shop(tmp_path)
    (tmp_path / "src" / "bad.js").write_text('const api_key = "sk_live_1234567890abcdef";\nel.innerHTML = name;\n'
                                             "const key = process.env.API_KEY;\n")
    result = ok(ws, "/security scan")
    kinds = {(s["file"], s["kind"]) for s in result["weak_spots"]}
    assert kinds == {("src/payments/routes.py", "sql_from_text"), ("src/bad.js", "secret_in_code"),
                     ("src/bad.js", "raw_html")}
    assert live.snapshot(ws)["weak_spots"]["src/bad.js"] == {"count": 2, "worst": "high"}


# Input: tmp_path
# Output: None
# Processing: Summaries are free by default, cached by fingerprint, rewritten when the file changes, and kept in the .plo.
# Changed by: Claude Code
# Date: 2026-11-18
def test_summaries_free_cached_and_shared(tmp_path):
    ws = shop(tmp_path)
    first = ok(ws, "/summary")
    assert first["about"] == "A tiny shop backend that charges cards."
    assert first["files"]["src/payments/routes.py"] == "Payment routes: charge a card and refund it."
    assert first["files"]["src/payments/email.py"] == "Defines send"
    assert first["files"]["web/index.html"] == "Web page: Shop"
    assert ok(ws, "/summary")["counts"] == {"cached": len(first["files"]), "free": 0, "ai": 0}
    (tmp_path / "src/payments/email.py").write_text('"""Sends receipts by email."""\ndef send(to):\n    return 1\n')
    assert ok(ws, "/summary src")["counts"]["free"] == 1
    ok(ws, '/summary set src/payments/routes.py "Takes card payments"', actor="claude")
    assert ok(ws, "/summary")["files"]["src/payments/routes.py"] == "Takes card payments"  # kept until the file changes
    plo = (tmp_path / "whiteboard.plo").read_text()
    assert "Takes card payments" in plo and "/project describe" in plo


# Input: tmp_path
# Output: None
# Processing: With a provider, every stale file is summarised in a single AI call, the project line too.
# Changed by: Claude Code
# Date: 2026-11-18
def test_summaries_use_one_ai_call(tmp_path):
    llm = ScriptedLLM(json.dumps({"_project": "An online shop", "src/payments/routes.py": "Charges cards",
                                  "src/payments/email.py": "Emails receipts"}))
    ws = project(tmp_path, {k: v for k, v in FILES.items() if k.startswith("src")}, llm=llm)
    ok(ws, "/project import")
    result = ok(ws, "/summary --provider anthropic")
    assert len(llm.prompts) == 1
    assert result["about"] == "An online shop"
    assert result["files"] == {"src/payments/email.py": "Emails receipts", "src/payments/routes.py": "Charges cards"}
    ok(ws, "/summary --provider anthropic")
    assert len(llm.prompts) == 1  # nothing stale, nothing asked


# Input: tmp_path
# Output: None
# Processing: Line by line: rules word common lines for free; the AI words only the rest, once, then the cache does.
# Changed by: Claude Code
# Date: 2026-11-18
def test_translate_line_by_line(tmp_path):
    ws = shop(tmp_path)
    result = ok(ws, "/translate src/payments/routes.py")
    words = {row["line"]: row["english"] for row in result["lines"]}
    assert words[5] == "Define charge taking amount and user"
    assert words[6] == "Set total to amount * 100"
    assert words[7] == "If total <= 0"
    assert result["free_share"] == 1.0 and result["needs_ai"] == 0
    assert rule_line("for (const item of cart.items) {", "javascript") == ("For each item in cart.items", "rule")
    assert rule_line("export async function pay(amount) {", "javascript") == ("Define async pay taking amount", "rule")
    assert rule_line("import { x } from './y'", "javascript") == ("Bring in { x } from ./y", "rule")
    (tmp_path / "src" / "odd.js").write_text("x ?? y\n")
    llm = ScriptedLLM('["Use x, or y when x is missing"]')
    ws._llm = llm
    first = ok(ws, "/translate src/odd.js --provider anthropic")
    assert first["lines"][0]["source"] == "ai" and len(llm.prompts) == 1
    again = ok(ws, "/translate src/odd.js --provider anthropic")
    assert again["lines"][0] == {"line": 1, "code": "x ?? y", "english": "Use x, or y when x is missing",
                                 "source": "cache"}
    assert len(llm.prompts) == 1


# Input: tmp_path
# Output: None
# Processing: Whiteboards built for one job: each its own .plo, made by an agent too, kept unless refreshed.
# Changed by: Claude Code
# Date: 2026-11-18
def test_board_make_each_kind(tmp_path):
    ws = shop(tmp_path)
    ok(ws, "/connect src -> payments --type api --endpoint 'POST /charge/{id}'", confirm=True)
    for kind in ("structure", "api", "security", "frontend"):
        made = ok(ws, f"/board make {kind}", actor="claude")
        assert made["board"] == f"whiteboards/{kind}.plo"
        text = (tmp_path / made["board"]).read_text()
        assert json.loads(text)["format"] == "plovane-whiteboard" and f"about-{kind}" in text
        assert "/agent put" not in text
    api = (tmp_path / "whiteboards/api.plo").read_text()
    assert "/endpoint put" in api and "--type api" in api and "src/payments/routes.py" not in api
    assert "routes.py" in (tmp_path / "whiteboards/security.plo").read_text()  # it has a weak spot
    front = (tmp_path / "whiteboards/frontend.plo").read_text()
    assert "page-home" in front and "page-cart-html" in front and "link-page-home-page-cart-html" in front
    assert ok(ws, "/board make api")["existing"] is True
    assert ok(ws, "/boards")["boards"][0] == "whiteboard.plo"
    assert fails(ws, "/board make maps") == "INVALID_ARGS"
    assert fails(ws, "/board make api --path whiteboard.plo --refresh") == "INVALID_ARGS"
    pages = ok(ws, "/pages")
    assert pages["folder"] == "web" and [p["route"] for p in pages["pages"]] == ["/", "/cart.html"]


# Input: tmp_path
# Output: None
# Processing: /frontend start serves plain HTML pages with a local server, and the frontend whiteboard links to them.
# Changed by: Claude Code
# Date: 2026-11-18
def test_frontend_start_serves_pages(tmp_path):
    ws = shop(tmp_path)
    started = ok(ws, "/frontend start")
    try:
        assert started["url"].startswith("http://127.0.0.1:")
        page = urllib.request.urlopen(started["url"] + "/cart.html", timeout=5).read().decode()
        assert "<title>Cart</title>" in page
        ok(ws, "/board make frontend")
        assert started["url"] + "/cart.html" in (tmp_path / "whiteboards/frontend.plo").read_text()
        assert fails(ws, "/frontend start", actor="claude") == "FORBIDDEN"  # web is outside its access
    finally:
        assert ok(ws, "/frontend stop")["stopped"] is True
