"""Tests for readable whiteboards: text settings, theme neutrals, and check/fix from the browser's measurement."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from backend.whiteboard import board_ai
from backend.whiteboard.engine import BOARD_AI, USER
from backend.whiteboard.ops.legibility import contrast, neutrals, settings
from whiteboard_helpers import project

FILES = {"src/__init__.py": "", "src/billing/__init__.py": "",
         "src/billing/charge_customer_card_and_send_receipt.py": "def charge(amount):\n    return amount\n"}


# Input: tmp_path (Path)
# Output: Workspace
# Processing: A small project on the board with two services drawn by the whiteboard AI.
# Changed by: Claude Code
# Date: 2026-11-26
def board(tmp_path: Path):
    ws = project(tmp_path, FILES)
    assert ws.command("/project import", confirm=True)["ok"]
    board_ai.ensure_board_ai(ws)
    assert ws.command("/sketch service payments; service orders", actor=BOARD_AI)["ok"]
    return ws


# Input: none
# Output: None
# Processing: Contrast follows WCAG, and theme neutrals stay readable on their page while leaning toward the accent.
# Changed by: Claude Code
# Date: 2026-11-26
def test_contrast_and_theme_neutrals() -> None:
    assert round(contrast("#000000", "#FFFFFF"), 1) == 21.0 and contrast("#777777", "#777777") == 1.0
    for accent in (None, "#7357E8", "#FCCE03", "#D94A4A"):
        for mode in ("light", "dark"):
            n = neutrals(accent, mode)
            assert contrast(n["text"], n["page"]) >= 7 and contrast(n["muted"], n["page"]) >= 4.5, (accent, mode)
    assert neutrals("#7357E8", "light")["text"] != neutrals(None, "light")["text"]  # tinted toward the accent


# Input: tmp_path (Path)
# Output: None
# Processing: /theme accent and /readable set land in the person's own view and survive switching overlays.
# Changed by: Claude Code
# Date: 2026-11-26
def test_theme_and_targets_are_personal(tmp_path: Path) -> None:
    ws = board(tmp_path)
    look = ws.command("/theme accent #397BD1 --mode dark")["data"]["result"]
    assert look["mode"] == "dark" and contrast(look["text"], look["page"]) >= 7
    assert ws.command("/readable set --min-size 14 --contrast AAA")["ok"]
    assert ws.command("/overlay on test_health")["ok"]
    assert ws.command("/role use cyber")["ok"]
    mine = settings(ws.board, USER)
    assert mine["min_size"] == 14 and mine["contrast"] == "AAA" and mine["appearance"]["accent"] == "#397BD1"
    assert settings(ws.board, BOARD_AI)["min_size"] == 12  # someone else's view is untouched
    assert ws.command("/readable set --contrast AAAA")["error"]["code"] == "INVALID_ARGS"
    assert ws.command("/theme accent purple")["error"]["code"] == "INVALID_ARGS"


# Input: tmp_path (Path)
# Output: None
# Processing: /text commands reach one block, a kind or a layer; the person's text edits pin style so the AI leaves them.
# Changed by: Claude Code
# Date: 2026-11-26
def test_text_commands_and_pins(tmp_path: Path) -> None:
    ws = board(tmp_path)
    assert set(ws.command("/text size kind:service 1.5", actor=BOARD_AI)["data"]["result"]["changed"]) == {"orders", "payments"}
    assert ws.command("/text size payments 9")["error"]["code"] == "INVALID_ARGS"
    assert ws.command("/text color orders #1F2328")["ok"]
    assert "style" in ws.board.blocks["orders"].meta["pinned"]
    assert ws.command("/text color orders auto", actor=BOARD_AI)["data"]["result"]["changed"] == []  # pinned: left alone
    assert ws.command("/text weight payments bold", actor=BOARD_AI)["ok"]
    assert ws.board.blocks["payments"].style == {"text_scale": 1.5, "text_weight": "bold"}
    assert ws.command("/text size payments auto", actor=BOARD_AI)["ok"] and "text_scale" not in ws.board.blocks["payments"].style
    long_file = ws.board.block_for_path("src/billing/charge_customer_card_and_send_receipt.py")
    assert ws.command(f"/text shorten {long_file.id} --to \"Charges cards\"", actor=BOARD_AI)["ok"]
    assert long_file.meta["short_name"] == "Charges cards" and long_file.name.endswith(".py")


# Input: tmp_path (Path)
# Output: None
# Processing: check/fix work from the browser's report: small text is scaled to the minimum, faint text goes back to automatic colour, cut-off text wraps then asks for a short label, long names are listed.
# Changed by: Claude Code
# Date: 2026-11-26
def test_check_and_fix_from_the_browser_report(tmp_path: Path) -> None:
    ws = board(tmp_path)
    ws.command("/text color orders #EEEEEE", actor=BOARD_AI)
    report = {"issues": [{"block": "payments", "kind": "small", "px": 8},
                         {"block": "orders", "kind": "contrast", "ratio": 1.2},
                         {"block": "src", "kind": "overflow", "overflow": 1.6},
                         {"block": "ghost", "kind": "small", "px": 3}]}
    assert ws.command(f"/readable report --data '{json.dumps(report)}'")["data"]["result"]["issues"] == 3
    assert ws.command(f"/readable report --data '{json.dumps(report)}'", actor=BOARD_AI)["error"]["code"] == "FORBIDDEN"
    checked = ws.command("/readable check", actor=BOARD_AI)["data"]["result"]
    kinds = {(p["block"], p["kind"]) for p in checked["problems"]}
    long_file = ws.board.block_for_path("src/billing/charge_customer_card_and_send_receipt.py")
    assert {("payments", "small"), ("orders", "contrast"), ("src", "overflow"), (long_file.id, "long_label")} <= kinds
    assert checked["measured"] is True

    fixed = ws.command("/readable fix", actor=BOARD_AI)["data"]["result"]
    assert ws.board.blocks["payments"].style["text_scale"] == 1.5  # 12px wanted / 8px measured
    assert "text_color" not in ws.board.blocks["orders"].style
    assert ws.board.blocks["src"].style["text_wrap"] == 2
    assert long_file.id in fixed["needs_short_label"]
    again = ws.command("/readable fix", actor=BOARD_AI)["data"]["result"]  # still overflowing after wrapping
    assert "src" in again["needs_short_label"]


# Input: tmp_path (Path)
# Output: None
# Processing: The board reply carries the asker's readability settings, so the browser can apply them.
# Changed by: Claude Code
# Date: 2026-11-26
def test_board_reply_carries_readable_settings(tmp_path: Path) -> None:
    pytest.importorskip("flask")
    from backend.whiteboard.server import create_app

    ws = board(tmp_path)
    ws.command("/readable set --min-size 15")
    data = create_app(ws).test_client().get("/api/v1/whiteboard/board").get_json()["data"]
    assert data["readable"]["min_size"] == 15 and data["readable"]["appearance"]["mode"] == "light"
