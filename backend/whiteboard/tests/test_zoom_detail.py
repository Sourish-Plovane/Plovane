"""Zooming in shows specifics: arrows say which code makes them happen, and files open as small parts."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from backend.whiteboard import board_ai, llm_agent
from backend.whiteboard.engine import BOARD_AI
from backend.whiteboard.refactor import symbols
from whiteboard_helpers import project

EXPRESS = {
    "README.md": "# Shop\nA small shop.\n",
    "app.js": ("const express = require('express');\nconst userController = require('./controllers/user');\n"
               "const { isAuthenticated } = require('./config/passport');\n\nconst app = express();\n"
               "app.get('/login', userController.getLogin);\napp.post('/login', userController.postLogin);\n"
               "app.get('/account', isAuthenticated, userController.getAccount);\n"),
    "config/passport.js": "exports.isAuthenticated = (req, res, next) => {\n  if (req.user) return next();\n  res.redirect('/login');\n};\n",
    "controllers/user.js": (
        "const User = require('../models/User');\n\n/**\n * GET /login\n * Login page.\n */\n"
        "exports.getLogin = (req, res) => {\n  res.render('login'); // don't log in twice\n};\n\n"
        "exports.postLogin = async (req, res, next) => {\n  const user = await User.findOne({ email: req.body.email });\n"
        "  const check = (u) => {\n    return !!u;\n  };\n  if (!check(user)) { return res.redirect('/login'); }\n  next();\n};\n\n"
        "const getAccount = function (req, res) {\n  res.render('account', { user: `${req.user}` });\n};\n"
        "module.exports.getAccount = getAccount;\n"),
    "models/User.js": "const userSchema = {};\nmodule.exports = userSchema;\n",
}


# Input: none
# Output: None
# Processing: Express-style functions (exports.x = arrow, const x = function) are found with their lines and comments; a helper inside a function stays part of it.
# Changed by: Claude Code
# Date: 2026-11-27
def test_javascript_assigned_functions() -> None:
    found = symbols(EXPRESS["controllers/user.js"], "controllers/user.js")
    assert [(s.name, s.start_line) for s in found] == [("getLogin", 7), ("postLogin", 11), ("getAccount", 20)]
    assert found[0].docstring.startswith("GET /login") and found[1].end_line == 18
    assert symbols(EXPRESS["config/passport.js"], "config/passport.js")[0].signature == "(req, res, next)"


# Input: tmp_path (Path)
# Output: None
# Processing: Import arrows say what is used: the members called through a whole-module require, or the names taken.
# Changed by: Claude Code
# Date: 2026-11-27
def test_import_arrows_say_what_is_used(tmp_path: Path) -> None:
    ws = project(tmp_path, EXPRESS)
    assert ws.command("/project import", confirm=True)["ok"]
    detail = {(ws.board.blocks[a.source].path, ws.board.blocks[a.target].path): a.meta.get("detail")
              for a in ws.board.arrows.values() if a.type == "import"}
    assert detail[("app.js", "controllers/user.js")] == ["uses getLogin, postLogin, getAccount"]
    assert detail[("app.js", "config/passport.js")] == ["uses isAuthenticated"]
    assert detail[("controllers/user.js", "models/User.js")] == ["uses findOne"]


# Input: tmp_path (Path)
# Output: None
# Processing: /arrow detail keeps real files and functions (with their line), drops an unknown function, refuses an unknown file, and clears with no steps.
# Changed by: Claude Code
# Date: 2026-11-27
def test_arrow_detail_is_checked_against_the_code(tmp_path: Path) -> None:
    ws = project(tmp_path, EXPRESS)
    assert ws.command('/sketch service "Sign in"; database Users; "Sign in" -> Users "checks the password"')["ok"]
    steps = [{"file": "controllers/user.js", "function": "postLogin", "does": "finds the user by email"},
             {"file": "models/User.js", "function": "nope", "does": "holds the user"}]
    reply = ws.command(f"/arrow detail sign-in->users --steps '{json.dumps(steps)}'")
    assert reply["ok"]
    arrow = next(a for a in ws.board.arrows.values() if a.source == "sign-in")
    assert arrow.meta["detail"] == ["controllers/user.js · postLogin: finds the user by email", "models/User.js: holds the user"]
    assert arrow.meta["via"][0]["line"] == 11 and arrow.meta["via"][1]["function"] is None
    assert not ws.command("""/arrow detail sign-in->users --steps '[{"file": "missing.js"}]'""")["ok"]
    assert ws.command("/arrow detail sign-in->users --steps '[]'")["ok"]
    assert "detail" not in ws.board.arrows[arrow.id].meta


# Input: tmp_path (Path), monkeypatch (pytest.MonkeyPatch)
# Output: None
# Processing: A build breaks a long file into parts, points the drawn arrows at code, and a second build replaces its own drawing instead of adding another; a person's box stays.
# Changed by: Claude Code
# Date: 2026-11-27
def test_build_adds_parts_and_arrow_details(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")
    monkeypatch.setattr(board_ai, "MIN_OUTLINE_LINES", 10)
    drawings = iter(['service "Login"\ndatabase "Users"\n"Login" -> "Users" "checks the password"',
                     'service "Sign in"\ndatabase "Users"\n"Sign in" -> "Users" "looks up the user"'])

    # Input: provider (str), model (str), system (str), prompt (str), **_ (Any)
    # Output: tuple[str, dict]
    # Processing: A stand-in AI answering the drawing, outline and arrow prompts.
    # Changed by: Claude Code
    # Date: 2026-11-27
    def fake_complete(provider, model, system, prompt, **_):
        if system == board_ai.ARROW_SYSTEM:
            arrow = prompt.split("\n")[1].split(":")[0]
            reply = {"arrows": [{"id": arrow, "steps": [{"file": "controllers/user.js", "function": "postLogin",
                                                         "does": "finds the user by email"}]}]}
        elif "with line numbers" in prompt:
            reply = {"summary": "Logs people in.", "blocks": [{"name": "Show the login page", "lines": [7, 9]},
                                                               {"name": "Check the password", "lines": [11, 18]}]}
        else:
            return next(drawings), {"input_tokens": 1, "output_tokens": 1}
        return json.dumps(reply), {"input_tokens": 1, "output_tokens": 1}

    monkeypatch.setattr(llm_agent, "complete", fake_complete)
    monkeypatch.setattr(llm_agent, "run", lambda task, **k: {"status": "done", "commands": [], "summary": "Done."})
    ws = project(tmp_path, EXPRESS)
    result = board_ai.build(ws, provider="deepseek")
    assert result["parts"]["files"] == 1 and result["arrow_details"]["detailed"] == 1
    owner = ws.board.block_for_path("controllers/user.js")
    assert {b.name for b in ws.board.blocks.values() if b.parent == owner.id} == {"Show the login page", "Check the password"}
    arrow = next(a for a in ws.board.arrows.values() if a.style.get("label") == "checks the password")
    assert arrow.meta["detail"] == ["controllers/user.js · postLogin: finds the user by email"]
    assert ws.command('/sketch service "Payments"', actor="user")["ok"]
    again = board_ai.build(ws, provider="deepseek")
    names = {b.name for b in ws.board.blocks.values() if b.kind in ("service", "database")}
    assert again["replaced"] == 2 and names == {"Sign in", "Users", "Payments"}
    assert ws.board.blocks["sign-in"].meta["by"] == BOARD_AI
