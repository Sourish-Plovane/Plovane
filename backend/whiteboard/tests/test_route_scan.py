"""Routes found in real code (ops/routes.py), and what a first /project import now adds."""

from __future__ import annotations

from backend.whiteboard.ops import live
from whiteboard_helpers import ScriptedLLM, ok, project

FLASK = {
    "app/config.py": 'FORUM_URL_PREFIX = "/forum"\n',
    "app/forum/views.py": (
        "from flask import Blueprint\n\n"
        'forum = Blueprint("forum", __name__)\n\n\n'
        '@forum.route("/topic/<int:topic_id>", methods=["GET", "POST"])\n'
        "def topic(topic_id):\n    return str(topic_id)\n\n\n"
        "def setup(app):\n"
        '    register_view(forum, routes=["/category/<int:cid>-<slug>"], view_func=None)\n'
        '    forum.add_url_rule("/memberlist", view_func=None)\n'
        '    app.register_blueprint(forum, url_prefix=app.config["FORUM_URL_PREFIX"])\n'),
    "app/helpers.py": "def plain(x):\n    return {'a': 1}.get(x)\n",
    "tests/test_topic.py": "def test_topic(client):\n    client.get('/forum/topic/5')\n",
}

NEXT = {
    "app/api/posts/route.ts": "export async function GET() {}\nexport async function POST() {}\n",
    "app/api/posts/[postId]/route.ts": "export async function DELETE() {}\n",
    "app/(auth)/login/page.tsx": "export default function Login() { return null }\n",
    "components/delete-post.tsx": "await fetch(`/api/posts/${post.id}`, { method: 'DELETE' })\n",
    "components/new-post.tsx": "await fetch('/api/posts', { method: 'POST' })\n",
    "server/index.js": "app.get('/health', (req, res) => res.send('ok'))\naxios.get('/health')\n",
}


# Input: tmp_path
# Output: None
# Processing: Flask routes with a config-driven blueprint prefix, a routes= helper and add_url_rule are found; a test client call gets an API arrow.
# Changed by: Claude Code
# Date: 2026-11-21
def test_flask_routes_and_callers_are_found(tmp_path):
    ws = project(tmp_path, FLASK)
    ok(ws, "/project import")
    paths = {(e.method, e.path) for e in ws.board.endpoints.values()}
    assert {("GET", "/forum/topic/<int:topic_id>"), ("POST", "/forum/topic/<int:topic_id>"),
            ("ANY", "/forum/category/<int:cid>-<slug>"), ("ANY", "/forum/memberlist")} <= paths
    handler = ws.board.block_for_path("app/forum/views.py").id
    caller = ws.board.block_for_path("tests/test_topic.py").id
    arrows = [a for a in ws.board.arrows.values() if a.type == "api"]
    assert any(a.source == caller and a.target == handler for a in arrows)
    # traffic to a real URL now matches, so the arrow can go red
    ok(ws, "/traffic GET /forum/topic/5 500")
    snap = live.snapshot(ws)
    assert snap["api"][f"{handler}:GET /forum/topic/<int:topic_id>"]["ok"] is False
    assert live.match_endpoint(ws.board, "GET", "/forum/category/3-general") is not None


# Input: tmp_path
# Output: None
# Processing: Next.js app-router handlers (route groups dropped), Express routes, and fetch/axios callers are linked.
# Changed by: Claude Code
# Date: 2026-11-21
def test_next_and_express_routes_and_callers_are_found(tmp_path):
    ws = project(tmp_path, NEXT)
    early = ws.command("/endpoint scan")  # before an import there are no file blocks to put routes on
    assert "import the project first" in " ".join(early["data"].get("notes", []))
    ok(ws, "/project import")
    reply = ok(ws, "/endpoint scan")
    paths = {(e.method, e.path) for e in ws.board.endpoints.values()}
    assert {("GET", "/api/posts"), ("POST", "/api/posts"), ("DELETE", "/api/posts/[postId]"),
            ("GET", "/health")} <= paths
    links = {(ws.board.blocks[a.source].path, a.endpoint.split(":", 1)[1])
             for a in ws.board.arrows.values() if a.type == "api"}
    assert ("components/delete-post.tsx", "DELETE /api/posts/[postId]") in links
    assert ("components/new-post.tsx", "POST /api/posts") in links
    assert reply["arrows"] >= 2
    again = ok(ws, "/endpoint scan")  # scanning twice does not duplicate arrows
    assert len([a for a in ws.board.arrows.values() if a.type == "api"]) == again["arrows"] == reply["arrows"]


# Input: tmp_path
# Output: None
# Processing: A first import adds free summaries and routes in one undo step; with a key, the AI upgrades the free lines.
# Changed by: Claude Code
# Date: 2026-11-21
def test_first_import_adds_summaries_and_routes_in_one_step(tmp_path):
    llm = ScriptedLLM('{"_project": "A forum", "app/forum/views.py": "Shows forum topics"}')
    ws = project(tmp_path, FLASK, llm=llm)
    result = ok(ws, "/project import")
    assert result["summarised"] >= 3 and result["routes_found"] >= 4
    views = ws.board.block_for_path("app/forum/views.py")
    assert views.meta["summary_by"] == "parser" and views.meta["summary"]
    upgraded = ok(ws, "/summary --provider deepseek")
    assert upgraded["files"]["app/forum/views.py"] == "Shows forum topics"
    assert upgraded["about"] == "A forum"
    ok(ws, "/undo")  # undoes the AI upgrade
    ok(ws, "/undo")  # undoes the whole import: blocks, summaries and routes together
    assert not ws.board.endpoints and ws.board.block_for_path("app/forum/views.py") is None
