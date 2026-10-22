"""Every whiteboard command category against a real temporary project."""

from __future__ import annotations

import json

from whiteboard_helpers import RETRY_PY, ScriptedLLM, fails, ok, project

ORDERS = '''"""Orders."""


def total(items):
    """Sum the prices."""
    result = 0
    for item in items:
        result += item["price"]
    return result


def describe(order):
    return "order " + str(order)
'''

TEST_ORDERS = '''from orders import total


def test_total():
    assert total([{"price": 2}, {"price": 3}]) == 5
'''


# -- Layers and zoom --------------------------------------------------------------


# Input: tmp_path
# Output: None
# Processing: Check that layers define edit rename remove.
# Changed by: Sourish
# Date: 2026-10-22
def test_layers_define_edit_rename_remove(tmp_path):
    ws = project(tmp_path)
    assert fails(ws, "/layer define 6 --zoom 95-99") == "CONFLICT"  # overlaps layer 5
    ok(ws, "/layer edit 5 --zoom 90-95")
    ok(ws, "/layer define 6 --zoom 95-100 --show code --name Deep")
    ok(ws, '/layer rename 6 "Very deep"')
    ok(ws, '/block add note "n" --layer 6')
    removed = ok(ws, "/layer remove 6", confirm=True)
    assert removed["blocks_moved_to"] == 5 and ws.board.block("n").layer == 5
    assert [l["number"] for l in ok(ws, "/layer list")["layers"]] == [0, 1, 2, 3, 4, 5]


# Input: tmp_path
# Output: None
# Processing: Check that zoom into and out.
# Changed by: Sourish
# Date: 2026-10-22
def test_zoom_into_and_out(tmp_path):
    ws = project(tmp_path, {"src/a.py": "A = 1\n"})
    ok(ws, "/project import")
    assert ok(ws, "/zoom 50")["layer"]["number"] == 3
    into = ok(ws, "/zoom into src")
    assert into["focus"] == "src" and into["layer"]["number"] == 3 and "a-py" in into["inside"]
    assert ok(ws, "/zoom out")["layer"]["number"] == 2
    assert fails(ws, "/zoom 101") == "NOT_FOUND"


# -- Project structure and blocks -------------------------------------------------


# Input: tmp_path
# Output: None
# Processing: Check that project import maps files and imports.
# Changed by: Sourish
# Date: 2026-10-22
def test_project_import_maps_files_and_imports(tmp_path):
    ws = project(tmp_path, {"app/main.py": "import app.util\n", "app/util.py": "X = 1\n",
                            "web/index.js": "import { x } from './lib';\n", "web/lib.js": "export const x = 1;\n"})
    result = ok(ws, "/project import")
    assert result["files_found"] == 4 and len(result["import_arrows_added"]) == 2
    assert ok(ws, "/project import")["blocks_added"] == []  # idempotent


# Input: tmp_path
# Output: None
# Processing: Check that file templates and folder blocks.
# Changed by: Sourish
# Date: 2026-10-22
def test_file_templates_and_folder_blocks(tmp_path):
    ws = project(tmp_path)
    ok(ws, "/file create api/app.py --template flask-app")
    assert "Flask(__name__)" in (tmp_path / "api/app.py").read_text()
    assert ws.board.block("api").kind == "folder"
    assert fails(ws, "/file create api/app.py") == "CONFLICT"
    assert fails(ws, "/file create x.py --template nope") == "INVALID_ARGS"


# Input: tmp_path
# Output: None
# Processing: Check that moving a file block moves the file.
# Changed by: Sourish
# Date: 2026-10-22
def test_moving_a_file_block_moves_the_file(tmp_path):
    ws = project(tmp_path, {"a/x.py": "X = 1\n", "b/keep.py": ""})
    ok(ws, "/project import")
    moved = ok(ws, "/block move x.py --into b")
    assert moved["moved_file"] == {"from": "a/x.py", "to": "b/x.py"}
    assert (tmp_path / "b/x.py").exists() and not (tmp_path / "a/x.py").exists()
    assert ws.board.block("x-py").path == "b/x.py"


# Input: tmp_path
# Output: None
# Processing: Check that renaming a function renames it across files.
# Changed by: Sourish
# Date: 2026-10-22
def test_renaming_a_function_renames_it_across_files(tmp_path):
    ws = project(tmp_path, {"orders.py": ORDERS, "use.py": "from orders import total\nprint(total([]))\n"})
    ok(ws, "/project import")
    ok(ws, "/block split orders.py --by function")
    renamed = ok(ws, "/block rename orders-py-total sum_prices")
    assert sorted(renamed["files_changed"]) == ["orders.py", "use.py"]
    assert "def sum_prices(items)" in (tmp_path / "orders.py").read_text()
    assert fails(ws, "/block rename orders-py-describe 'bad name'") == "INVALID_ARGS"


# Input: tmp_path
# Output: None
# Processing: Check that deleting a function block removes its code.
# Changed by: Sourish
# Date: 2026-10-22
def test_deleting_a_function_block_removes_its_code(tmp_path):
    ws = project(tmp_path, {"orders.py": ORDERS})
    ok(ws, "/project import")
    ok(ws, "/block split orders.py --by function")
    deleted = ok(ws, "/block delete orders-py-describe", confirm=True)
    assert deleted["code_removed"].startswith("orders.py:")
    text = (tmp_path / "orders.py").read_text()
    assert "def describe" not in text and "def total" in text


# Input: tmp_path
# Output: None
# Processing: Check that group merge and info.
# Changed by: Sourish
# Date: 2026-10-22
def test_group_merge_and_info(tmp_path):
    ws = project(tmp_path, {"a.py": "A = 1\n", "b.py": "B = 2\n"})
    ok(ws, "/project import")
    grouped = ok(ws, "/block group a.py,b.py --as core --kind folder --path core")
    assert (tmp_path / "core/a.py").exists() and grouped["members"] == ["a-py", "b-py"]
    merged = ok(ws, "/block merge a.py,b.py", confirm=True)
    assert merged["files_merged"] == ["core/b.py"]
    assert "B = 2" in (tmp_path / "core/a.py").read_text() and not (tmp_path / "core/b.py").exists()
    info = ok(ws, "/block info a.py")
    assert "B = 2" in info["code"] and info["parent"] == "core"


# Input: tmp_path
# Output: None
# Processing: Check that block add routes files and refuses derived kinds.
# Changed by: Sourish
# Date: 2026-10-22
def test_block_add_routes_files_and_refuses_derived_kinds(tmp_path):
    ws = project(tmp_path)
    assert ok(ws, "/block add file lib/x.py")["path"] == "lib/x.py"
    assert fails(ws, "/block add step something") == "INVALID_ARGS"
    assert fails(ws, "/block add widget w") == "INVALID_ARGS"


# -- Placement and style ----------------------------------------------------------


# Input: tmp_path
# Output: None
# Processing: Check that relative placement alignment and size.
# Changed by: Sourish
# Date: 2026-10-22
def test_relative_placement_alignment_and_size(tmp_path):
    ws = project(tmp_path)
    ok(ws, '/block add service "auth" --at 200,120')
    ok(ws, '/block add service "billing" --right-of auth --gap 80')
    auth, billing = ws.board.block("auth"), ws.board.block("billing")
    assert (billing.x, billing.y) == (auth.x + auth.w + 80, auth.y)
    ok(ws, "/place billing --at 600,300")
    ok(ws, "/align auth,billing --top")
    assert ws.board.block("billing").y == ws.board.block("auth").y
    ok(ws, "/distribute auth,billing --horizontal --gap 60")
    assert ws.board.block("billing").x == auth.x + auth.w + 60
    ok(ws, "/size auth 240x120")
    assert (ws.board.block("auth").w, ws.board.block("auth").h) == (240, 120)
    ok(ws, "/grid 10 --snap off")
    assert ok(ws, "/coords auth")["w"] == 240


# Input: tmp_path
# Output: None
# Processing: Check that style presets and validation.
# Changed by: Sourish
# Date: 2026-10-22
def test_style_presets_and_validation(tmp_path):
    ws = project(tmp_path)
    ok(ws, '/block add service "auth"')
    ok(ws, "/preset define brand --fill #112233 --radius 4")
    styled = ok(ws, "/style auth --preset brand --border #445566")
    assert styled["style"] == {"fill": "#112233", "radius": 4, "border": "#445566"}
    assert fails(ws, "/style auth --fill blue") == "INVALID_STYLE"
    assert fails(ws, "/style auth --shape star") == "INVALID_STYLE"
    assert ok(ws, "/theme dark")["theme"] == "dark"


# -- Arrows -----------------------------------------------------------------------


# Input: tmp_path
# Output: None
# Processing: Check that import arrows write and remove the import.
# Changed by: Sourish
# Date: 2026-10-22
def test_import_arrows_write_and_remove_the_import(tmp_path):
    ws = project(tmp_path, {"app/a.py": '"""A."""\n', "app/b.py": "B = 1\n"})
    ok(ws, "/project import")
    ok(ws, "/connect a.py -> b.py --type import")
    assert "import app.b" in (tmp_path / "app/a.py").read_text()
    assert ok(ws, "/deps a.py")["drawn"][0]["block"] == "b-py"
    assert ok(ws, "/deps b.py --reverse")["in_code"] == [{"file": "app/a.py", "drawn": True}]
    ok(ws, "/arrow style a.py->b.py --width 3 --dash 4")
    ok(ws, "/arrow route a.py->b.py --curve orthogonal")
    ok(ws, "/disconnect a.py -> b.py", confirm=True)
    assert "import app.b" not in (tmp_path / "app/a.py").read_text()


# Input: tmp_path
# Output: None
# Processing: Check that api arrow without an endpoint is planned.
# Changed by: Sourish
# Date: 2026-10-22
def test_api_arrow_without_an_endpoint_is_planned(tmp_path):
    ws = project(tmp_path)
    ok(ws, '/block add service "web"')
    ok(ws, '/block add service "api"')
    assert ok(ws, "/connect web -> api --type api")["status"] == "planned"
    assert len(ok(ws, "/connections api")["incoming"]) == 1


# -- Endpoints --------------------------------------------------------------------


# Input: tmp_path
# Output: None
# Processing: Check that endpoint lifecycle.
# Changed by: Sourish
# Date: 2026-10-22
def test_endpoint_lifecycle(tmp_path):
    ws = project(tmp_path, {"web/checkout.py": '"""Checkout."""\n'})
    ok(ws, "/project import")
    ok(ws, '/block add service "payments"')
    ok(ws, "/endpoint add payments POST /charge --request '{\"amount\": \"integer\"}' --response Receipt")
    routes = (tmp_path / "services/payments/routes.py").read_text()
    assert '@bp.route("/charge", methods=["POST"])' in routes and "# Processing: Handle POST /charge." in routes
    ok(ws, "/endpoint link checkout.py -> payments:POST /charge")
    assert "def call_payments_post_charge" in (tmp_path / "web/checkout.py").read_text()
    tested = ok(ws, "/endpoint test payments:POST /charge --body sample")
    assert tested["status"] == 501 and tested["sent"] == {"amount": 1}
    assert ok(ws, "/endpoint check all")["ok"]
    assert ok(ws, "/trace 'POST /charge'")["hops"][0] == {"from": "checkout-py", "to": "payments",
                                                         "via": "payments:POST /charge", "type": "api"}
    edited = ok(ws, "/endpoint edit payments:POST /charge --path /charges", confirm=True)
    assert edited["broken_callers"] == ["checkout-py"]
    assert '@bp.route("/charges", methods=["POST"])' in (tmp_path / "services/payments/routes.py").read_text()
    assert not ok(ws, "/endpoint check all")["ok"]
    listed = ok(ws, "/endpoint list payments")["endpoints"][0]
    assert listed["callers"] == ["checkout-py"] and listed["id"] == "payments:POST /charges"
    assert "methods=" in ok(ws, "/endpoint info payments:POST /charges")["handler_code"]
    ok(ws, "/endpoint unlink checkout.py -> payments:POST /charges", confirm=True)
    assert "call_payments" not in (tmp_path / "web/checkout.py").read_text()
    ok(ws, "/endpoint delete payments:POST /charges", confirm=True)
    assert "/charges" not in (tmp_path / "services/payments/routes.py").read_text()


# Input: tmp_path
# Output: None
# Processing: Check that mock export import and external.
# Changed by: Sourish
# Date: 2026-10-22
def test_mock_export_import_and_external(tmp_path):
    ws = project(tmp_path)
    ok(ws, '/block add service "users"')
    ok(ws, "/endpoint add users GET /users --response '{\"id\": \"integer\", \"name\": \"string\"}'")
    ok(ws, "/endpoint mock users:GET /users")
    assert ok(ws, "/endpoint test users:GET /users")["body"] == {"id": 1, "name": "example"}
    exported = ok(ws, "/endpoint export users --out openapi.json")
    assert "/users" in exported["openapi"]["paths"]
    imported = ok(ws, "/endpoint import openapi.json --service users-copy")
    assert imported["endpoints"] == ["users-copy:GET /users"]
    external = ok(ws, "/api external add stripe --spec openapi.json --base-url https://api.stripe.com")
    assert external["endpoints"] == ["stripe:GET /users"]


# -- Templates --------------------------------------------------------------------


# Input: tmp_path
# Output: None
# Processing: Check that templates apply preview create edit fill.
# Changed by: Sourish
# Date: 2026-10-22
def test_templates_apply_preview_create_edit_fill(tmp_path):
    ws = project(tmp_path)
    preview = ok(ws, "/template preview worker-queue --name jobs")
    assert [b["name"] for b in preview["would_place"]][:3] == ["jobs-api", "jobs-queue", "jobs-worker"]
    assert ws.board.blocks == {}
    applied = ok(ws, "/template apply rest-api-service --name orders --at 0,0")
    assert "orders-db" in applied["blocks"] and len(applied["arrows"]) == 1
    assert fails(ws, "/template apply rest-api-service --name orders") == "CONFLICT"
    created = ok(ws, "/template create mine --from layer 1 --placeholder orders")
    assert created["blocks"] >= 2 and "validate-input" in created["slots"]
    assert any(b["name"] == "{name}" for b in ws.board.templates["mine"].blocks)
    ok(ws, "/template edit mine --add-slot cache --slot-query 'cache a function result'")
    assert "cache" in ok(ws, "/template list --layer services")["templates"][-1]["slots"]
    filled = ok(ws, "/template fill orders --language python")
    assert {f["entry"] for f in filled["filled"]} <= {"demo:retry_request", "demo:slugify", None}
    assert any(b.status == "filled" for b in ws.board.blocks.values())


# -- Library ----------------------------------------------------------------------


# Input: tmp_path
# Output: None
# Processing: Check that library rank insert palette cite.
# Changed by: Sourish
# Date: 2026-10-22
def test_library_rank_insert_palette_cite(tmp_path):
    ws = project(tmp_path, {"lib/__init__.py": ""})
    ranked = ok(ws, "/rank call a function retrying when it raises --k 1 --language python")
    assert ranked["recommendations"][0]["block_id"] == "demo:retry_request"
    inserted = ok(ws, "/insert demo:retry_request --into lib")
    text = (tmp_path / "lib/retry_request.py").read_text()
    assert text.count("def retry_request") == 1 and "Licence: MIT" in text
    assert ok(ws, "/palette text")["entries"][0]["name"] == "slugify"
    assert fails(ws, "/palette nothing-here") == "NOT_FOUND"
    cited = ok(ws, f"/cite {inserted['block']}")
    assert cited["inserted"][0]["entry"] == "demo:retry_request"
    assert cited["matching_library_code"][0]["entry"] == "demo:retry_request"


# Input: tmp_path
# Output: None
# Processing: Check that suggest and save.
# Changed by: Sourish
# Date: 2026-10-22
def test_suggest_and_save(tmp_path):
    ws = project(tmp_path, {"orders.py": ORDERS, "test_orders.py": TEST_ORDERS, "retry_copy.py": RETRY_PY})
    ok(ws, "/project import")
    suggested = ok(ws, "/suggest orders.py")["functions"]
    assert {f["name"] for f in suggested} == {"total", "describe"}
    saved = ok(ws, "/save orders.py --private")
    assert saved["saved"] and saved["checks"]["tests"] is True
    assert json.loads((tmp_path / ".plovane/submissions.jsonl").read_text())["status"] == "pending_verification"
    duplicate = ok(ws, "/save retry_copy.py")
    assert not duplicate["saved"] and duplicate["checks"]["duplicates"] == {"retry_request": "demo:retry_request"}


# -- English blocks and compile ---------------------------------------------------


# Input: tmp_path
# Output: None
# Processing: Check that blocks view at each detail.
# Changed by: Sourish
# Date: 2026-10-22
def test_blocks_view_at_each_detail(tmp_path):
    ws = project(tmp_path, {"orders.py": ORDERS})
    assert [s["english"] for s in ok(ws, "/blocks view orders.py --detail 1")["steps"]] == \
        ["Sum the prices.", "describe(order)"]
    two = ok(ws, "/blocks view orders.py --detail 2")["steps"]
    assert [s["english"] for s in two if s["function"] == "total"] == [
        "Set result to 0", "For each item in items", "Return result"]
    three = ok(ws, "/blocks view orders.py --detail 3")["steps"]
    assert any(s["english"] == "Add item['price'] to result" and s["depth"] == 1 for s in three)


# Input: tmp_path
# Output: None
# Processing: Check that edit compile and apply runs the tests.
# Changed by: Sourish
# Date: 2026-10-22
def test_edit_compile_and_apply_runs_the_tests(tmp_path):
    llm = ScriptedLLM("result = 10")
    ws = project(tmp_path, {"orders.py": ORDERS, "test_orders.py": TEST_ORDERS}, llm=llm)
    steps = ok(ws, "/blocks view orders.py --detail 2")["steps"]
    first = next(s["block"] for s in steps if s["english"] == "Set result to 0")
    edited = ok(ws, f'/block edit {first} "Start the total at ten"')
    preview = edited["compile"]
    assert "-    result = 0\n+    result = 10" in preview["diff"]
    assert preview["tests"]["ran"] and preview["tests"]["passed"] is False
    assert fails(ws, f"/compile apply {preview['compile']}", confirm=True) == "TESTS_FAILED"
    assert "result = 0" in (tmp_path / "orders.py").read_text()


# Input: tmp_path
# Output: None
# Processing: Check that compile applies a passing change.
# Changed by: Sourish
# Date: 2026-10-22
def test_compile_applies_a_passing_change(tmp_path):
    llm = ScriptedLLM("```python\nresult = 0  # start from zero\n```")
    ws = project(tmp_path, {"orders.py": ORDERS, "test_orders.py": TEST_ORDERS}, llm=llm)
    steps = ok(ws, "/blocks view orders.py --detail 2")["steps"]
    first = next(s["block"] for s in steps if s["english"] == "Set result to 0")
    compile_id = ok(ws, f'/block edit {first} "Start from zero"')["compile"]["compile"]
    applied = ok(ws, f"/compile apply {compile_id}", confirm=True)
    assert applied["tests"] is True and "# start from zero" in (tmp_path / "orders.py").read_text()
    assert fails(ws, "/compile blocks-to-code orders.py") == "NOTHING_TO_COMPILE"


# Input: tmp_path
# Output: None
# Processing: Check that replace a step with a library helper.
# Changed by: Sourish
# Date: 2026-10-22
def test_replace_a_step_with_a_library_helper(tmp_path):
    ws = project(tmp_path, {"fetch.py": "def load(get):\n    data = get()\n    return data\n"})
    steps = ok(ws, "/blocks view fetch.py --detail 2")["steps"]
    preview = ok(ws, f"/block replace {steps[0]['block']} --with demo:retry_request --times 3")
    assert "+    data = retry_request(times=3)" in preview["diff"] and "+def retry_request" in preview["diff"]
    ok(ws, f"/compile apply {preview['compile']}", confirm=True)
    assert "From the Plovane library: demo:retry_request (MIT)" in (tmp_path / "fetch.py").read_text()


# Input: tmp_path
# Output: None
# Processing: Check that without an llm the edit is kept without a preview.
# Changed by: Sourish
# Date: 2026-10-22
def test_without_an_llm_the_edit_is_kept_without_a_preview(tmp_path):
    from backend.whiteboard.registry import OpError

    ws = project(tmp_path, {"orders.py": ORDERS})

    # Input: prompt, system
    # Output: None (raises OpError)
    # Processing: Stand-in LLM that reports it is unavailable.
    # Changed by: Sourish
    # Date: 2026-10-22
    def unavailable(prompt, system=None):
        raise OpError("LLM_UNAVAILABLE", "ANTHROPIC_API_KEY is not set")

    ws._llm = unavailable
    steps = ok(ws, "/blocks view orders.py")["steps"]
    reply = ws.command(f'/block edit {steps[0]["block"]} "Start at one"')
    assert reply["ok"] and "compile" not in reply["data"]["result"] and reply["data"]["notes"]
    assert fails(ws, "/blocks view orders.py") == "CONFLICT"  # the edit is not discarded silently


# -- Code, health, history and view ----------------------------------------------


# Input: tmp_path
# Output: None
# Processing: Check that open diff explain.
# Changed by: Sourish
# Date: 2026-10-22
def test_open_diff_explain(tmp_path):
    ws = project(tmp_path, {"orders.py": ORDERS}, git=True)
    ok(ws, "/project import")
    assert ok(ws, "/open orders.py")["content"] == ORDERS
    (tmp_path / "orders.py").write_text(ORDERS + "\nX = 1\n")
    assert "+X = 1" in ok(ws, "/diff orders.py")["diff"]
    ws._llm = ScriptedLLM("It adds up prices.")
    assert ok(ws, "/explain orders.py")["explanation"] == "It adds up prices."
    assert "total(items)" in ok(ws, "/explain orders.py --ai false")["explanation"]


# Input: tmp_path
# Output: None
# Processing: Check that scan alerts and health.
# Changed by: Sourish
# Date: 2026-10-22
def test_scan_alerts_and_health(tmp_path):
    ws = project(tmp_path, {"bad.py": 'import subprocess\nPASSWORD = "hunter2hunter2"\n'
                                      'subprocess.run("ls", shell=True)\neval("1")\n',
                            "web/x.js": "el.innerHTML = input;\n"})
    ok(ws, "/project import")
    found = ok(ws, "/scan bad.py")["findings"]
    assert {f["line"] for f in found} == {2, 3, 4}
    ok(ws, "/scan web")
    assert ok(ws, "/alerts --severity high")["count"] == 3
    assert ok(ws, "/alerts")["count"] == 4
    (tmp_path / "web/x.js").unlink()
    problems = ok(ws, "/health")["problems"]
    assert any("missing on disk" in p["problem"] for p in problems)


# Input: tmp_path
# Output: None
# Processing: Check that monitor shows live values.
# Changed by: Sourish
# Date: 2026-10-22
def test_monitor_shows_live_values(tmp_path):
    import time

    ws = project(tmp_path, {"job.py": "print('progress=50 status=ok', flush=True)\n"})
    ok(ws, "/project import")
    ok(ws, "/monitor start job.py")
    for _ in range(50):
        state = ok(ws, "/monitor status job.py")["monitors"]["job-py"]
        if state["values"]:
            break
        time.sleep(0.1)
    assert state["values"] == {"progress": "50", "status": "ok"}
    assert ok(ws, "/monitor stop job.py")["monitors"]["job-py"] == {"stopped": True}


# Input: tmp_path
# Output: None
# Processing: Check that snapshot history and git status.
# Changed by: Sourish
# Date: 2026-10-22
def test_snapshot_history_and_git_status(tmp_path):
    ws = project(tmp_path, {"orders.py": ORDERS}, git=True)
    ok(ws, "/project import")
    ok(ws, '/snapshot "before refactor"')
    ok(ws, '/block add service "temp"')
    ok(ws, '/snapshot "before refactor" --restore')
    assert "temp" not in ws.board.blocks
    assert ok(ws, "/history orders.py")["commits"][0]["message"] == "start"
    (tmp_path / "orders.py").write_text("changed\n")
    status = ok(ws, "/git status")
    assert {"path": "orders.py", "state": "M", "block": "orders-py"} in status["changed"]
    assert ws.command("/git sync --push")["data"]["status"] == "needs_confirmation"


# Input: tmp_path
# Output: None
# Processing: Check that find focus layout filter.
# Changed by: Sourish
# Date: 2026-10-22
def test_find_focus_layout_filter(tmp_path):
    ws = project(tmp_path)
    for name in ("web", "api", "db"):
        ok(ws, f'/block add service "{name}"')
    ok(ws, "/connect web -> api")
    ok(ws, "/connect api -> db --type database")
    assert [f["block"] for f in ok(ws, "/find ap")["found"]] == ["api"]
    assert ok(ws, "/focus api")["shown"] == ["api", "db", "web"]
    ok(ws, "/layout left-to-right --layer 1")
    xs = [ws.board.block(n).x for n in ("web", "api", "db")]
    assert xs == sorted(xs) and len(set(xs)) == 3
    assert ok(ws, "/filter --arrows api,database")["filter"] == {"blocks": [], "arrows": ["api", "database"]}
    assert fails(ws, "/filter --arrows teleport") == "INVALID_ARGS"


# Input: tmp_path (Path)
# Output: None
# Processing: Check that /board gives the whole picture, one layer, one block, and only what an agent may read.
# Changed by: Sourish
# Date: 2026-10-22
def test_board_read_gives_the_picture(tmp_path):
    ws = project(tmp_path, {"web/checkout.py": '"""Checkout."""\n', "secret/keys.py": "K = 1\n"})
    ok(ws, "/project import")
    ok(ws, '/block add service "payments"')
    ok(ws, "/endpoint add payments POST /charge")
    ok(ws, "/endpoint link checkout.py -> payments:POST /charge")
    whole = ok(ws, "/board")
    assert {b["id"] for b in whole["blocks"]} >= {"payments", "checkout-py", "web", "secret"}
    assert whole["endpoints"] == [{"id": "payments:POST /charge", "status": "built", "callers": ["checkout.py"]}]
    assert {"from": "checkout.py", "to": "payments", "type": "api", "status": "built",
            "endpoint": "payments:POST /charge"} in whole["arrows"]
    assert [b["id"] for b in ok(ws, "/board --layer 1")["blocks"]] == ["payments"]
    assert {b["id"] for b in ok(ws, "/board --block web")["blocks"]} == {"web", "checkout-py"}
    ok(ws, "/agent add bot")
    ok(ws, "/agent grant bot web --mode read", confirm=True)
    seen = {b["id"] for b in ok(ws, "/board", actor="bot")["blocks"]}
    assert "checkout-py" in seen and "keys-py" not in seen
