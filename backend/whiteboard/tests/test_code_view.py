"""Issue #64: /blocks turns a file into plain-English steps on the English steps layer."""

from __future__ import annotations

from backend.whiteboard.ops.english import brace_sentence, line_steps
from whiteboard_helpers import ok, project

CART = '''def total(items, tax):
    subtotal = 0
    for item in items:
        subtotal += item.price
    return subtotal * (1 + tax)
'''


# Input: tmp_path
# Output: None
# Processing: /blocks takes a relative, root-relative or absolute path and puts steps on layer 4.
# Changed by: Claude
# Date: 2026-11-10
def test_blocks_shorthand_and_steps_layer(tmp_path):
    ws = project(tmp_path, {"shop/cart.py": CART})
    ok(ws, "/project import")
    for path in ("shop/cart.py", "/shop/cart.py", f"{tmp_path.resolve()}/shop/cart.py"):
        result = ok(ws, f"/blocks {path}")
        assert result["file"] == "shop/cart.py"
        steps = [b for b in ws.board.blocks.values() if b.kind == "step"]
        assert [s.meta["english"] for s in steps] == ["Set subtotal to 0", "For each item in items",
                                                      "Return subtotal * (1 + tax)"]
        assert {s.layer for s in steps} == {4}
    assert (tmp_path / "shop/cart.py").read_text() == CART  # turning code into blocks never edits it


# Input: none
# Output: None
# Processing: C-like statements read as plain English, and braceless bodies are indented.
# Changed by: Claude
# Date: 2026-11-10
def test_c_like_steps_read_as_plain_english():
    assert brace_sentence("for (uint8_t i = 0; i < 7; i++)") == "Repeat for each i from 0 up to 7"
    assert brace_sentence("if (!ready)") == "If not ready"
    assert brace_sentence("total += price") == "Add price to total"
    assert brace_sentence("DateTime now = rtc.now()") == "Set now to rtc.now()"
    assert brace_sentence("Wire.begin()") == "Call Wire.begin"
    assert brace_sentence("return false") == "Give back false"
    code = "bool f() {\n  if (!ok)\n    return false;\n  count++;\n  return true;\n}".splitlines()
    assert [(s["english"], s["depth"]) for s in line_steps(code, 1, len(code))] == [
        ("If not ok", 0), ("Give back false", 1), ("Add 1 to count", 0), ("Give back true", 0)]


# Input: tmp_path
# Output: None
# Processing: /file save writes the file, rebuilds its blocks, refuses stale saves and is undoable (#65).
# Changed by: Claude
# Date: 2026-11-10
def test_file_save_rebuilds_blocks_refuses_stale_and_undoes(tmp_path):
    ws = project(tmp_path, {"shop/cart.py": CART})
    ok(ws, "/project import")
    ok(ws, "/blocks shop/cart.py")
    opened = ws.execute("code.open", {"target": "shop/cart.py"})["data"]["result"]
    edited = CART.replace("subtotal = 0", "subtotal = 10")
    saved = ws.execute("file.save", {"path": "shop/cart.py", "content": edited, "base": opened["version"]})
    assert saved["ok"] and saved["data"]["result"]["blocks_refreshed"]
    assert (tmp_path / "shop/cart.py").read_text() == edited
    assert "Set subtotal to 10" in [b.meta["english"] for b in ws.board.blocks.values() if b.kind == "step"]
    stale = ws.execute("file.save", {"path": "shop/cart.py", "content": "x", "base": opened["version"]})
    assert stale["error"]["code"] == "CONFLICT" and (tmp_path / "shop/cart.py").read_text() == edited
    ok(ws, "/undo")
    assert (tmp_path / "shop/cart.py").read_text() == CART


# Input: tmp_path
# Output: None
# Processing: An agent without write access cannot save, and cannot read a file by path without read access.
# Changed by: Claude
# Date: 2026-11-10
def test_file_save_and_open_follow_agent_access(tmp_path):
    ws = project(tmp_path, {"shop/cart.py": CART})
    ok(ws, "/agent add bot")
    assert ws.execute("file.save", {"path": "shop/cart.py", "content": "x"}, actor="bot")["error"]["code"] == "FORBIDDEN"
    assert ws.execute("code.open", {"target": "shop/cart.py"}, actor="bot")["error"]["code"] == "FORBIDDEN"
    assert (tmp_path / "shop/cart.py").read_text() == CART


# Input: tmp_path
# Output: None
# Processing: /files lists project files, skips tooling folders, and narrows to one folder (#66).
# Changed by: Claude
# Date: 2026-11-10
def test_files_lists_project_files(tmp_path):
    ws = project(tmp_path, {"shop/cart.py": CART, "README.md": "hi", "node_modules/x/index.js": "",
                            ".venv/lib/a.py": ""})
    listed = ok(ws, "/files")
    assert [f["path"] for f in listed["files"]] == ["README.md", "shop/cart.py"]
    assert [f["path"] for f in ok(ws, "/files shop")["files"]] == ["shop/cart.py"]
