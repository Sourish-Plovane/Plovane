"""Chatting with a whiteboard agent from the prompt panel (agent_chat.py).

A scripted client stands in for Claude, so these run without a key or network.
"""

from __future__ import annotations

import json
import time
from types import SimpleNamespace

import pytest

pytest.importorskip("flask")
pytest.importorskip("anthropic")

from backend.whiteboard.agent_chat import AgentChat
from backend.whiteboard.server import create_app
from whiteboard_helpers import ok, project


# Input: text (str)
# Output: SimpleNamespace
# Processing: A text content block.
# Changed by: Claude
# Date: 2026-11-09
def text(value: str) -> SimpleNamespace:
    return SimpleNamespace(type="text", text=value)


# Input: name (str), args (dict), call_id (str)
# Output: SimpleNamespace
# Processing: A tool_use content block.
# Changed by: Claude
# Date: 2026-11-09
def tool(name: str, args: dict, call_id: str) -> SimpleNamespace:
    return SimpleNamespace(type="tool_use", name=name, input=args, id=call_id)


class ScriptedClaude:
    """Returns one scripted response per call and records every request."""

    # Input: responses (list[tuple[str, list]])
    # Output: None
    # Processing: Keep the (stop_reason, content) pairs to return in order.
    # Changed by: Claude
    # Date: 2026-11-09
    def __init__(self, responses: list[tuple[str, list]]) -> None:
        self.responses = list(responses)
        self.requests: list[dict] = []
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self.create))

    # Input: **request (Any)
    # Output: SimpleNamespace
    # Processing: Record the request and return the next scripted response.
    # Changed by: Claude
    # Date: 2026-11-09
    def create(self, **request) -> SimpleNamespace:
        self.requests.append({**request, "messages": list(request["messages"])})
        stop_reason, content = self.responses.pop(0)
        return SimpleNamespace(stop_reason=stop_reason, content=content)


# Input: monkeypatch
# Output: None
# Processing: Pin the provider to Claude so a key in the developer's shell cannot change the path.
# Changed by: Claude
# Date: 2026-11-09
@pytest.fixture(autouse=True)
def claude_provider(monkeypatch):
    monkeypatch.setenv("PLOVANE_LLM_PROVIDER", "anthropic")
    monkeypatch.delenv("PLOVANE_AGENT_MODEL", raising=False)


# Input: http (FlaskClient), name (str)
# Output: dict
# Processing: Poll the chat until the turn ends and return the final snapshot.
# Changed by: Claude
# Date: 2026-11-09
def finished(http, name: str) -> dict:
    for _ in range(200):
        data = http.get(f"/api/v1/whiteboard/agents/{name}/chat").get_json()["data"]
        if not data["running"]:
            return data
        time.sleep(0.01)
    raise AssertionError("the turn never finished")


# Input: tmp_path
# Output: tuple
# Processing: A project with one agent, a scripted Claude, and an HTTP client over both.
# Changed by: Claude
# Date: 2026-11-09
def setup(tmp_path, responses, grant: str | None = "."):
    ws = project(tmp_path, {"src/app.py": "A = 1\n"})
    ok(ws, "/agent add helper")
    if grant:
        ok(ws, f"/agent grant helper {grant} --mode read-write", confirm=True)
    claude = ScriptedClaude(responses)
    chat = AgentChat(ws, lock=None, client_factory=lambda: claude)  # create_app hands it the board lock
    return ws, claude, create_app(ws, chat).test_client()


# Input: tmp_path
# Output: None
# Processing: A turn reads the board, runs a command as the agent, and replies.
# Changed by: Claude
# Date: 2026-11-09
def test_agent_runs_commands_as_itself_and_replies(tmp_path):
    ws, claude, http = setup(tmp_path, [
        ("tool_use", [text("Looking first."), tool("read_board", {}, "t1")]),
        ("tool_use", [tool("run_command", {"command": '/block add service "payments"'}, "t2")]),
        ("end_turn", [text("Added a payments service.")]),
    ])
    sent = http.post("/api/v1/whiteboard/agents/helper/chat", json={"message": "add payments"}).get_json()
    assert sent["ok"] and sent["data"]["events"][0] == {**sent["data"]["events"][0], "role": "user", "text": "add payments"}
    data = finished(http, "helper")
    roles = [e["role"] for e in data["events"]]
    assert roles == ["user", "assistant", "tool", "assistant"]
    assert data["events"][2]["command"] == '/block add service "payments"' and data["events"][2]["ok"]
    assert "payments" in ws.board.blocks
    assert ws.store.read_log(actor="helper", last=5)[-1]["op"] == "block.add"  # logged as the agent
    # The board summary reached Claude, and tool results went back as one user message.
    first_result = claude.requests[1]["messages"][-1]["content"][0]
    assert first_result["tool_use_id"] == "t1" and '"helper"' in first_result["content"]
    assert claude.requests[0]["model"] and claude.requests[0]["fallbacks"] == "default"


# Input: tmp_path
# Output: None
# Processing: An agent without access gets FORBIDDEN and the board does not change.
# Changed by: Claude
# Date: 2026-11-09
def test_agent_without_grants_is_refused(tmp_path):
    ws, claude, http = setup(tmp_path, [
        ("tool_use", [tool("run_command", {"command": "/file create src/new.py"}, "t1")]),
        ("end_turn", [text("I need write access to src/.")]),
    ], grant=None)
    http.post("/api/v1/whiteboard/agents/helper/chat", json={"message": "make a file"})
    data = finished(http, "helper")
    call = next(e for e in data["events"] if e["role"] == "tool")
    assert not call["ok"] and "no read-write access" in call["summary"]
    assert not (tmp_path / "src" / "new.py").exists()
    assert claude.requests[1]["messages"][-1]["content"][0]["is_error"] is True
    assert data["grants"] == []


# Input: tmp_path
# Output: None
# Processing: An agent cannot grant itself access, even when Claude tries.
# Changed by: Claude
# Date: 2026-11-09
def test_agent_cannot_grant_itself_access(tmp_path):
    ws, _, http = setup(tmp_path, [
        ("tool_use", [tool("run_command", {"command": "/agent grant helper . --mode read-write"}, "t1")]),
        ("end_turn", [text("I can't change my own access.")]),
    ], grant="src")
    http.post("/api/v1/whiteboard/agents/helper/chat", json={"message": "give yourself access"})
    call = next(e for e in finished(http, "helper")["events"] if e["role"] == "tool")
    assert not call["ok"]
    assert ws.board.agents["helper"].grants == [{"path": "src", "mode": "read-write"}]


# Input: tmp_path
# Output: None
# Processing: Unknown agents, empty messages, and refusals are reported, not raised.
# Changed by: Claude
# Date: 2026-11-09
def test_errors_are_reported(tmp_path):
    _, claude, http = setup(tmp_path, [("refusal", [])])
    assert http.post("/api/v1/whiteboard/agents/ghost/chat", json={"message": "hi"}).status_code == 404
    assert http.post("/api/v1/whiteboard/agents/helper/chat", json={"message": "  "}).status_code == 400
    http.post("/api/v1/whiteboard/agents/helper/chat", json={"message": "hi"})
    data = finished(http, "helper")
    assert data["events"][-1]["role"] == "error" and "declined" in data["events"][-1]["text"]
    assert http.delete("/api/v1/whiteboard/agents/helper/chat").get_json()["data"]["cleared"]
    assert http.get("/api/v1/whiteboard/agents/helper/chat").get_json()["data"]["events"] == []


# Input: tmp_path
# Output: None
# Processing: Missing credentials end the turn with a message that says how to fix it.
# Changed by: Claude
# Date: 2026-11-09
def test_missing_credentials_say_how_to_fix(tmp_path):
    import anthropic

    ws = project(tmp_path)
    ok(ws, "/agent add helper")

    # Input: none
    # Output: None
    # Processing: A client factory that fails the way the SDK does without credentials.
    # Changed by: Claude
    # Date: 2026-11-09
    def no_key():
        raise anthropic.AnthropicError("no api key")

    http = create_app(ws, AgentChat(ws, lock=None, client_factory=no_key)).test_client()
    http.post("/api/v1/whiteboard/agents/helper/chat", json={"message": "hi"})
    last = finished(http, "helper")["events"][-1]
    assert last["role"] == "error" and "ANTHROPIC_API_KEY" in last["text"]


# Input: tmp_path, monkeypatch
# Output: None
# Processing: With DeepSeek chosen, the same turn runs over chat-completions with tool calls.
# Changed by: Claude
# Date: 2026-11-09
def test_deepseek_runs_the_same_tools(tmp_path, monkeypatch):
    monkeypatch.setenv("PLOVANE_LLM_PROVIDER", "deepseek")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test")
    ws = project(tmp_path)
    ok(ws, "/agent add helper")
    ok(ws, "/agent grant helper . --mode read-write", confirm=True)
    call = lambda i, n, a: {"id": i, "type": "function", "function": {"name": n, "arguments": a}}  # noqa: E731
    replies = [
        {"choices": [{"finish_reason": "tool_calls", "message": {
            "content": "", "reasoning_content": "private", "tool_calls": [
                call("c1", "read_board", "{}"),
                call("c2", "run_command", '{"command": "/block add service \\"payments\\""}')]}}]},
        {"choices": [{"finish_reason": "stop", "message": {"content": "Added payments."}}]},
    ]
    sent = []

    # Input: url (str), headers (dict), body (dict)
    # Output: dict
    # Processing: Record the request and return the next scripted DeepSeek reply.
    # Changed by: Claude
    # Date: 2026-11-09
    def post(url, headers, body):
        sent.append((url, headers, json.loads(json.dumps(body))))
        return replies.pop(0)

    http = create_app(ws, AgentChat(ws, lock=None, post=post)).test_client()
    http.post("/api/v1/whiteboard/agents/helper/chat", json={"message": "add payments"})
    data = finished(http, "helper")
    assert [e["role"] for e in data["events"]] == ["user", "tool", "assistant"]
    assert data["provider"] == "deepseek" and data["model"] == "deepseek-chat"
    assert "payments" in ws.board.blocks
    url, headers, first = sent[0]
    assert url == "https://api.deepseek.com/chat/completions" and headers["authorization"] == "Bearer sk-test"
    assert first["messages"][0]["role"] == "system" and first["tools"][0]["function"]["name"] == "read_board"
    second = sent[1][2]["messages"]
    assert [m["role"] for m in second] == ["system", "user", "assistant", "tool", "tool"]
    assert "reasoning_content" not in second[2] and second[3]["tool_call_id"] == "c1"
    # Switching provider mid-chat is refused until the chat is cleared.
    monkeypatch.setenv("PLOVANE_LLM_PROVIDER", "anthropic")
    assert http.post("/api/v1/whiteboard/agents/helper/chat", json={"message": "hi"}).status_code == 409
