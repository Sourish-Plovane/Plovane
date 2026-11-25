"""Ask an LLM to propose a new overlay from what Plovane already knows.

``/overlay propose "<goal>"`` sends a compact board plus the data signals
Plovane has gathered (log_errors.json from ``/logs scan``, trace.json from
``/trace run``, outside_apis.json, security scan results) to a lightweight
LLM. The model returns a strict JSON spec: a label, a channel, a style, and
notes keyed by block id. Each note becomes one rule (``if id is X, show
this``) and the overlay is saved with ``/overlay rule``, so switching it on
costs no further tokens and it travels in the .plo file like any overlay.

No code is changed, no tools are called. The model is asked for one JSON
object. Any extra prose or a missing field fails validation and the person
sees the raw reply so they can rephrase the goal.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

from backend.whiteboard.engine import Context
from backend.whiteboard.registry import OpError, operation
from backend.whiteboard.store import STATE_DIR
from backend.whiteboard.llm_agent import (
    AgentError, api_key, base_url, canonical, http_json, model_for, saved_keys,
)

DEFAULT_MODELS = {"anthropic": "claude-haiku-5-5", "deepseek": "deepseek-chat", "openai": "gpt-5-mini"}
CHANNELS = ("badge", "chip", "icon", "border", "fill")
APPEARANCES = ("hidden", "count", "mark", "detail")
MAX_BLOCKS_IN_CONTEXT = 120
MAX_ITEMS_PER_BLOCK = 3

SYSTEM = """You propose one overlay for the Plovane whiteboard.

An overlay highlights blocks (services, files, functions) with short notes.
You are given the board (a list of blocks with id, kind, name and path) and
the signals Plovane has already gathered: log errors, test coverage,
outside-API detection, risky-code scans.

Pick blocks from the list. Do not invent block ids. Reply with ONE JSON
object and nothing else, matching:

{
  "label": "a short title shown in the overlays panel",
  "channel": "badge | chip | icon | border | fill",
  "priority": 50,
  "style": {"color": "red|orange|blue|green|purple", "badge": "short word"},
  "reasoning": "one or two sentences on what the overlay shows",
  "items": {
    "<block_id>": [{"message": "short note", "count": 1, "line": null}]
  }
}

Return at most one item per block. Keep each message under 100 characters.
If no signal fits the goal, reply {"items": {}, "reasoning": "nothing found"}."""


# Input: ctx (Context)
# Output: list[dict[str, Any]]
# Processing: A compact block list for the LLM: id, kind, name, path, parent, layer only.
# Changed by: Claude Code
# Date: 2026-11-25
def compact_blocks(ctx: Context) -> list[dict[str, Any]]:
    board = ctx.board
    rows: list[dict[str, Any]] = []
    for block in list(board.blocks.values())[:MAX_BLOCKS_IN_CONTEXT]:
        rows.append({k: v for k, v in {
            "id": block.id, "kind": block.kind, "name": block.name,
            "path": block.path, "parent": block.parent, "layer": block.layer,
        }.items() if v is not None})
    return rows


# Input: root (Path)
# Output: dict[str, Any]
# Processing: Read every signal file Plovane has saved, trimmed, so the model has data to work from.
# Changed by: Claude Code
# Date: 2026-11-25
def gather_signals(root: Path) -> dict[str, Any]:
    state = root / STATE_DIR
    signals: dict[str, Any] = {}
    for name, filename in (("log_errors", "log_errors.json"), ("trace", "trace.json"),
                           ("outside_apis", "outside_apis.json"), ("scan", "scan.json")):
        path = state / filename
        if not path.is_file():
            continue
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if name == "log_errors":
            signals[name] = {rel: {"count": e.get("count"), "worst_line": next(iter(e.get("lines") or {}), None)}
                             for rel, e in (record.get("files") or {}).items()}
        elif name == "trace":
            signals[name] = {rel: {"ran": len(e.get("ran") or []), "missed": len(e.get("missed") or [])}
                             for rel, e in (record.get("files") or {}).items()}
        else:
            signals[name] = record
    return signals


# Input: ctx (Context), provider (str | None)
# Output: tuple[str, str, str]
# Processing: Pick the lightweight provider, the model and the API key for this call.
# Changed by: Claude Code
# Date: 2026-11-25
def pick_provider(ctx: Context, provider: str | None) -> tuple[str, str, str]:
    if provider:
        name = canonical(provider)
    else:
        for candidate in ("anthropic", "deepseek", "openai"):
            if api_key(candidate):
                name = candidate
                break
        else:
            keys = sorted(saved_keys())
            raise OpError("NEEDS_CONFIRMATION",
                          "no LLM configured for /overlay propose; run 'plovane connect claude' "
                          f"(or deepseek/openai). Saved: {keys or 'none'}.")
    key = api_key(name)
    if not key:
        raise OpError("NEEDS_CONFIRMATION", f"no API key for {name!r}; run 'plovane connect {name}'")
    model = os.environ.get(f"PLOVANE_{name.upper()}_MODEL_OVERLAY") or model_for(name, DEFAULT_MODELS[name])
    return name, model, key


# Input: provider (str), model (str), key (str), prompt (str)
# Output: str
# Processing: Ask one lightweight model for a JSON reply; no tools, no streaming, one round-trip.
# Changed by: Claude Code
# Date: 2026-11-25
def ask_for_json(provider: str, model: str, key: str, prompt: str) -> str:
    if provider == "anthropic":
        reply = http_json(f"{base_url('anthropic')}/v1/messages",
                          {"x-api-key": key, "anthropic-version": "2023-06-01"},
                          {"model": model, "max_tokens": 2048, "system": SYSTEM,
                           "messages": [{"role": "user", "content": prompt}]})
        if "_http_status" in reply or reply.get("type") == "error":
            raise AgentError(f"Claude returned an error: {json.dumps(reply.get('error') or reply)[:400]}")
        return "".join(b.get("text", "") for b in (reply.get("content") or []) if b.get("type") == "text")
    body: dict[str, Any] = {"model": model, "messages": [{"role": "system", "content": SYSTEM},
                                                         {"role": "user", "content": prompt}],
                            "response_format": {"type": "json_object"}}
    body["max_completion_tokens" if provider == "openai" else "max_tokens"] = 2048
    reply = http_json(f"{base_url(provider)}/chat/completions", {"authorization": f"Bearer {key}"}, body)
    if "_http_status" in reply or "choices" not in reply:
        raise AgentError(f"{provider} returned an error: {json.dumps(reply.get('error') or reply)[:400]}")
    return reply["choices"][0]["message"].get("content") or ""


# Input: text (str)
# Output: dict[str, Any]
# Processing: Strip code fences, parse JSON, or raise so the person sees the raw reply.
# Changed by: Claude Code
# Date: 2026-11-25
def parse_json(text: str) -> dict[str, Any]:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", cleaned, flags=re.MULTILINE).strip()
    match = re.search(r"\{.*\}", cleaned, re.DOTALL)
    if not match:
        raise OpError("INVALID_ARGS", f"model did not return JSON. Reply started: {text[:200]!r}")
    try:
        data = json.loads(match.group(0))
    except json.JSONDecodeError as exc:
        raise OpError("INVALID_ARGS", f"model returned bad JSON ({exc.msg}). Reply started: {text[:200]!r}") from None
    if not isinstance(data, dict):
        raise OpError("INVALID_ARGS", "model's JSON must be an object")
    return data


# Input: data (dict), board (Any)
# Output: dict[str, Any]
# Processing: Keep only known fields, drop items whose block id does not exist, trim notes to 200 chars.
# Changed by: Claude Code
# Date: 2026-11-25
def sanitize(data: dict[str, Any], board: Any) -> dict[str, Any]:
    channel = data.get("channel") if data.get("channel") in CHANNELS else "badge"
    style = data.get("style") if isinstance(data.get("style"), dict) else {}
    items_in = data.get("items") if isinstance(data.get("items"), dict) else {}
    items: dict[str, list[dict[str, Any]]] = {}
    for block_id, notes in items_in.items():
        if block_id not in board.blocks or not isinstance(notes, list):
            continue
        kept: list[dict[str, Any]] = []
        for note in notes[:MAX_ITEMS_PER_BLOCK]:
            if not isinstance(note, dict):
                continue
            message = str(note.get("message") or "").strip()[:200]
            if not message:
                continue
            entry: dict[str, Any] = {"message": message}
            if isinstance(note.get("count"), (int, float)):
                entry["count"] = int(note["count"])
            if isinstance(note.get("line"), int):
                entry["line"] = note["line"]
            kept.append(entry)
        if kept:
            items[block_id] = kept
    priority = int(data["priority"]) if isinstance(data.get("priority"), (int, float)) else 50
    label = str(data.get("label") or "").strip() or "Proposed overlay"
    return {"label": label[:80], "channel": channel, "priority": max(1, min(priority, 100)),
            "style": {k: str(v)[:40] for k, v in style.items() if isinstance(k, str)},
            "reasoning": str(data.get("reasoning") or "").strip()[:400], "items": items}


# Input: name (str)
# Output: str
# Processing: Turn a free-form overlay name into the lowercase id Plovane allows (letters, digits, underscores).
# Changed by: Claude Code
# Date: 2026-11-25
def slugify(name: str) -> str:
    out = re.sub(r"[^a-z0-9_]+", "_", name.lower()).strip("_")
    if not out:
        out = "proposed"
    if out[0].isdigit():
        out = "o_" + out
    return out[:40]


# Input: ctx (Context), goal (str), provider (str | None)
# Output: dict[str, Any]
# Processing: Build a prompt from the board and signals, ask the LLM for a JSON overlay spec, and save it as a rule overlay.
# Changed by: Claude Code
# Date: 2026-11-25
@operation("overlay.propose", command="/overlay propose", tool="plovane_overlay_propose",
           group="plovane_overlay", agents=False)
def propose(ctx: Context, goal: str, provider: str | None = None) -> dict[str, Any]:
    """Asks a lightweight LLM to propose an overlay for the whiteboard; people only."""
    goal_text = str(goal or "").strip()
    if not goal_text:
        raise OpError("INVALID_ARGS", "give a short goal, e.g. /overlay propose \"files hit hardest by errors\"")
    ctx.require(None, "read-write")

    provider_name, model, key = pick_provider(ctx, provider)
    blocks = compact_blocks(ctx)
    signals = gather_signals(ctx.root)
    prompt = json.dumps({"goal": goal_text, "blocks": blocks, "signals": signals}, default=str)[:60_000]
    try:
        reply = ask_for_json(provider_name, model, key, prompt)
    except AgentError as exc:
        raise OpError("UPSTREAM", str(exc)) from None

    parsed = parse_json(reply)
    clean = sanitize(parsed, ctx.board)
    if not clean["items"]:
        return {"ok": True, "overlay": None, "provider": provider_name, "model": model,
                "reasoning": clean["reasoning"] or "model found nothing to show",
                "raw": reply[:500]}

    base = slugify(parsed.get("name") or clean["label"])
    oid = base
    counter = 2
    while oid in ctx.board.overlays:
        oid = f"{base}_{counter}"
        counter += 1

    from backend.whiteboard.overlay_rules import MAX_RULES

    color = clean["style"].get("color") if clean["style"].get("color") in ("red", "orange", "blue", "green", "purple", "gray") else None
    rules = [{"on": "block", "if": {"id": bid},
              "show": {"message": notes[0]["message"], **({"color": color} if color else {}),
                       **({"badge": clean["style"]["badge"]} if clean["style"].get("badge") else {})}}
             for bid, notes in list(clean["items"].items())[:MAX_RULES]]
    data = {"label": clean["label"], "channel": clean["channel"], "priority": clean["priority"],
            "style": clean["style"], "rules": rules}
    ctx.execute("overlay.rule", {"overlay": oid, "data": data, "show": False})
    spec = ctx.board.overlays[oid]

    ctx.notes.append(f"proposed overlay {oid!r}: {clean['reasoning']} "
                     f"Switch it on with /overlay on {oid}.")
    return {"ok": True, "overlay": oid, "spec": spec, "provider": provider_name, "model": model,
            "reasoning": clean["reasoning"],
            "items": {r["if"]["id"]: 1 for r in rules}}
