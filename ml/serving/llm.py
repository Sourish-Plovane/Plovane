"""Calls to hosted LLMs (Claude, OpenAI), for the ``ask_llm`` tool.

Plain HTTPS through ``urllib`` so neither vendor SDK is a dependency. Keys are
read from the environment by name and never echoed: ``ANTHROPIC_API_KEY`` and
``OPENAI_API_KEY``.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from typing import Any

DEFAULT_MODELS = {"anthropic": "claude-sonnet-5-5", "openai": "gpt-5"}
KEY_NAMES = {"anthropic": "ANTHROPIC_API_KEY", "openai": "OPENAI_API_KEY"}


# Input: none
# Output: dict[str, bool]
# Processing: Which providers have a key set, by name only.
# Changed by: Claude Code
# Date: 2026-10-18
def configured() -> dict[str, bool]:
    return {provider: bool(os.environ.get(name)) for provider, name in KEY_NAMES.items()}


# Input: url (str), headers (dict), body (dict)
# Output: dict[str, Any]
# Processing: POST JSON, return JSON.
# Changed by: Claude Code
# Date: 2026-10-18
def _post(url: str, headers: dict[str, str], body: dict[str, Any]) -> dict[str, Any]:
    request = urllib.request.Request(
        url, data=json.dumps(body).encode(), method="POST",
        headers={"content-type": "application/json", **headers},
    )
    with urllib.request.urlopen(request, timeout=300) as response:
        return json.loads(response.read())


# Input: prompt (str), provider (str), model (str | None), system (str | None), max_tokens (int)
# Output: dict[str, Any]
# Processing: Send one prompt to a hosted model and return its text.
# Changed by: Claude Code
# Date: 2026-10-18
def ask(
    prompt: str, *, provider: str = "anthropic", model: str | None = None,
    system: str | None = None, max_tokens: int = 1024,
) -> dict[str, Any]:
    if provider not in KEY_NAMES:
        return {"error": f"provider must be one of {sorted(KEY_NAMES)}"}
    key = os.environ.get(KEY_NAMES[provider])
    if not key:
        return {"error": f"{KEY_NAMES[provider]} is not set in the MCP server's environment"}
    model = model or os.environ.get(f"PLOVANE_{provider.upper()}_MODEL") or DEFAULT_MODELS[provider]

    try:
        if provider == "anthropic":
            body: dict[str, Any] = {
                "model": model, "max_tokens": max_tokens,
                "messages": [{"role": "user", "content": prompt}],
            }
            if system:
                body["system"] = system
            raw = _post("https://api.anthropic.com/v1/messages",
                        {"x-api-key": key, "anthropic-version": "2023-06-01"}, body)
            text = "".join(b.get("text", "") for b in raw.get("content", []) if b.get("type") == "text")
            usage = raw.get("usage", {})
        else:
            messages = ([{"role": "system", "content": system}] if system else []) + [
                {"role": "user", "content": prompt}]
            raw = _post("https://api.openai.com/v1/chat/completions",
                        {"authorization": f"Bearer {key}"},
                        {"model": model, "max_completion_tokens": max_tokens, "messages": messages})
            text = raw["choices"][0]["message"].get("content") or ""
            usage = raw.get("usage", {})
    except urllib.error.HTTPError as exc:
        # The body names the problem (bad model, quota) and never holds the key.
        detail = exc.read().decode(errors="replace")[:500]
        return {"error": f"{provider} returned HTTP {exc.code}", "detail": detail}
    except urllib.error.URLError as exc:
        return {"error": f"could not reach {provider}: {exc.reason}"}
    return {"provider": provider, "model": model, "text": text, "usage": usage}
