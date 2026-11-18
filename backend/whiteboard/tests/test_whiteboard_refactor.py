"""The reliability fixes: renames follow real references, classes and methods
appear as blocks, relative imports are drawn, moves fix imports, and layout
reaches blocks inside folders. Where it matters the result is executed, not
just read."""

from __future__ import annotations

import subprocess
import sys

from whiteboard_helpers import fails, ok, project

PKG = {
    "pkg/__init__.py": "",
    "pkg/orders.py": (
        '"""Orders."""\n'
        "from .util import helper\n\n"
        '__all__ = ["total"]\n\n\n'
        "def total(items):\n"
        "    result = helper()\n"
        "    for item in items:\n"
        "        result += item\n"
        "    return result\n\n\n"
        "class Cart:\n"
        "    def __init__(self):\n"
        "        self.items = []\n\n"
        "    def add(self, item):\n"
        "        self.items.append(item)\n"
        "        return self\n\n"
        "    def size(self):\n"
        "        return len(self.add(0).items)\n"
    ),
    "pkg/util.py": "def helper():\n    return 0\n",
    "pkg/report.py": (
        "def summary(rows):\n"
        "    total = len(rows)\n"
        '    return f"{total} rows"\n'
    ),
    "app.py": (
        "import pkg.orders\n"
        "from pkg import orders as o\n"
        "from pkg.orders import total, Cart\n"
        "from pkg.orders import total as t\n\n\n"
        "def shadow(total):\n"
        "    return total\n\n\n"
        "print(total([1, 2]), pkg.orders.total([3]), o.total([4]), t([5]), shadow(6), Cart().size())\n"
    ),
}

EXPECTED = "3 3 4 5 6 1\n"


# Input: root (Path)
# Output: str
# Processing: Run app.py in the project and return what it printed.
# Changed by: Sourish
# Date: 2026-10-22
def run_app(root) -> str:
    done = subprocess.run([sys.executable, "app.py"], cwd=root, capture_output=True, text=True)
    assert done.returncode == 0, done.stderr
    return done.stdout


# Input: tmp_path (Path)
# Output: None
# Processing: Check that a function rename follows every real reference and nothing else.
# Changed by: Sourish
# Date: 2026-10-22
def test_rename_follows_real_references_only(tmp_path):
    ws = project(tmp_path, PKG)
    assert run_app(tmp_path) == EXPECTED
    ok(ws, "/project import")
    ok(ws, "/block split orders.py --by function")
    changed = ok(ws, "/block rename orders-py-total sum_items")["files_changed"]
    assert changed == ["app.py", "pkg/orders.py"]
    report = (tmp_path / "pkg/report.py").read_text()
    assert "total = len(rows)" in report  # the unrelated local is untouched
    app = (tmp_path / "app.py").read_text()
    assert "def shadow(total):\n    return total" in app  # a parameter of the same name is untouched
    assert "from pkg.orders import sum_items as t" in app and "t([5])" in app
    assert '__all__ = ["sum_items"]' in (tmp_path / "pkg/orders.py").read_text()
    assert run_app(tmp_path) == EXPECTED


# Input: tmp_path (Path)
# Output: None
# Processing: Check that classes and methods become blocks and steps.
# Changed by: Sourish
# Date: 2026-10-22
def test_classes_and_methods_are_blocks(tmp_path):
    ws = project(tmp_path, PKG)
    ok(ws, "/project import")
    created = ok(ws, "/block split orders.py --by function")["created"]
    assert {"orders-py-cart", "orders-py-cart-add", "orders-py-cart-size", "orders-py-total"} <= set(created)
    assert ws.board.block("orders-py-cart").kind == "class"
    assert ws.board.block("orders-py-cart-add").parent == "orders-py-cart"
    steps = ok(ws, "/blocks view pkg/orders.py --detail 2")["steps"]
    assert {"Cart.__init__", "Cart.add", "Cart.size", "total"} == {s["function"] for s in steps}
    assert "def add(self, item)" in ok(ws, "/block info orders-py-cart-add")["code"]


# Input: tmp_path (Path)
# Output: None
# Processing: Check that renaming a method or class keeps the program working.
# Changed by: Sourish
# Date: 2026-10-22
def test_rename_method_and_class(tmp_path):
    ws = project(tmp_path, PKG)
    ok(ws, "/project import")
    ok(ws, "/block split orders.py --by function")
    ok(ws, "/block rename orders-py-cart-add push")
    orders = (tmp_path / "pkg/orders.py").read_text()
    assert "def push(self, item)" in orders and "self.push(0)" in orders
    ok(ws, "/block rename orders-py-cart Basket")
    assert "Basket().size()" in (tmp_path / "app.py").read_text()
    assert ws.board.block("orders-py-cart-add").symbol == "Basket.push"  # ids stay stable
    assert run_app(tmp_path) == EXPECTED


# Input: tmp_path (Path)
# Output: None
# Processing: Check that deleting a class's methods keeps the class valid Python.
# Changed by: Sourish
# Date: 2026-10-22
def test_deleting_methods_keeps_the_class_valid(tmp_path):
    ws = project(tmp_path, {"shapes.py": "class Shape:\n    def area(self):\n        return 0\n"})
    ok(ws, "/project import")
    ok(ws, "/block split shapes.py --by function")
    ok(ws, "/block delete shapes-py-shape-area", confirm=True)
    text = (tmp_path / "shapes.py").read_text()
    compile(text, "shapes.py", "exec")
    assert "pass" in text and "def area" not in text


# Input: tmp_path (Path)
# Output: None
# Processing: Check that relative imports are drawn as arrows.
# Changed by: Sourish
# Date: 2026-10-22
def test_relative_imports_are_drawn(tmp_path):
    ws = project(tmp_path, PKG | {"pkg/sub/__init__.py": "", "pkg/sub/deep.py": "from .. import util\nfrom ..orders import total\n"})
    ok(ws, "/project import")
    arrows = {(a.source, a.target) for a in ws.board.arrows.values()}
    assert ("orders-py", "util-py") in arrows
    assert ("deep-py", "util-py") in arrows and ("deep-py", "orders-py") in arrows


# Input: tmp_path (Path)
# Output: None
# Processing: Check that moving and renaming Python files fixes every import, and undo restores them.
# Changed by: Sourish
# Date: 2026-10-22
def test_moving_python_files_fixes_imports(tmp_path):
    ws = project(tmp_path, PKG | {"lib/__init__.py": ""})
    ok(ws, "/project import")
    moved = ok(ws, "/block move util.py --into lib")
    assert moved["imports_updated"] == ["pkg/orders.py"]
    assert "from lib.util import helper" in (tmp_path / "pkg/orders.py").read_text()
    assert run_app(tmp_path) == EXPECTED
    moved = ok(ws, "/block move orders.py --into lib")
    assert moved["imports_updated"] == ["app.py"]
    app = (tmp_path / "app.py").read_text()
    assert "import lib.orders" in app and "lib.orders.total([3])" in app and "from lib import orders as o" in app
    assert run_app(tmp_path) == EXPECTED
    ok(ws, "/block rename orders.py shop.py")
    assert "from lib.shop import total, Cart" in (tmp_path / "app.py").read_text()
    assert run_app(tmp_path) == EXPECTED
    for _ in range(3):
        ok(ws, "/undo")
    assert (tmp_path / "app.py").read_text() == PKG["app.py"]
    assert run_app(tmp_path) == EXPECTED


# Input: tmp_path (Path)
# Output: None
# Processing: Check that a moved file's own relative imports are rewritten.
# Changed by: Sourish
# Date: 2026-10-22
def test_a_moved_files_relative_imports_still_resolve(tmp_path):
    ws = project(tmp_path, PKG | {"lib/__init__.py": ""})
    ok(ws, "/project import")
    moved = ok(ws, "/block move orders.py --into lib")
    assert set(moved["imports_updated"]) == {"app.py", "lib/orders.py"}
    assert "from pkg.util import helper" in (tmp_path / "lib/orders.py").read_text()
    assert run_app(tmp_path) == EXPECTED


# Input: tmp_path (Path)
# Output: None
# Processing: Check that renaming a package folder fixes the imports into it.
# Changed by: Sourish
# Date: 2026-10-22
def test_renaming_a_package_fixes_imports(tmp_path):
    ws = project(tmp_path, PKG)
    ok(ws, "/project import")
    result = ok(ws, "/block rename pkg shop")
    assert "app.py" in result["files_changed"]
    assert "from shop.orders import total, Cart" in (tmp_path / "app.py").read_text()
    assert run_app(tmp_path) == EXPECTED


# Input: tmp_path (Path)
# Output: None
# Processing: Check that moving JavaScript files fixes relative imports both ways.
# Changed by: Sourish
# Date: 2026-10-22
def test_moving_js_files_fixes_relative_imports(tmp_path):
    ws = project(tmp_path, {"web/index.js": "import { x } from './lib';\nconst y = require('./util.js');\n",
                            "web/lib.js": "import { helper } from './util';\nexport const x = 1;\n",
                            "web/util.js": "export function helper() { return 1; }\n",
                            "web/shared/keep.js": ""})
    ok(ws, "/project import")
    ok(ws, "/block move lib.js --into web/shared")
    assert "from './shared/lib'" in (tmp_path / "web/index.js").read_text()
    assert "from '../util'" in (tmp_path / "web/shared/lib.js").read_text()
    ok(ws, "/block move util.js --into web/shared")
    index = (tmp_path / "web/index.js").read_text()
    assert "require('./shared/util.js')" in index
    assert "from './util'" in (tmp_path / "web/shared/lib.js").read_text()


# Input: tmp_path (Path)
# Output: None
# Processing: Check that layout arranges blocks inside folders too.
# Changed by: Sourish
# Date: 2026-10-22
def test_layout_reaches_blocks_inside_folders(tmp_path):
    ws = project(tmp_path, {"src/a.py": "import src.b\n", "src/b.py": "import src.c\n", "src/c.py": ""})
    ok(ws, "/project import")
    for ref in ("a.py", "b.py", "c.py"):
        ok(ws, f"/place {ref} --at 0,0")
    moved = ok(ws, "/layout left-to-right --layer 3")["moved"]
    assert set(moved) == {"a-py", "b-py", "c-py"}
    xs = [ws.board.block(f).x for f in ("a.py", "b.py", "c.py")]
    assert xs == sorted(xs) and len(set(xs)) == 3
    folder = ws.board.block("src")
    assert all(ws.board.block(f).x + ws.board.block(f).w <= folder.w for f in ("a.py", "b.py", "c.py"))


# Input: tmp_path (Path)
# Output: None
# Processing: Check that rename refuses names that are not identifiers.
# Changed by: Sourish
# Date: 2026-10-22
def test_rename_rejects_bad_identifiers(tmp_path):
    ws = project(tmp_path, PKG)
    ok(ws, "/project import")
    ok(ws, "/block split orders.py --by function")
    assert fails(ws, "/block rename orders-py-total 'two words'") == "INVALID_ARGS"


# Input: tmp_path (Path)
# Output: None
# Processing: Check that test files are mapped and their imports fixed on a move.
# Changed by: Sourish
# Date: 2026-10-22
def test_test_files_are_part_of_the_project(tmp_path):
    ws = project(tmp_path, PKG | {"tests/test_orders.py": "from pkg.orders import total\n\n\ndef test_total():\n"
                                                      "    assert total([1]) == 1\n", "lib/__init__.py": ""})
    ok(ws, "/project import")
    assert ws.board.block_for_path("tests/test_orders.py") is not None
    assert "tests/test_orders.py" in ok(ws, "/block move orders.py --into lib")["imports_updated"]
    assert "from lib.orders import total" in (tmp_path / "tests/test_orders.py").read_text()
