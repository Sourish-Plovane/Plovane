"""Shared set-up for the whiteboard tests: a temporary project, a small
library on the placeholder encoder, and a scripted LLM."""

from __future__ import annotations

import subprocess
from pathlib import Path

from ml.retrieval.embedding import HashingEmbeddingModel
from ml.schemas import Entry, EntryType
from ml.serving.engine import Engine
from whiteboard.engine import Workspace

RETRY_PY = '''def retry_request(call, times=3):
    """Call a function, retrying when it raises."""
    for attempt in range(times):
        try:
            return call()
        except Exception:
            if attempt == times - 1:
                raise
'''

SLUG_PY = '''def slugify(text):
    """Turn a title into a URL-safe slug."""
    import re
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
'''


# Input: name (str), code (str), description (str), category (str)
# Output: Entry
# Processing: A Python library entry for the test library.
# Changed by: Sourish
# Date: 2026-10-22
def entry(name: str, code: str, description: str, category: str = "networking") -> Entry:
    return Entry(id=f"demo:{name}", type=EntryType.HELPER, name=name, description=description,
                 category=category, language="python", signature="(call, times=3)", code=code,
                 docstring=description, license="MIT", source_url="https://example.com/demo")


# Input: none
# Output: Engine
# Processing: A two-entry library on the hashing placeholder encoder.
# Changed by: Sourish
# Date: 2026-10-22
def library() -> Engine:
    model = HashingEmbeddingModel()
    entries = [entry("retry_request", RETRY_PY, "Call a function, retrying when it raises"),
               entry("slugify", SLUG_PY, "Turn a title into a URL-safe slug", category="text")]
    for e, vector in zip(entries, model.encode_entries(entries)):
        e.embedding = vector
    return Engine(entries=entries, embedding_model=model)


class ScriptedLLM:
    """Answers each prompt with the next scripted reply, recording prompts."""

    # Input: replies (list[str])
    # Output: None
    # Processing: Keep the replies to give, in order.
    # Changed by: Sourish
    # Date: 2026-10-22
    def __init__(self, *replies: str) -> None:
        self.replies = list(replies)
        self.prompts: list[str] = []

    # Input: prompt (str), system (str | None)
    # Output: str
    # Processing: Record the prompt and return the next reply.
    # Changed by: Sourish
    # Date: 2026-10-22
    def __call__(self, prompt: str, system: str | None = None) -> str:
        self.prompts.append(prompt)
        return self.replies.pop(0) if self.replies else "pass"


# Input: root (Path), files (dict[str, str]), git (bool), llm (ScriptedLLM | None)
# Output: Workspace
# Processing: A project folder with the given files, opened as a workspace.
# Changed by: Sourish
# Date: 2026-10-22
def project(root: Path, files: dict[str, str] | None = None, *, git: bool = False,
            llm: ScriptedLLM | None = None) -> Workspace:
    for rel, text in (files or {}).items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(text)
    if git:
        run = lambda *a: subprocess.run(["git", "-C", str(root), *a], check=True, capture_output=True)  # noqa: E731
        run("init", "-q")
        run("-c", "user.email=t@t", "-c", "user.name=t", "add", "-A")
        run("-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "-m", "start", "--allow-empty")
    shared = library()
    return Workspace(root, library=lambda: shared, llm=llm or ScriptedLLM())


# Input: ws (Workspace), command (str), confirm (bool), actor (str)
# Output: dict
# Processing: Run a command and return its result, failing the test on an error.
# Changed by: Sourish
# Date: 2026-10-22
def ok(ws: Workspace, command: str, *, confirm: bool = False, actor: str = "user") -> dict:
    reply = ws.command(command, confirm=confirm, actor=actor)
    assert reply["ok"], f"{command}: {reply['error']}"
    return reply["data"]["result"] if "result" in reply["data"] else reply["data"]


# Input: ws (Workspace), command (str), confirm (bool), actor (str)
# Output: str
# Processing: Run a command that must fail and return its error code.
# Changed by: Sourish
# Date: 2026-10-22
def fails(ws: Workspace, command: str, *, confirm: bool = False, actor: str = "user") -> str:
    reply = ws.command(command, confirm=confirm, actor=actor)
    assert not reply["ok"], f"{command} unexpectedly succeeded: {reply['data']}"
    return reply["error"]["code"]
