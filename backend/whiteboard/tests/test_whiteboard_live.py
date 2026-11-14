"""Live whiteboard: tokens, change events, other processes' writes, AI agents.

Covers the path an AI takes to the board, over MCP or the API, and that
what it does reaches an open whiteboard.
"""

from __future__ import annotations

import json
import threading

import pytest

pytest.importorskip("flask")

from werkzeug.serving import make_server

from backend.whiteboard import llm_agent, mcp_tools
from backend.whiteboard.engine import Workspace
from backend.whiteboard.server import create_app, parse_model_json
from whiteboard_helpers import project

PERSON = "plv_person-token-for-tests"


# Input: tmp_path
# Output: tuple
# Processing: A token-protected app over a fresh project, with its workspace.
# Changed by: Claude Code
# Date: 2026-11-09
@pytest.fixture()
def secured(tmp_path):
    ws = project(tmp_path)
    app = create_app(ws, person_token=PERSON)
    return app, app.test_client(), ws


# Input: token (str)
# Output: dict[str, str]
# Processing: The Authorization header for a token.
# Changed by: Claude Code
# Date: 2026-11-09
def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


# Input: secured
# Output: None
# Processing: Check that a request without a token, or with a wrong one, changes nothing.
# Changed by: Claude Code
# Date: 2026-11-09
def test_requests_without_a_token_are_refused(secured, tmp_path):
    _, client, _ = secured
    for headers in ({}, {"X-Plovane-Agent": "user"}, bearer("plv_wrong")):
        reply = client.post("/api/v1/whiteboard/command", json={"command": "/file create a.py"}, headers=headers)
        assert reply.status_code == 401
        assert reply.get_json()["error"]["code"] == "UNAUTHENTICATED"
    assert not (tmp_path / "a.py").exists()
    assert client.get("/healthz").status_code == 200
    ok = client.post("/api/v1/whiteboard/command", json={"command": "/file create a.py"}, headers=bearer(PERSON))
    assert ok.status_code == 200 and (tmp_path / "a.py").exists()


# Input: secured
# Output: None
# Processing: Check that a non-local Host header is refused (DNS rebinding).
# Changed by: Claude Code
# Date: 2026-11-09
def test_non_local_host_is_refused(secured):
    _, client, _ = secured
    reply = client.get("/api/v1/whiteboard/board", headers={**bearer(PERSON), "Host": "evil.example:8700"})
    assert reply.status_code == 403


# Input: secured
# Output: None
# Processing: Check that the served page carries the person token and the API does not leak it.
# Changed by: Claude Code
# Date: 2026-11-09
def test_page_carries_person_token(secured):
    _, client, _ = secured
    html = client.get("/whiteboard").get_data(as_text=True)
    assert f'<meta name="plovane-token" content="{PERSON}">' in html


# Input: secured, tmp_path
# Output: None
# Processing: Check that an agent token acts as that agent, within its grants, and cannot grant itself more.
# Changed by: Claude Code
# Date: 2026-11-09
def test_agent_token_is_held_to_its_grants(secured, tmp_path):
    _, client, ws = secured
    (tmp_path / "src").mkdir()
    (tmp_path / "docs").mkdir()
    issued = client.post("/api/v1/whiteboard/agents/claude/token", json={"access": "read-write", "path": "src"},
                         headers=bearer(PERSON)).get_json()
    assert issued["ok"], issued
    token = issued["data"]["token"]
    assert token.startswith("plv_") and token not in (tmp_path / ".plovane" / "agent_tokens.json").read_text()

    inside = client.post("/api/v1/whiteboard/command", json={"command": "/file create src/pay.py"}, headers=bearer(token))
    assert inside.get_json()["ok"], inside.get_json()
    outside = client.post("/api/v1/whiteboard/command", json={"command": "/file create docs/x.md"}, headers=bearer(token))
    assert outside.status_code == 403
    escalate = client.post("/api/v1/whiteboard/command", json={"command": "/agent grant claude . --mode read-write",
                                                               "confirm": True}, headers=bearer(token))
    assert escalate.status_code == 403
    mint = client.post("/api/v1/whiteboard/agents/other/token", json={}, headers=bearer(token))
    assert mint.status_code == 403
    assert ws.store.read_log(actor="claude", last=5)[-1]["actor"] == "claude"

    assert client.delete("/api/v1/whiteboard/agents/claude/token", headers=bearer(PERSON)).get_json()["ok"]
    assert client.get("/api/v1/whiteboard/board", headers=bearer(token)).status_code == 401


# Input: secured
# Output: None
# Processing: Check that each change becomes an event naming who did it and the command typed.
# Changed by: Claude Code
# Date: 2026-11-09
def test_changes_become_events(secured):
    _, client, _ = secured
    start = client.get("/api/v1/whiteboard/board", headers=bearer(PERSON)).get_json()["data"]["seq"]
    client.post("/api/v1/whiteboard/command", json={"command": "/block add service payments"}, headers=bearer(PERSON))
    client.post("/api/v1/whiteboard/undo", headers=bearer(PERSON))
    events = client.get(f"/api/v1/whiteboard/events/recent?after={start}", headers=bearer(PERSON)).get_json()["data"]["events"]
    assert [e["op"] for e in events] == ["block.add", "history.undo"]
    assert events[0]["command"] == "/block add service payments" and events[0]["actor"] == "user"
    assert events[0]["changes"]["blocks"]["added"] == ["payments"]


# Input: secured
# Output: None
# Processing: Check the SSE stream format: a hello, then one event per change with its id.
# Changed by: Claude Code
# Date: 2026-11-09
def test_event_stream_format(secured):
    app, client, _ = secured
    client.post("/api/v1/whiteboard/command", json={"command": "/block add service a"}, headers=bearer(PERSON))
    bus = app.config["plovane_bus"]
    stop = threading.Event()
    chunks = []
    for chunk in bus.stream(0, stop=stop, timeout=0.01):
        chunks.append(chunk)
        if chunk.startswith("id: "):
            stop.set()
    text = "".join(chunks)
    assert "event: hello" in text and "id: 1\nevent: change\ndata: " in text
    payload = json.loads(text.split("event: change\ndata: ")[1].split("\n")[0])
    assert payload["op"] == "block.add"


# Input: secured, tmp_path
# Output: None
# Processing: Check that a change another process saves is picked up, announced, and not overwritten.
# Changed by: Claude Code
# Date: 2026-11-09
def test_other_process_changes_are_picked_up_not_lost(secured, tmp_path):
    _, client, ws = secured
    other = Workspace(tmp_path)  # what an MCP server without a running whiteboard opens
    other.execute("block.add", {"kind": "service", "name": "from-mcp"})
    board = client.get("/api/v1/whiteboard/board", headers=bearer(PERSON)).get_json()["data"]["board"]
    assert "from-mcp" in board["blocks"]
    client.post("/api/v1/whiteboard/command", json={"command": "/block add service from-ui"}, headers=bearer(PERSON))
    saved = Workspace(tmp_path).board.blocks
    assert {"from-mcp", "from-ui"} <= set(saved)
    kinds = [e["kind"] for e in client.get("/api/v1/whiteboard/events/recent?after=0",
                                           headers=bearer(PERSON)).get_json()["data"]["events"]]
    assert "external" in kinds


# Input: none
# Output: None
# Processing: Check that a fenced JSON reply from a model still parses.
# Changed by: Claude Code
# Date: 2026-11-09
def test_parse_model_json_accepts_fences():
    assert parse_model_json('```json\n{"commands": ["/x"]}\n```') == {"commands": ["/x"]}
    assert parse_model_json('{"a": 1}') == {"a": 1}


# Input: provider (str)
# Output: Callable
# Processing: A fake provider API that adds one block, then finishes.
# Changed by: Claude Code
# Date: 2026-11-09
def fake_provider(provider: str):
    calls = []

    # Input: url (str), headers (dict), body (dict), method (str | None), timeout (float)
    # Output: dict[str, Any]
    # Processing: Answer like Claude or a chat-completions API would.
    # Changed by: Claude Code
    # Date: 2026-11-09
    def answer(url, headers=None, body=None, method=None, timeout=300):
        calls.append({"url": url, "body": body})
        first = len(calls) == 1
        if provider == "anthropic":
            if first:
                return {"content": [{"type": "text", "text": "Adding it."},
                                    {"type": "tool_use", "id": "t1", "name": "run_command",
                                     "input": {"command": "/block add service payments"}}],
                        "usage": {"input_tokens": 10, "output_tokens": 5}}
            return {"content": [{"type": "text", "text": "Added a payments service."}], "usage": {}}
        if first:
            return {"choices": [{"message": {"content": "", "tool_calls": [{
                "id": "c1", "type": "function",
                "function": {"name": "run_command", "arguments": json.dumps({"command": "/block add service payments"})}}]}}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 5}}
        return {"choices": [{"message": {"content": "Added a payments service."}}], "usage": {}}

    return answer, calls


# Input: secured, monkeypatch, provider (str)
# Output: None
# Processing: Check that a run needs an agent added in the CLI, then works on the board as that agent, live.
# Changed by: Claude Code
# Date: 2026-11-09
@pytest.mark.parametrize("provider", ["anthropic", "deepseek"])
def test_agent_run_builds_on_the_board(secured, monkeypatch, provider):
    app, client, ws = secured
    answer, calls = fake_provider(provider)
    monkeypatch.setattr(llm_agent, "http_json", answer)
    monkeypatch.setenv(llm_agent.KEY_NAMES[provider], "sk-test-key")
    agent = "claude" if provider == "anthropic" else "deepseek"
    missing = client.post("/api/v1/whiteboard/agent/run", json={"prompt": "add payments", "provider": provider},
                          headers=bearer(PERSON))
    assert missing.status_code == 404 and "plovane agent add" in missing.get_json()["error"]["message"]
    ws.execute("agent.add", {"name": agent})  # what 'plovane agent add' does
    started = client.post("/api/v1/whiteboard/agent/run", json={"prompt": "add payments", "provider": provider},
                          headers=bearer(PERSON)).get_json()
    assert started["ok"], started
    run_id = started["data"]["id"]
    for _ in range(200):
        status = client.get(f"/api/v1/whiteboard/agent/runs/{run_id}", headers=bearer(PERSON)).get_json()["data"]
        if status["status"] != "running":
            break
        threading.Event().wait(0.02)
    assert status["status"] == "done", status
    assert status["result"]["summary"] == "Added a payments service."
    assert "payments" in ws.board.blocks
    assert ws.store.read_log(actor=agent, last=1)[-1]["op"] == "block.add"
    events = client.get("/api/v1/whiteboard/events/recent?after=0", headers=bearer(PERSON)).get_json()["data"]["events"]
    change = next(e for e in events if e.get("op") == "block.add")
    assert change["actor"] == agent and change["command"] == "/block add service payments"
    assert any(e.get("type") == "finished" for e in events)
    tools = calls[0]["body"]["tools"]
    assert {t.get("name") or t["function"]["name"] for t in tools} == {"run_command", "get_board", "list_commands"}


# Input: secured, monkeypatch
# Output: None
# Processing: Check that a run without a key is refused with a message saying how to add one.
# Changed by: Claude Code
# Date: 2026-11-09
def test_agent_run_needs_a_key(secured, monkeypatch, tmp_path):
    _, client, _ = secured
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.setattr(llm_agent, "KEYS_PATH", tmp_path / "keys.json")
    reply = client.post("/api/v1/whiteboard/agent/run", json={"prompt": "x", "provider": "deepseek"},
                        headers=bearer(PERSON))
    assert reply.status_code == 503 and "plovane connect deepseek" in reply.get_json()["error"]["message"]
    providers = client.get("/api/v1/whiteboard/providers", headers=bearer(PERSON)).get_json()["data"]["providers"]
    assert {p["provider"] for p in providers} == {"anthropic", "deepseek", "openai"}
    saved = client.post("/api/v1/whiteboard/providers/deepseek/key", json={"key": "sk-abcdefgh"}, headers=bearer(PERSON))
    assert saved.get_json()["ok"] and oct((tmp_path / "keys.json").stat().st_mode)[-3:] == "600"


# Input: tmp_path, monkeypatch
# Output: None
# Processing: Check that MCP tools go to a running server with the agent token, so the change is announced live.
# Changed by: Claude Code
# Date: 2026-11-09
def test_mcp_tools_forward_to_running_server(tmp_path, monkeypatch):
    pytest.importorskip("mcp")
    import asyncio

    from backend.mcp_server.server import mcp

    ws = project(tmp_path)
    app = create_app(ws, person_token=PERSON)
    server = make_server("127.0.0.1", 0, app, threaded=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        url = f"http://127.0.0.1:{server.server_port}"
        token = app.test_client().post("/api/v1/whiteboard/agents/claude/token", json={"access": "read-write"},
                                       headers=bearer(PERSON)).get_json()["data"]["token"]
        monkeypatch.setenv("PLOVANE_URL", url)
        monkeypatch.setenv("PLOVANE_AGENT_TOKEN", token)
        mcp_tools.set_workspace(None)
        asyncio.run(mcp.call_tool("plovane_command", {"command": "/block add service orders"}))
        assert "orders" in ws.board.blocks
        events = app.config["plovane_bus"].since(0)
        change = next(e for e in events if e.get("op") == "block.add")
        assert change["actor"] == "claude" and change["command"] == "/block add service orders"
    finally:
        server.shutdown()
