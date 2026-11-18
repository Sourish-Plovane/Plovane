"""Drive the whiteboard with an LLM over its API: Claude, DeepSeek or OpenAI.

This is the API path for an AI to use Plovane, next to the MCP path
(``plovane mcp``). Both end up making the same calls to the same server, so
the agent is held to its grants and every step shows up live on the board.

    from backend.whiteboard.llm_agent import HttpClient, run
    run("Add a payments service with a /charge endpoint", provider="deepseek",
        client=HttpClient("http://127.0.0.1:8700", agent_token))

The model gets three tools:

    run_command(command, confirm=False)   one whiteboard slash command
    get_board()                           the blocks and arrows, compactly
    list_commands(filter="")              slash commands it may use

Providers, all plain HTTPS through urllib so no vendor SDK is needed:

    anthropic   Messages API tool use              ANTHROPIC_API_KEY
    deepseek    chat completions, function calls   DEEPSEEK_API_KEY
    openai      chat completions, function calls   OPENAI_API_KEY

A key comes from the environment, or from ``~/.plovane/keys.json`` which
``plovane connect`` writes. ``PLOVANE_<PROVIDER>_BASE_URL`` points a provider
at any compatible endpoint (a proxy, Azure, a local model server), and
``PLOVANE_<PROVIDER>_MODEL`` picks the model.
"""

from __future__ import annotations

import json
import os
import threading
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Callable

PROVIDERS = ("anthropic", "deepseek", "openai")
ALIASES = {"claude": "anthropic", "anthropic": "anthropic", "deepseek": "deepseek", "openai": "openai",
           "gpt": "openai", "chatgpt": "openai"}
KEY_NAMES = {"anthropic": "ANTHROPIC_API_KEY", "deepseek": "DEEPSEEK_API_KEY", "openai": "OPENAI_API_KEY"}
DEFAULT_MODELS = {"anthropic": "claude-sonnet-5-5", "deepseek": "deepseek-chat", "openai": "gpt-5"}
BASE_URLS = {"anthropic": "https://api.anthropic.com", "deepseek": "https://api.deepseek.com",
             "openai": "https://api.openai.com/v1"}
KEYS_PATH = Path(os.path.expanduser("~")) / ".plovane" / "keys.json"
RESULT_LIMIT = 6000
"""Characters of a tool result the model sees; the board can be large."""

SYSTEM = """You are an AI agent working on a software project through the Plovane whiteboard.
The whiteboard is a live map of the project: blocks (services, databases, folders, files,
functions) on zoom layers, joined by arrows. A person is watching it update as you work.

Work only through the tools. run_command takes one Plovane slash command exactly as a person
would type it, for example:
  /block add service payments
  /file create src/payments/charge.py
  /connect payments -> orders --type api
  /endpoint add payments POST /charge
Use list_commands to see every command with an example before guessing syntax, and get_board
to see what already exists. A destructive command answers status needs_confirmation with a
preview: read it, and only repeat it with confirm=true when the change is what was asked.
If a command fails, read the error and fix the command; do not repeat it unchanged.
When the task is done, reply with a short plain-English summary of what you changed."""

TOOLS = [
    {"name": "run_command",
     "description": "Run one Plovane whiteboard slash command, e.g. '/block add service payments'. "
                    "Returns the server's JSON reply.",
     "schema": {"type": "object", "properties": {
         "command": {"type": "string", "description": "The slash command, starting with /"},
         "confirm": {"type": "boolean", "description": "Apply a command that answered needs_confirmation"}},
         "required": ["command"]}},
    {"name": "get_board",
     "description": "The current blocks (id, kind, name, layer, path, parent) and arrows on the whiteboard.",
     "schema": {"type": "object", "properties": {}}},
    {"name": "list_commands",
     "description": "Slash commands this agent may run, with a one-line summary and an example.",
     "schema": {"type": "object", "properties": {
         "filter": {"type": "string", "description": "Only commands containing this text, e.g. 'block'"}}}},
]


class AgentError(RuntimeError):
    """The provider could not be reached, refused, or is not configured."""


# Input: provider (str)
# Output: str
# Processing: The canonical provider name for an alias like "claude", or AgentError.
# Changed by: Claude Code
# Date: 2026-11-09
def canonical(provider: str) -> str:
    name = ALIASES.get((provider or "").strip().lower())
    if name is None:
        raise AgentError(f"provider must be one of claude, deepseek, openai (got {provider!r})")
    return name


# Input: none
# Output: dict[str, str]
# Processing: Keys saved by ``plovane connect``, by provider.
# Changed by: Claude Code
# Date: 2026-11-09
def saved_keys() -> dict[str, str]:
    try:
        raw = json.loads(KEYS_PATH.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return {}
    return {k: v for k, v in raw.items() if isinstance(v, str) and v} if isinstance(raw, dict) else {}


# Input: none
# Output: None
# Processing: Put saved keys into the environment where none is set, so every caller finds them.
# Changed by: Claude Code
# Date: 2026-11-09
def load_saved_keys() -> None:
    for provider, key in saved_keys().items():
        name = KEY_NAMES.get(provider)
        if name and not os.environ.get(name):
            os.environ[name] = key


# Input: provider (str), key (str)
# Output: str
# Processing: Save a provider's key to ~/.plovane/keys.json (mode 600), use it in this process, and return the provider.
# Changed by: Claude Code
# Date: 2026-11-09
def save_key(provider: str, key: str) -> str:
    from backend.whiteboard.auth import write_private

    provider = canonical(provider)
    keys = saved_keys()
    keys[provider] = key
    write_private(KEYS_PATH, keys)
    os.environ[KEY_NAMES[provider]] = key
    return provider


# Input: provider (str)
# Output: str | None
# Processing: The API key for a provider, from the environment or keys.json.
# Changed by: Claude Code
# Date: 2026-11-09
def api_key(provider: str) -> str | None:
    provider = canonical(provider)
    return os.environ.get(KEY_NAMES[provider]) or saved_keys().get(provider)


# Input: none
# Output: list[dict[str, Any]]
# Processing: Every provider with whether it has a key and the model it would use (never the key).
# Changed by: Claude Code
# Date: 2026-11-09
def providers() -> list[dict[str, Any]]:
    return [{"provider": p, "label": {"anthropic": "Claude", "deepseek": "DeepSeek", "openai": "OpenAI"}[p],
             "configured": bool(api_key(p)), "model": model_for(p), "base_url": base_url(p)}
            for p in PROVIDERS]


# Input: provider (str), model (str | None)
# Output: str
# Processing: The model to call: explicit, then PLOVANE_<P>_MODEL, then the default.
# Changed by: Claude Code
# Date: 2026-11-09
def model_for(provider: str, model: str | None = None) -> str:
    provider = canonical(provider)
    return model or os.environ.get(f"PLOVANE_{provider.upper()}_MODEL") or DEFAULT_MODELS[provider]


# Input: provider (str)
# Output: str
# Processing: The API base URL: PLOVANE_<P>_BASE_URL, or the provider's own.
# Changed by: Claude Code
# Date: 2026-11-09
def base_url(provider: str) -> str:
    provider = canonical(provider)
    return (os.environ.get(f"PLOVANE_{provider.upper()}_BASE_URL") or BASE_URLS[provider]).rstrip("/")


# Input: url (str), headers (dict[str, str]), body (dict[str, Any] | None), method (str), timeout (float)
# Output: dict[str, Any]
# Processing: Send JSON and parse the JSON reply; HTTP errors keep their body for the message.
# Changed by: Claude Code
# Date: 2026-11-09
def http_json(url: str, headers: dict[str, str] | None = None, body: dict[str, Any] | None = None,
              method: str | None = None, timeout: float = 300) -> dict[str, Any]:
    data = None if body is None else json.dumps(body).encode()
    request = urllib.request.Request(url, data=data, method=method or ("POST" if data else "GET"),
                                     headers={"content-type": "application/json", **(headers or {})})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read() or b"{}")
    except urllib.error.HTTPError as exc:
        text = exc.read().decode(errors="replace")
        try:
            return {"_http_status": exc.code, **json.loads(text)}
        except json.JSONDecodeError:
            return {"_http_status": exc.code, "_text": text[:800]}


class HttpClient:
    """Calls a running whiteboard server as one agent (or the person)."""

    # Input: base_url (str), token (str | None)
    # Output: None
    # Processing: Remember where the server is and the bearer token to send.
    # Changed by: Claude Code
    # Date: 2026-11-09
    def __init__(self, base_url: str, token: str | None) -> None:
        self.base = base_url.rstrip("/") + "/api/v1/whiteboard"
        self.headers = {"authorization": f"Bearer {token}"} if token else {}

    # Input: command (str), confirm (bool)
    # Output: dict[str, Any]
    # Processing: POST one slash command and return the envelope.
    # Changed by: Claude Code
    # Date: 2026-11-09
    def run_command(self, command: str, confirm: bool = False) -> dict[str, Any]:
        reply = http_json(f"{self.base}/command", self.headers, {"command": command, "confirm": confirm},
                          timeout=600)
        reply.pop("_http_status", None)
        return reply

    # Input: none
    # Output: dict[str, Any]
    # Processing: GET the board envelope.
    # Changed by: Claude Code
    # Date: 2026-11-09
    def board(self) -> dict[str, Any]:
        return http_json(f"{self.base}/board", self.headers)

    # Input: none
    # Output: dict[str, Any]
    # Processing: GET the command list envelope.
    # Changed by: Claude Code
    # Date: 2026-11-09
    def commands(self) -> dict[str, Any]:
        return http_json(f"{self.base}/commands", self.headers)


class LocalClient:
    """Calls a Workspace in this process, for runs the server starts itself."""

    # Input: workspace (Any), lock (Any), actor (str), board_reply (Callable), commands_reply (Callable)
    # Output: None
    # Processing: Bind the workspace, the server's lock, the agent name and the read-only replies.
    # Changed by: Claude Code
    # Date: 2026-11-09
    def __init__(self, workspace: Any, lock: Any, actor: str,
                 board_reply: Callable[[], dict[str, Any]], commands_reply: Callable[[], dict[str, Any]]) -> None:
        self.workspace, self.lock, self.actor = workspace, lock, actor
        self._board, self._commands = board_reply, commands_reply

    # Input: command (str), confirm (bool)
    # Output: dict[str, Any]
    # Processing: Run one slash command as this agent, holding the server's lock.
    # Changed by: Claude Code
    # Date: 2026-11-09
    def run_command(self, command: str, confirm: bool = False) -> dict[str, Any]:
        with self.lock:
            self.workspace.current_command = command
            try:
                return self.workspace.command(command, actor=self.actor, confirm=confirm)
            finally:
                self.workspace.current_command = None

    # Input: none
    # Output: dict[str, Any]
    # Processing: The board envelope.
    # Changed by: Claude Code
    # Date: 2026-11-09
    def board(self) -> dict[str, Any]:
        return self._board()

    # Input: none
    # Output: dict[str, Any]
    # Processing: The command list envelope.
    # Changed by: Claude Code
    # Date: 2026-11-09
    def commands(self) -> dict[str, Any]:
        return self._commands()


# Input: reply (dict[str, Any])
# Output: dict[str, Any]
# Processing: The blocks and arrows of a board envelope, without styles or coordinates.
# Changed by: Claude Code
# Date: 2026-11-09
def compact_board(reply: dict[str, Any]) -> dict[str, Any]:
    if not reply.get("ok"):
        return reply
    board = (reply.get("data") or {}).get("board") or {}
    blocks = [{k: b.get(k) for k in ("id", "kind", "name", "layer", "path", "parent", "status") if b.get(k) is not None}
              for b in (board.get("blocks") or {}).values()]
    arrows = [{k: a.get(k) for k in ("from", "to", "type") if a.get(k) is not None}
              for a in (board.get("arrows") or {}).values()]
    return {"ok": True, "blocks": blocks, "arrows": arrows,
            "layers": {k: v.get("name") for k, v in (board.get("layers") or {}).items()}}


# Input: client (Any), name (str), args (dict[str, Any])
# Output: str
# Processing: Run one tool call against the whiteboard and return its JSON text, trimmed.
# Changed by: Claude Code
# Date: 2026-11-09
def call_tool(client: Any, name: str, args: dict[str, Any]) -> str:
    if name == "run_command":
        command = args.get("command")
        if not isinstance(command, str) or not command.strip().startswith("/"):
            result: dict[str, Any] = {"ok": False, "error": {"code": "INVALID_ARGS",
                                                             "message": "command must be a slash command"}}
        else:
            result = client.run_command(command.strip(), bool(args.get("confirm")))
    elif name == "get_board":
        result = compact_board(client.board())
    elif name == "list_commands":
        reply = client.commands()
        listing = ((reply.get("data") or {}).get("commands") or []) if reply.get("ok") else []
        needle = str(args.get("filter") or "").lower()
        result = {"ok": True, "commands": [
            {"command": c["command"], "summary": c.get("summary"), "example": c.get("example")}
            for c in listing if not c.get("people_only") and needle in (c["command"] + " " + str(c.get("summary"))).lower()]}
    else:
        result = {"ok": False, "error": {"code": "UNKNOWN_TOOL", "message": f"no tool {name!r}"}}
    text = json.dumps(result, default=str)
    return text if len(text) <= RESULT_LIMIT else text[:RESULT_LIMIT] + '..." (truncated)'


# Input: model (str), messages (list), key (str)
# Output: dict[str, Any]
# Processing: One Anthropic Messages call with the whiteboard tools.
# Changed by: Claude Code
# Date: 2026-11-09
def _anthropic_turn(model: str, messages: list[dict[str, Any]], key: str) -> dict[str, Any]:
    reply = http_json(f"{base_url('anthropic')}/v1/messages",
                      {"x-api-key": key, "anthropic-version": "2023-06-01"},
                      {"model": model, "max_tokens": 4096, "system": SYSTEM, "messages": messages,
                       "tools": [{"name": t["name"], "description": t["description"], "input_schema": t["schema"]}
                                 for t in TOOLS]})
    if "_http_status" in reply or reply.get("type") == "error":
        raise AgentError(f"Claude returned an error: {json.dumps(reply.get('error') or reply)[:500]}")
    return reply


# Input: provider (str), model (str), messages (list), key (str)
# Output: dict[str, Any]
# Processing: One chat-completions call (DeepSeek or OpenAI) with the whiteboard tools as functions.
# Changed by: Claude Code
# Date: 2026-11-09
def _chat_turn(provider: str, model: str, messages: list[dict[str, Any]], key: str) -> dict[str, Any]:
    limit = "max_completion_tokens" if provider == "openai" else "max_tokens"
    reply = http_json(f"{base_url(provider)}/chat/completions", {"authorization": f"Bearer {key}"},
                      {"model": model, limit: 4096, "messages": messages,
                       "tools": [{"type": "function", "function": {
                           "name": t["name"], "description": t["description"], "parameters": t["schema"]}}
                           for t in TOOLS]})
    if "_http_status" in reply or "choices" not in reply:
        raise AgentError(f"{provider} returned an error: {json.dumps(reply.get('error') or reply)[:500]}")
    return reply


# Input: task (str), provider (str), client (Any), model (str | None), max_steps (int), on_step (Callable | None), stop (threading.Event | None)
# Output: dict[str, Any]
# Processing: Let the model call whiteboard tools until it answers in text, reporting each step.
# Changed by: Claude Code
# Date: 2026-11-09
def run(task: str, *, provider: str, client: Any, model: str | None = None, max_steps: int = 30,
        on_step: Callable[[dict[str, Any]], None] | None = None,
        stop: threading.Event | None = None) -> dict[str, Any]:
    provider = canonical(provider)
    key = api_key(provider)
    if not key:
        raise AgentError(f"no API key for {provider}: set {KEY_NAMES[provider]} or run "
                         f"'plovane connect {'claude' if provider == 'anthropic' else provider}'")
    model = model_for(provider, model)
    report = on_step or (lambda _event: None)
    usage = {"input_tokens": 0, "output_tokens": 0}
    commands: list[dict[str, Any]] = []
    if provider == "anthropic":
        messages: list[dict[str, Any]] = [{"role": "user", "content": task}]
    else:
        messages = [{"role": "system", "content": SYSTEM}, {"role": "user", "content": task}]

    for step in range(1, max_steps + 1):
        if stop is not None and stop.is_set():
            return {"status": "stopped", "steps": step - 1, "commands": commands, "usage": usage,
                    "provider": provider, "model": model, "summary": "Stopped by the person."}
        if provider == "anthropic":
            reply = _anthropic_turn(model, messages, key)
            u = reply.get("usage") or {}
            usage["input_tokens"] += int(u.get("input_tokens") or 0)
            usage["output_tokens"] += int(u.get("output_tokens") or 0)
            content = reply.get("content") or []
            text = "".join(b.get("text", "") for b in content if b.get("type") == "text").strip()
            calls = [(b["id"], b["name"], b.get("input") or {}) for b in content if b.get("type") == "tool_use"]
            messages.append({"role": "assistant", "content": content})
        else:
            reply = _chat_turn(provider, model, messages, key)
            u = reply.get("usage") or {}
            usage["input_tokens"] += int(u.get("prompt_tokens") or 0)
            usage["output_tokens"] += int(u.get("completion_tokens") or 0)
            message = reply["choices"][0]["message"]
            text = (message.get("content") or "").strip()
            calls = []
            for call in message.get("tool_calls") or []:
                try:
                    arguments = json.loads(call["function"].get("arguments") or "{}")
                except json.JSONDecodeError:
                    arguments = {}
                calls.append((call["id"], call["function"]["name"], arguments))
            kept = {"role": "assistant", "content": message.get("content") or ""}
            if message.get("tool_calls"):
                kept["tool_calls"] = message["tool_calls"]
            messages.append(kept)

        if text:
            report({"type": "thought", "step": step, "text": text})
        if not calls:
            return {"status": "done", "steps": step, "commands": commands, "usage": usage,
                    "provider": provider, "model": model, "summary": text}

        results = []
        for call_id, name, arguments in calls:
            output = call_tool(client, name, arguments)
            if name == "run_command":
                parsed = json.loads(output) if not output.endswith("(truncated)") else {"ok": True}
                entry = {"command": arguments.get("command"), "ok": bool(parsed.get("ok")),
                         "error": (parsed.get("error") or {}).get("message") if isinstance(parsed.get("error"), dict) else None}
                commands.append(entry)
                report({"type": "command", "step": step, **entry})
            else:
                report({"type": "tool", "step": step, "tool": name})
            results.append((call_id, output))
        if provider == "anthropic":
            messages.append({"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": call_id, "content": output} for call_id, output in results]})
        else:
            messages.extend({"role": "tool", "tool_call_id": call_id, "content": output}
                            for call_id, output in results)

    return {"status": "max_steps", "steps": max_steps, "commands": commands, "usage": usage,
            "provider": provider, "model": model,
            "summary": f"Stopped after {max_steps} steps without finishing."}
