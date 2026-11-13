"""AI outlines (/blocks generate) and saving edited code (/file save)."""

from __future__ import annotations

import json

from whiteboard_helpers import ScriptedLLM, fails, ok, project

BILLING = "\n".join([
    "import stripe",                      # 1
    "",                                   # 2
    "RATE = 3",                           # 3
    "",                                   # 4
    "def charge(card, amount):",          # 5
    "    if amount <= 0:",                # 6
    "        raise ValueError('amount')", # 7
    "    token = stripe.token(card)",     # 8
    "    receipt = stripe.charge(token)", # 9
    "    save(receipt)",                  # 10
    "    return receipt",                 # 11
    "",                                   # 12
    "def save(receipt):",                 # 13
    "    print(receipt)",                 # 14
]) + "\n"

OUTLINE = {
    "summary": "Charges cards through Stripe and keeps the receipts.",
    "blocks": [
        {"name": "Setup and settings", "summary": "Imports Stripe and sets the rate.", "lines": [1, 3]},
        {"name": "Charge a card", "summary": "Checks the amount and charges it.", "lines": [5, 11],
         "children": [
             {"name": "Check the amount", "summary": "Refuses zero or less.", "lines": [6, 7]},
             {"name": "Charge through Stripe", "summary": "Gets a token and charges.", "lines": [8, 9]},
             {"name": "Overlaps the one before", "lines": [9, 10]},
             {"name": "Keep the receipt", "lines": [10, 11]},
         ]},
        {"name": "Save a receipt", "summary": "Prints it.", "lines": [13, 99]},
    ],
}


# Input: ws (Workspace)
# Output: dict[str, dict]
# Processing: The AI blocks on the board by name.
# Changed by: Claude Code
# Date: 2026-11-09
def ai_blocks(ws) -> dict:
    return {b.name: b for b in ws.board.blocks.values() if b.meta.get("source") == "ai"}


# Input: tmp_path
# Output: None
# Processing: Check that the AI's outline becomes nested blocks, cleaned against the file.
# Changed by: Claude Code
# Date: 2026-11-09
def test_ai_outline_becomes_nested_blocks(tmp_path):
    llm = ScriptedLLM("Here it is:\n```json\n" + json.dumps(OUTLINE) + "\n```")
    ws = project(tmp_path, {"src/billing.py": BILLING}, llm=llm)
    result = ok(ws, "/blocks generate src/billing.py --provider deepseek")
    assert "52 | " not in llm.prompts[0] and "    5 | def charge(card, amount):" in llm.prompts[0]
    blocks = ai_blocks(ws)
    assert set(blocks) == {"Setup and settings", "Charge a card", "Check the amount", "Charge through Stripe",
                           "Keep the receipt", "Save a receipt"}
    file_block = ws.board.block_for_path("src/billing.py")
    charge = blocks["Charge a card"]
    assert charge.parent == file_block.id and blocks["Check the amount"].parent == charge.id
    assert blocks["Save a receipt"].meta["lines"] == [13, 14]  # clamped to the file
    assert file_block.meta["summary"].startswith("Charges cards")
    assert result["files"][0]["by"] == "deepseek" and result["blocks"] == 6
    reply = ws.command("/undo")
    assert reply["ok"] and not ai_blocks(ws)


# Input: tmp_path
# Output: None
# Processing: Check that a tree passed by an MCP agent is drawn without calling an AI, and replaces the old one.
# Changed by: Claude Code
# Date: 2026-11-09
def test_agent_passes_its_own_outline_and_regenerating_replaces_it(tmp_path):
    llm = ScriptedLLM()
    ws = project(tmp_path, {"src/billing.py": BILLING}, llm=llm)
    reply = ws.execute("blocks.generate", {"target": "src/billing.py", "blocks": OUTLINE["blocks"]})
    assert reply["ok"] and not llm.prompts and len(ai_blocks(ws)) == 6
    again = ws.execute("blocks.generate", {"target": "src/billing.py",
                                           "blocks": json.dumps([{"name": "Everything", "lines": [1, 14]}])})
    assert again["ok"] and set(ai_blocks(ws)) == {"Everything"}
    assert any("overlap" in note for note in reply["data"]["notes"])


# Input: tmp_path
# Output: None
# Processing: Check the errors for no usable blocks, a missing file and a too-deep request.
# Changed by: Claude Code
# Date: 2026-11-09
def test_outline_errors(tmp_path):
    ws = project(tmp_path, {"src/billing.py": BILLING}, llm=ScriptedLLM("no json here"))
    assert fails(ws, "/blocks generate src/billing.py") == "LLM_BAD_REPLY"
    assert fails(ws, "/blocks generate src/missing.py") == "NOT_FOUND"
    assert fails(ws, "/blocks generate src/billing.py --depth 9") == "INVALID_ARGS"
    assert ws.execute("blocks.generate", {"target": "src", "blocks": []})["error"]["code"] == "INVALID_ARGS"


# Input: tmp_path
# Output: None
# Processing: Check that a folder target outlines every source file inside it.
# Changed by: Claude Code
# Date: 2026-11-09
def test_folder_outlines_each_file(tmp_path):
    reply = json.dumps({"blocks": [{"name": "All of it", "lines": [1, 2]}]})
    ws = project(tmp_path, {"src/a.py": "x = 1\ny = 2\n", "src/b.py": "z = 3\n", "src/notes.txt": "hi"},
                 llm=ScriptedLLM(reply, reply))
    result = ok(ws, "/blocks generate src")
    assert sorted(f["file"] for f in result["files"]) == ["src/a.py", "src/b.py"]


# Input: tmp_path
# Output: None
# Processing: Check that /file save writes the file, refuses a stale base, refreshes line numbers and undoes.
# Changed by: Claude Code
# Date: 2026-11-09
def test_file_save_writes_checks_base_and_undoes(tmp_path):
    ws = project(tmp_path, {"src/billing.py": BILLING})
    ok(ws, "/blocks view src/billing.py --detail 1")
    save = next(b for b in ws.board.blocks.values() if b.kind == "function" and b.name == "save")
    assert save.meta["lines"] == [13, 14]
    edited = "# billing\n" + BILLING
    reply = ws.execute("file.save", {"path": "src/billing.py", "content": edited, "base": BILLING})
    assert reply["ok"] and reply["data"]["result"]["changed"]
    assert (tmp_path / "src/billing.py").read_text() == edited
    assert ws.board.blocks[save.id].meta["lines"] == [14, 15]
    stale = ws.execute("file.save", {"path": "src/billing.py", "content": "x", "base": BILLING})
    assert stale["error"]["code"] == "CONFLICT"
    assert ws.command("/undo")["ok"] and (tmp_path / "src/billing.py").read_text() == BILLING
    assert ws.execute("file.save", {"path": "src/nope.py", "content": "x"})["error"]["code"] == "NOT_FOUND"


# Input: tmp_path
# Output: None
# Processing: Check that the project import never walks tool folders such as node_modules or .claude worktrees.
# Changed by: Claude Code
# Date: 2026-11-12
def test_import_skips_tool_and_worktree_folders(tmp_path):
    ws = project(tmp_path, {"src/app.py": "x = 1\n", "node_modules/lib/index.js": "1",
                            ".claude/worktrees/copy/src/app.py": "x = 1\n", "pkg.egg-info/x.py": "y = 2\n"})
    result = ok(ws, "/project import")
    assert result["files_found"] == 1 and ws.board.block_for_path("src/app.py") is not None
