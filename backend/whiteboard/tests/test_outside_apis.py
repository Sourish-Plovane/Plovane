"""Tests for /outside scan: auto-detect outside APIs the code calls."""

from __future__ import annotations

from pathlib import Path

from whiteboard_helpers import ok, project

BILLING_PY = '''import requests
import httpx


def charge(amount):
    requests.post("https://api.stripe.com/v1/charges", data={"amount": amount}, verify=False)


def notify(order):
    httpx.get("https://hooks.slack.com/services/x/y/z", params={"order": order})


def local_only():
    requests.get("http://localhost:8080/health")
'''

CLIENT_JS = '''export async function completion(prompt) {
  const res = await fetch("https://api.openai.com/v1/chat/completions", { method: "POST", body: prompt })
  return res.json()
}

export async function user(id) {
  return await axios.get(`http://127.0.0.1:3000/users/${id}`)
}
'''

FILES = {
    "src/__init__.py": "",
    "src/billing.py": BILLING_PY,
    "web/client.js": CLIENT_JS,
}


# Input: tmp_path (Path)
# Output: None
# Processing: /outside scan finds the distinct outside hosts and adds one external block per host plus caller arrows; localhost is ignored.
# Changed by: Claude Code
# Date: 2026-11-24
def test_outside_scan_adds_external_blocks_per_host(tmp_path: Path) -> None:
    ws = project(tmp_path, FILES)
    ok(ws, "/project import", confirm=True)
    result = ok(ws, "/outside scan")

    assert result["hosts"] == 3  # stripe, slack, openai; localhost ignored
    hosts = set(result["found"])
    assert hosts == {"api.stripe.com", "hooks.slack.com", "api.openai.com"}

    board = ws.main.board
    externals = {b.name: b for b in board.blocks.values() if b.kind == "external"}
    assert set(externals) == {"stripe", "slack", "openai"}

    # Arrows connect the right source file to the right host, marked as scanned.
    links = {(a.source, board.blocks[a.target].name) for a in board.arrows.values()
             if a.meta.get("outside_scanned")}
    source_names = {s: board.blocks[s].name for s in {a.source for a in board.arrows.values() if a.meta.get("outside_scanned")}}
    assert ("billing.py", "stripe") in {(source_names[s], n) for s, n in links}
    assert ("billing.py", "slack") in {(source_names[s], n) for s, n in links}
    assert ("client.js", "openai") in {(source_names[s], n) for s, n in links}


# Input: tmp_path (Path)
# Output: None
# Processing: Running the scan twice doesn't duplicate blocks or arrows; a removed call cleans up its block.
# Changed by: Claude Code
# Date: 2026-11-24
def test_outside_scan_replaces_its_previous_results(tmp_path: Path) -> None:
    ws = project(tmp_path, FILES)
    ok(ws, "/project import", confirm=True)
    ok(ws, "/outside scan")
    ok(ws, "/outside scan")
    externals = [b for b in ws.main.board.blocks.values() if b.kind == "external" and b.meta.get("outside_scanned")]
    assert len(externals) == 3
    arrows = [a for a in ws.main.board.arrows.values() if a.meta.get("outside_scanned")]
    assert len(arrows) == 3

    # Delete the stripe + slack calls; next scan removes those blocks.
    (tmp_path / "src/billing.py").write_text("def charge(amount):\n    pass\n")
    ok(ws, "/outside scan")
    externals = sorted(b.name for b in ws.main.board.blocks.values() if b.kind == "external" and b.meta.get("outside_scanned"))
    assert externals == ["openai"]


# Input: tmp_path (Path)
# Output: None
# Processing: An external block a person made is reused by the scan instead of being duplicated.
# Changed by: Claude Code
# Date: 2026-11-24
def test_outside_scan_reuses_a_hand_made_external(tmp_path: Path) -> None:
    ws = project(tmp_path, FILES)
    ok(ws, "/project import", confirm=True)
    ok(ws, "/block add external stripe")  # already there, by hand
    before = len([b for b in ws.main.board.blocks.values() if b.kind == "external"])
    ok(ws, "/outside scan")
    after = {b.name for b in ws.main.board.blocks.values() if b.kind == "external"}
    assert after == {"stripe", "slack", "openai"}
    # The hand-made stripe stayed; only two scanned blocks were added.
    scanned = [b for b in ws.main.board.blocks.values() if b.kind == "external" and b.meta.get("outside_scanned")]
    assert {b.name for b in scanned} == {"slack", "openai"}
    assert before == 1
