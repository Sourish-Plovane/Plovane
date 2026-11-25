"""Chat with a whiteboard agent from the prompt panel.

Any agent placed on the board (``/agent add NAME``) can be talked to. A turn
runs a hosted model in a tool loop on a background thread. The model reads the
board and runs slash commands *as that agent*, so every change is held to the
agent's grants exactly as an MCP client acting as it would be: an agent with no
grants can look but not touch, and no agent can grant itself access.

The browser sends a message, then polls for new events until the turn ends:

    POST   /api/v1/whiteboard/agents/<name>/chat   {"message": "..."}
    GET    /api/v1/whiteboard/agents/<name>/chat   ?since=N
    DELETE /api/v1/whiteboard/agents/<name>/chat   forget the conversation

Conversations live in memory and end when the server stops.

Which model answers (checked when a turn starts):

    PLOVANE_LLM_PROVIDER   anthropic | deepseek | openai, as for the rest of the whiteboard
    (unset)                the first of ANTHROPIC_API_KEY, DEEPSEEK_API_KEY, OPENAI_API_KEY that is set
    PLOVANE_AGENT_MODEL    the model id; defaults to claude-opus-5-5, deepseek-chat or gpt-5

Claude goes through the Anthropic SDK (which also reads an ``ant auth login``
profile). DeepSeek and OpenAI take the same chat-completions request, sent as
plain HTTPS like ``ml/serving/llm.py`` does, with their usual key variables.
"""

from __future__ import annotations

import json
import os
import threading
import urllib.error
from typing import Any, Callable

from backend.whiteboard.store import now
from ml.serving.llm import CHAT_COMPLETIONS, KEY_NAMES, _post

DEFAULT_MODELS = {"anthropic": "claude-opus-5-5", "deepseek": "deepseek-chat", "openai": "gpt-5"}
MAX_STEPS = 25  # model calls per turn before the loop gives up
MAX_RESULT_CHARS = 6000  # a tool result longer than this is cut, with a note
MAX_BOARD_BLOCKS = 400

SYSTEM = """You are {agent}, an AI agent placed on a Plovane whiteboard. The whiteboard maps a \
software project as blocks (agents, services, folders, files, functions, steps), arrows between \
them, and API endpoints. People talk to you from the whiteboard's prompt panel.

You act only through your tools. read_board shows the current board and your grants. \
run_command runs one Plovane slash command as you. To draw, always use /sketch with short lines \
separated by ';', for example `/sketch service orders, payments; database postgres; orders -> payments \
"POST /charge"; orders, payments -> postgres`. Sketch lines are: service|database|queue|external NAMES, \
A -> B "label", unlink A -> B, rename A "new name", remove A. It lays the board out itself, reuses \
blocks that already exist and refuses unknown names, so add a block before linking it. find_commands searches the command reference: use it \
before a command you are unsure of instead of guessing its syntax.

Every command you run is checked against your grants. A FORBIDDEN result means you lack access: \
say which path or block you need and that a person can grant it with \
`/agent grant {agent} PATH --mode read-write`. You cannot grant or revoke access yourself. A \
needs_confirmation result means the change waits for a person: tell them what it does; the panel \
gives them a button to review and run it.

Read the board before changing it so you use real block ids. Keep replies short: say what you \
changed, or what you found, in a sentence or two. Ask when a request is ambiguous rather than \
building the wrong thing."""

TOOLS: list[dict[str, Any]] = [
    {
        "name": "read_board",
        "description": "The whiteboard as it is now: blocks (id, kind, name, layer, parent, path), "
                       "arrows, layers, every agent's grants, and open alerts. Call it before "
                       "changing anything, and again after a change to check the result.",
        "input_schema": {"type": "object", "properties": {}, "required": [], "additionalProperties": False},
        "strict": True,
    },
    {
        "name": "run_command",
        "description": "Run one Plovane slash command as this agent and return its result envelope "
                       "{ok, data, error}. One command per call; quote names that contain spaces.",
        "input_schema": {
            "type": "object",
            "properties": {"command": {"type": "string", "description": "A slash command, e.g. /block add service \"auth\""}},
            "required": ["command"],
            "additionalProperties": False,
        },
        "strict": True,
    },
    {
        "name": "find_commands",
        "description": "Search the slash-command reference by words such as 'connect', 'endpoint' "
                       "or 'move block'. Returns matching commands with an example each, and says "
                       "which need a person's confirmation or are for people only.",
        "input_schema": {
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
            "additionalProperties": False,
        },
        "strict": True,
    },
]


class Conversation:
    """One agent's chat: the API message history and the events the panel shows."""

    # Input: agent (str)
    # Output: None
    # Processing: Start an empty conversation with one agent.
    # Changed by: Claude
    # Date: 2026-11-09
    def __init__(self, agent: str) -> None:
        self.agent = agent
        self.messages: list[dict[str, Any]] = []  # append-only: thinking blocks must stay intact
        self.events: list[dict[str, Any]] = []
        self.running = False
        self.provider = ""  # set by the first turn; the message format depends on it


# Input: none
# Output: str
# Processing: The provider for the next turn: PLOVANE_LLM_PROVIDER, else the first key that is set.
# Changed by: Claude
# Date: 2026-11-09
def provider() -> str:
    chosen = os.environ.get("PLOVANE_LLM_PROVIDER", "").strip().lower()
    if chosen in DEFAULT_MODELS:
        return chosen
    for name in ("anthropic", "deepseek", "openai"):
        if os.environ.get(KEY_NAMES[name]):
            return name
    return "anthropic"  # the SDK may still find an `ant auth login` profile


# Input: name (str)
# Output: str
# Processing: The model id for a provider: PLOVANE_AGENT_MODEL, else that provider's default.
# Changed by: Claude
# Date: 2026-11-09
def model_for(name: str) -> str:
    return os.environ.get("PLOVANE_AGENT_MODEL") or DEFAULT_MODELS[name]


# Input: none
# Output: Any
# Processing: An Anthropic client from the SDK's default credential sources.
# Changed by: Claude
# Date: 2026-11-09
def default_client() -> Any:
    import anthropic

    return anthropic.Anthropic()


# Input: reply (dict[str, Any])
# Output: str
# Processing: A tool result as JSON, cut to MAX_RESULT_CHARS with a note.
# Changed by: Claude
# Date: 2026-11-09
def clip(reply: Any) -> str:
    text = json.dumps(reply, default=str)
    if len(text) <= MAX_RESULT_CHARS:
        return text
    return text[:MAX_RESULT_CHARS] + f"... [cut: {len(text) - MAX_RESULT_CHARS} more characters]"


class AgentChat:
    """Every agent conversation for one workspace, sharing the server's board lock."""

    # Input: workspace (Workspace), lock (threading.Lock), client_factory (Callable | None), post (Callable | None)
    # Output: None
    # Processing: Keep the workspace and its lock; tests inject a fake Claude client or HTTP poster.
    # Changed by: Claude
    # Date: 2026-11-09
    def __init__(self, workspace: Any, lock: threading.Lock,
                 client_factory: Callable[[], Any] | None = None,
                 post: Callable[[str, dict, dict], dict] | None = None) -> None:
        self.workspace = workspace
        self.lock = lock
        self.client_factory = client_factory or default_client
        self.post = post or _post
        self.conversations: dict[str, Conversation] = {}
        self.guard = threading.Lock()  # guards conversation state, never held across a model call

    # -- Public API used by the server ------------------------------------------

    # Input: agent (str)
    # Output: str
    # Processing: The person's instructions for this agent, for the end of its system prompt.
    # Changed by: Claude
    # Date: 2026-11-14
    def _instructions(self, agent: str) -> str:
        from backend.whiteboard.ops.agents import instructions_for

        return instructions_for(self.workspace.board, agent)

    # Input: agent (str), since (int)
    # Output: dict[str, Any]
    # Processing: The agent's grants, whether a turn is running, and events from ``since`` on.
    # Changed by: Claude
    # Date: 2026-11-09
    def snapshot(self, agent: str, since: int = 0) -> dict[str, Any]:
        with self.lock:
            self.workspace.sync()
            record = self.workspace.board.agents.get(agent)
            grants = list(record.grants) if record else []
        with self.guard:
            convo = self.conversations.get(agent)
            events = list(convo.events[since:]) if convo else []
            name = (convo and convo.provider) or provider()
            return {"agent": agent, "exists": record is not None, "grants": grants,
                    "provider": name, "model": model_for(name),
                    "running": bool(convo and convo.running), "events": events,
                    "next": len(convo.events) if convo else 0}

    # Input: agent (str), text (str)
    # Output: dict[str, Any]
    # Processing: Record the person's message and start a turn on a background thread.
    # Changed by: Claude
    # Date: 2026-11-09
    def send(self, agent: str, text: str) -> dict[str, Any]:
        with self.lock:
            self.workspace.sync()
            if agent not in self.workspace.board.agents:
                raise LookupError(f"no agent {agent!r}; place one with /agent add {agent}")
        with self.guard:
            convo = self.conversations.setdefault(agent, Conversation(agent))
            if convo.running:
                raise RuntimeError(f"{agent} is still answering the last message")
            since = len(convo.events)
            if convo.provider and convo.provider != provider():
                raise RuntimeError(f"this chat used {convo.provider}; start a new chat to switch to {provider()}")
            convo.provider = provider()
            convo.running = True
            convo.messages.append({"role": "user", "content": text})
            self._event(convo, "user", text=text)
        threading.Thread(target=self._turn, args=(convo,), daemon=True, name=f"agent-chat-{agent}").start()
        return self.snapshot(agent, since)

    # Input: agent (str)
    # Output: None
    # Processing: Forget an agent's conversation, unless a turn is still running.
    # Changed by: Claude
    # Date: 2026-11-09
    def reset(self, agent: str) -> None:
        with self.guard:
            convo = self.conversations.get(agent)
            if convo and convo.running:
                raise RuntimeError(f"{agent} is still answering; wait for it to finish")
            self.conversations.pop(agent, None)

    # -- The turn -----------------------------------------------------------------

    # Input: convo (Conversation), role (str), **fields (Any)
    # Output: None
    # Processing: Append one panel event. The caller holds ``self.guard``.
    # Changed by: Claude
    # Date: 2026-11-09
    def _event(self, convo: Conversation, role: str, **fields: Any) -> None:
        convo.events.append({"id": len(convo.events), "role": role, "at": now(), **fields})

    # Input: convo (Conversation), role (str), **fields (Any)
    # Output: None
    # Processing: Append one panel event from the turn thread.
    # Changed by: Claude
    # Date: 2026-11-09
    def _emit(self, convo: Conversation, role: str, **fields: Any) -> None:
        with self.guard:
            self._event(convo, role, **fields)

    # Input: convo (Conversation)
    # Output: None
    # Processing: Run the tool loop until Claude ends its turn, then mark the turn done.
    # Changed by: Claude
    # Date: 2026-11-09
    def _turn(self, convo: Conversation) -> None:
        try:
            if convo.provider == "anthropic":
                self._loop_claude(convo)
            else:
                self._loop_chat_completions(convo)
        except Exception as exc:  # a bug must still end the turn and tell the person
            self._emit(convo, "error", text=f"The agent stopped: {type(exc).__name__}: {exc}")
        finally:
            with self.guard:
                convo.running = False

    # Input: convo (Conversation)
    # Output: None
    # Processing: Call Claude, run the tools it asks for as the agent, repeat until done.
    # Changed by: Claude
    # Date: 2026-11-09
    def _loop_claude(self, convo: Conversation) -> None:
        import anthropic

        try:
            client = self.client_factory()
        except anthropic.AnthropicError as exc:
            self._emit(convo, "error", text="No model key found. Start the whiteboard server with "
                                            "ANTHROPIC_API_KEY or DEEPSEEK_API_KEY exported in the same "
                                            f"terminal, or run `ant auth login`. ({exc})")
            return
        system = SYSTEM.format(agent=convo.agent) + self._instructions(convo.agent)
        for _ in range(MAX_STEPS):
            try:
                response = client.beta.messages.create(
                    model=model_for("anthropic"),
                    max_tokens=16000,
                    system=system,
                    tools=TOOLS,
                    messages=convo.messages,
                    output_config={"effort": "medium"},
                    cache_control={"type": "ephemeral"},
                    betas=["server-side-fallback-2026-07-01"],
                    fallbacks="default",
                )
            except anthropic.AuthenticationError:
                self._emit(convo, "error", text="Claude rejected the API key. Check ANTHROPIC_API_KEY.")
                return
            except anthropic.RateLimitError:
                self._emit(convo, "error", text="Claude is rate limiting this key. Wait a moment and send again.")
                return
            except anthropic.APIStatusError as exc:
                self._emit(convo, "error", text=f"Claude API error {exc.status_code}: {exc.message}")
                return
            except anthropic.APIConnectionError:
                self._emit(convo, "error", text="Could not reach the Claude API. Check the network connection.")
                return

            if response.stop_reason == "refusal":
                # Not appended: the declined reply has nothing to keep, and the history stays valid.
                self._emit(convo, "error", text="Claude declined this request.")
                return
            with self.guard:
                convo.messages.append({"role": "assistant", "content": response.content})
            for block in response.content:
                if block.type == "text" and block.text.strip():
                    self._emit(convo, "assistant", text=block.text)
            if response.stop_reason == "pause_turn":
                continue
            if response.stop_reason == "max_tokens":
                self._emit(convo, "error", text="The reply hit its length limit and was cut off.")
                return
            calls = [b for b in response.content if b.type == "tool_use"]
            if response.stop_reason != "tool_use" or not calls:
                return
            results = []
            for call in calls:
                content, is_error = self._run_tool(convo, call.name, call.input)
                results.append({"type": "tool_result", "tool_use_id": call.id, "content": content, "is_error": is_error})
            with self.guard:  # every result in one user message, so parallel calls keep working
                convo.messages.append({"role": "user", "content": results})
        self._emit(convo, "error", text=f"Stopped after {MAX_STEPS} steps. Send another message to continue.")

    # Input: convo (Conversation)
    # Output: None
    # Processing: The same loop over an OpenAI-style chat-completions API (DeepSeek, OpenAI).
    # Changed by: Claude
    # Date: 2026-11-09
    def _loop_chat_completions(self, convo: Conversation) -> None:
        name = convo.provider
        key = os.environ.get(KEY_NAMES[name])
        if not key:
            self._emit(convo, "error", text=f"{KEY_NAMES[name]} is not set. Start the whiteboard server with it "
                                            "exported in the same terminal.")
            return
        tools = [{"type": "function", "function": {"name": t["name"], "description": t["description"],
                                                   "parameters": t["input_schema"]}} for t in TOOLS]
        system = {"role": "system", "content": SYSTEM.format(agent=convo.agent) + self._instructions(convo.agent)}
        limit = "max_completion_tokens" if name == "openai" else "max_tokens"
        for _ in range(MAX_STEPS):
            try:
                raw = self.post(CHAT_COMPLETIONS[name], {"authorization": f"Bearer {key}"},
                                {"model": model_for(name), limit: 8000, "tools": tools,
                                 "messages": [system, *convo.messages]})
            except urllib.error.HTTPError as exc:
                # The body names the problem (bad key, no balance, bad model) and never holds the key.
                detail = exc.read().decode(errors="replace")[:300]
                self._emit(convo, "error", text=f"{name} returned HTTP {exc.code}: {detail}")
                return
            except urllib.error.URLError as exc:
                self._emit(convo, "error", text=f"Could not reach {name}: {exc.reason}")
                return
            choice = (raw.get("choices") or [{}])[0]
            message = choice.get("message") or {}
            calls = message.get("tool_calls") or []
            # Kept as sent back, minus reasoning_content, which DeepSeek rejects in later requests.
            kept = {"role": "assistant", "content": message.get("content") or ""}
            if calls:
                kept["tool_calls"] = calls
            with self.guard:
                convo.messages.append(kept)
            if (message.get("content") or "").strip():
                self._emit(convo, "assistant", text=message["content"])
            if choice.get("finish_reason") == "length":
                self._emit(convo, "error", text="The reply hit its length limit and was cut off.")
                return
            if choice.get("finish_reason") == "content_filter":
                self._emit(convo, "error", text=f"{name} declined this request.")
                return
            if not calls:
                return
            results = []
            for call in calls:
                function = call.get("function") or {}
                try:
                    args = json.loads(function.get("arguments") or "{}")
                    content, _ = self._run_tool(convo, function.get("name", ""), args)
                except json.JSONDecodeError:
                    content = "the arguments were not valid JSON; send them again as a JSON object"
                results.append({"role": "tool", "tool_call_id": call.get("id"), "content": content})
            with self.guard:
                convo.messages.extend(results)
        self._emit(convo, "error", text=f"Stopped after {MAX_STEPS} steps. Send another message to continue.")

    # Input: convo (Conversation), name (str), args (Any)
    # Output: tuple[str, bool]
    # Processing: Run one tool call as the agent; return its result text and whether it failed.
    # Changed by: Claude
    # Date: 2026-11-09
    def _run_tool(self, convo: Conversation, name: str, args: Any) -> tuple[str, bool]:
        args = args if isinstance(args, dict) else {}
        is_error = False
        if name == "read_board":
            content = clip(self.board_summary())
        elif name == "find_commands":
            content = clip(find_commands(str(args.get("query", ""))))
        elif name == "run_command" and isinstance(args.get("command"), str):
            command = args["command"].strip()
            with self.lock:
                reply = self.workspace.command(command, actor=convo.agent)
            data = reply.get("data") or {}
            waiting = reply["ok"] and data.get("status") == "needs_confirmation"
            is_error = not reply["ok"]
            error = reply.get("error") or {}
            self._emit(convo, "tool", command=command, ok=reply["ok"] and not waiting,
                       needs_confirmation=waiting,
                       summary=data.get("message") if waiting else (error.get("message") if is_error else None),
                       changed=reply["ok"] and not waiting)
            content = clip(reply)
        else:
            content, is_error = f"unknown tool or missing arguments: {name}", True
        return content, is_error

    # Input: none
    # Output: dict[str, Any]
    # Processing: A compact view of the board for the model, capped at MAX_BOARD_BLOCKS blocks.
    # Changed by: Claude
    # Date: 2026-11-09
    def board_summary(self) -> dict[str, Any]:
        with self.lock:
            self.workspace.sync()
            board = self.workspace.board.to_dict()
        blocks = [{k: b.get(k) for k in ("id", "kind", "name", "layer", "parent", "path", "status") if b.get(k) is not None}
                  for b in board.get("blocks", {}).values()]
        arrows = [{k: a.get(k) for k in ("source", "target", "type", "endpoint", "status") if a.get(k) is not None}
                  for a in board.get("arrows", {}).values()]
        summary = {
            "layers": {n: layer.get("name") for n, layer in board.get("layers", {}).items()},
            "blocks": blocks[:MAX_BOARD_BLOCKS],
            "arrows": arrows,
            "agents": {name: agent.get("grants", []) for name, agent in board.get("agents", {}).items()},
            "alerts": board.get("alerts", []),
        }
        if len(blocks) > MAX_BOARD_BLOCKS:
            summary["note"] = f"showing {MAX_BOARD_BLOCKS} of {len(blocks)} blocks; use /find or /block info for the rest"
        return summary


# Input: query (str)
# Output: dict[str, Any]
# Processing: Commands whose phrase or summary contains every word of the query.
# Changed by: Claude
# Date: 2026-11-09
def find_commands(query: str) -> dict[str, Any]:
    import backend.whiteboard.ops  # noqa: F401  (registers every operation)
    from backend.whiteboard.commands import COMMANDS
    from backend.whiteboard.registry import OPERATIONS

    words = [w for w in query.lower().replace("/", " ").split() if w]
    found = []
    for c in COMMANDS:
        spec = OPERATIONS[c.op]
        text = f"{c.phrase} {spec.summary or ''}".lower()
        if all(w in text for w in words):
            found.append({"command": "/" + c.phrase, "example": c.example, "summary": spec.summary,
                          "needs_confirmation": spec.confirm is True, "people_only": not spec.agents})
    return {"matches": found[:15], "total": len(found)}
