"""Plovane's models as an MCP server (stdio).

    python -m ml.mcp_server

Basic tools: search_helpers, find_helpers_in_file, get_helper,
list_categories, model_status. Complex tools: rerank_candidates,
check_file_against_library, recommend_for_file, explain_ranking,
batch_search. Plus ask_llm, which forwards a prompt to Claude or OpenAI.
See ml/mcp_server/README.md.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

try:  # mcp 2.x renamed FastMCP to MCPServer; same decorator API
    from mcp.server.mcpserver import MCPServer
except ImportError:  # mcp 1.x
    from mcp.server.fastmcp import FastMCP as MCPServer

from ml.serving import llm
from ml.serving.engine import get_engine

mcp = MCPServer("plovane")


# Input: path (str | None), code (str | None), filename (str | None)
# Output: tuple[str, str]
# Processing: A file's source and name, from a path or pasted code.
# Changed by: Sourish
# Date: 2026-10-18
def _source(path: str | None, code: str | None, filename: str | None) -> tuple[str, str]:
    if path:
        file = Path(path).expanduser()
        return file.read_text(errors="replace"), file.name
    if code is None or not filename:
        raise ValueError("pass either path, or code together with filename (for the language)")
    return code, filename


# -- Basic ---------------------------------------------------------------------


# Input: query (str), language (str | None), k (int), dependencies (list[str] | None), drop_in_only (bool)
# Output: dict[str, Any]
# Processing: MCP tool: search the library with the full B06 pipeline.
# Changed by: Sourish
# Date: 2026-10-22
@mcp.tool()
def search_helpers(
    query: str, language: str | None = None, k: int = 5,
    dependencies: list[str] | None = None, drop_in_only: bool = False,
) -> dict[str, Any]:
    """Search the helper library for code that does what `query` describes.

    language: "python", "javascript", "typescript" or "cpp"; set it whenever
    known. dependencies: packages the project already has. drop_in_only: only
    code that needs nothing new installed. Returns the B06 envelope, best first.
    """
    return get_engine().search(query, language=language, k=k,
                               dependencies=dependencies, drop_in_only=drop_in_only)


# Input: path (str | None), code (str | None), filename (str | None), helpers_only (bool)
# Output: dict[str, Any]
# Processing: MCP tool: list the standalone helpers in one file.
# Changed by: Sourish
# Date: 2026-10-22
@mcp.tool()
def find_helpers_in_file(
    path: str | None = None, code: str | None = None, filename: str | None = None,
    helpers_only: bool = False,
) -> dict[str, Any]:
    """List the functions in one file that stand alone as reusable helpers.

    Give `path`, or paste `code` with a `filename` such as "utils.py". Each
    helper comes with its imports and paste-ready code; near helpers list the
    names they still depend on. Rule-based, no model needed.
    """
    from ml.ingest.find_helpers import find_helpers

    source, name = _source(path, code, filename)
    report = find_helpers(source, name)
    if helpers_only:
        report = {k: v for k, v in report.items() if k not in ("near_helpers", "unchecked")}
    return report


# Input: block_id (str)
# Output: dict[str, Any]
# Processing: MCP tool: one library entry in full.
# Changed by: Sourish
# Date: 2026-10-22
@mcp.tool()
def get_helper(block_id: str) -> dict[str, Any]:
    """Fetch one library entry in full, code included, by its block_id
    (e.g. "axios_axios:combineURLs")."""
    return get_engine().get(block_id)


# Input: language (str | None)
# Output: dict[str, Any]
# Processing: MCP tool: library entry counts by category and language.
# Changed by: Sourish
# Date: 2026-10-22
@mcp.tool()
def list_categories(language: str | None = None) -> dict[str, Any]:
    """What the library holds: entry counts by category and language."""
    return get_engine().categories(language)


# Input: none
# Output: dict[str, Any]
# Processing: MCP tool: loaded models, device, accuracy and LLM keys set.
# Changed by: Sourish
# Date: 2026-10-22
@mcp.tool()
def model_status() -> dict[str, Any]:
    """Which models are loaded, on what device, and their measured accuracy.
    The first call loads the models (up to a minute or two)."""
    try:
        status = get_engine().status()
    except Exception as exc:  # report a broken setup rather than crash the tool
        status = {"error": str(exc)}
    return status | {"llm_keys_set": llm.configured()}


# -- Complex -------------------------------------------------------------------


# Input: query (str), candidates (list[dict]), language (str | None)
# Output: dict[str, Any]
# Processing: MCP tool: cross-encoder scores for the caller's snippets.
# Changed by: Sourish
# Date: 2026-10-22
@mcp.tool()
def rerank_candidates(query: str, candidates: list[dict], language: str | None = None) -> dict[str, Any]:
    """Score your own code snippets against a query with the trained
    cross-encoder, best first.

    candidates: [{"name": ..., "code": ..., "docstring"?: ..., "signature"?: ...,
    "id"?: ...}]. Scores order these candidates only; they are not probabilities.
    """
    return get_engine().rerank(query, candidates, language=language)


# Input: path (str | None), code (str | None), filename (str | None)
# Output: dict[str, Any]
# Processing: MCP tool: mark each function in a file in_library, similar_exists or new.
# Changed by: Sourish
# Date: 2026-10-22
@mcp.tool()
def check_file_against_library(
    path: str | None = None, code: str | None = None, filename: str | None = None,
) -> dict[str, Any]:
    """For each function in a file, say whether the library already has it.

    verdict is "in_library" (same helper; use the library's), "similar_exists"
    (a close relative worth comparing), or "new" (a candidate to contribute).
    """
    source, name = _source(path, code, filename)
    return get_engine().check_file(source, name)


# Input: path (str | None), code (str | None), filename (str | None), k (int)
# Output: dict[str, Any]
# Processing: MCP tool: library helpers that fit what a file does.
# Changed by: Sourish
# Date: 2026-10-22
@mcp.tool()
def recommend_for_file(
    path: str | None = None, code: str | None = None, filename: str | None = None, k: int = 5,
) -> dict[str, Any]:
    """Suggest library helpers that fit what a file does, leaving out the
    ones the file already contains."""
    source, name = _source(path, code, filename)
    return get_engine().recommend_for_file(source, name, k=k)


# Input: query (str), language (str | None), k (int)
# Output: dict[str, Any]
# Processing: MCP tool: every stage's rank and score for the top results.
# Changed by: Sourish
# Date: 2026-10-22
@mcp.tool()
def explain_ranking(query: str, language: str | None = None, k: int = 5) -> dict[str, Any]:
    """Show why the top results rank where they do: keyword rank, vector rank,
    fused rank, cross-encoder score and final score for each."""
    return get_engine().explain(query, language=language, k=k)


# Input: queries (list[str]), language (str | None), k (int)
# Output: dict[str, Any]
# Processing: MCP tool: several library searches in one call.
# Changed by: Sourish
# Date: 2026-10-22
@mcp.tool()
def batch_search(queries: list[str], language: str | None = None, k: int = 3) -> dict[str, Any]:
    """Run several library searches in one call, e.g. one per step of a plan."""
    return get_engine().batch(queries, language=language, k=k)


# -- External models -----------------------------------------------------------


# Input: prompt (str), provider (str), model (str | None), system (str | None), max_tokens (int)
# Output: dict[str, Any]
# Processing: MCP tool: send a prompt to Claude or OpenAI.
# Changed by: Sourish
# Date: 2026-10-22
@mcp.tool()
def ask_llm(
    prompt: str, provider: str = "anthropic", model: str | None = None,
    system: str | None = None, max_tokens: int = 1024,
) -> dict[str, Any]:
    """Send a prompt to a hosted model: provider "anthropic" (Claude) or
    "openai". Needs ANTHROPIC_API_KEY or OPENAI_API_KEY in the server's
    environment. model defaults to claude-sonnet-5-5 / gpt-5."""
    return llm.ask(prompt, provider=provider, model=model, system=system, max_tokens=max_tokens)


from backend.whiteboard.mcp_tools import register as register_whiteboard  # noqa: E402

WHITEBOARD_TOOLS = register_whiteboard(mcp)
"""The whiteboard's grouped tools (backend/whiteboard/mcp_tools.py). They edit the
project in PLOVANE_PROJECT, or the folder the server starts in."""


# Input: none
# Output: None
# Processing: Run over stdio.
# Changed by: Sourish
# Date: 2026-10-18
def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
