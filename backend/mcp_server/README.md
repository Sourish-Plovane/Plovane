# Plovane MCP server

Gives Claude Code, Claude Desktop or any other MCP client access to Plovane's
models: the B06 helper recommender (retrieval plus the trained Stage 2
cross-encoder), the helper finder, and hosted LLMs (Claude, OpenAI). It runs
locally over stdio.

## Setup (once)

From the repository root:

```sh
pip install "mcp>=1.2" torch transformers numpy huggingface_hub
read -s "GITHUB_TOKEN?Paste GitHub token: "; export GITHUB_TOKEN; echo
python -m ml.serving.setup
```

Setup downloads the base encoder and the trained weights (about 1 GB) and
checks them. If `ml/data/entries.jsonl` is missing it rebuilds the library, then
loads everything once so the library embeddings are cached. It ends by
printing the exact command to register the server.

## Register it

**Claude Code**, run from the repository root:

```sh
claude mcp add plovane -- python -m backend.mcp_server
```

**Claude Desktop**: add this to `~/Library/Application Support/Claude/claude_desktop_config.json`,
using your own clone path and python:

```json
{
  "mcpServers": {
    "plovane": {
      "command": "/usr/bin/python3",
      "args": ["-m", "backend.mcp_server"],
      "cwd": "/Users/you/Plovane",
      "env": {"PYTHONPATH": "/Users/you/Plovane", "ANTHROPIC_API_KEY": "..."}
    }
  }
}
```

## Tools

| Tool | Kind | What it does |
|---|---|---|
| `search_helpers` | basic | Searches the library for code that does what you describe. Returns the B06 envelope (see docs/b06_recommendation_api.md). |
| `find_helpers_in_file` | basic | Lists the functions in a file that stand alone, with paste-ready code. Rule-based, so no model is needed. |
| `get_helper` | basic | Returns one library entry in full by `block_id`. |
| `list_categories` | basic | Counts the library's entries by category and language. |
| `model_status` | basic | Shows what's loaded, the device, the rerank depth, measured accuracy, and which LLM keys are set (names only). |
| `rerank_candidates` | complex | Scores your own snippets against a query with the cross-encoder. |
| `check_file_against_library` | complex | Labels each function in a file `in_library`, `similar_exists` or `new`, with the closest library entry. |
| `recommend_for_file` | complex | Suggests library helpers that fit what a file does, leaving out the ones the file already has. |
| `explain_ranking` | complex | Shows each stage's rank and score for the top results. |
| `batch_search` | complex | Runs several searches in one call. |
| `ask_llm` | external | Sends a prompt to Claude (`anthropic`), `openai` or `deepseek`. |

`find_helpers_in_file`, `check_file_against_library` and `recommend_for_file`
take either `path`, or pasted `code` plus a `filename` (the filename sets the
language).

### Whiteboard tools

The server also carries the whiteboard: 15 tools grouped by noun plus
`plovane_command`, 16 in all. Each grouped tool takes an `action`, its
`args`, and optionally `confirm` and `dry_run`. For example, `plovane_block`
with `{"action": "add", "args": {"kind": "service", "name": "auth"}}` does the
same as typing `/block add service auth`.

| Tool | Actions |
|---|---|
| `plovane_layer`, `plovane_view` | read the whole board (`board_read`), layers, zoom, find, focus, layout, filter |
| `plovane_project`, `plovane_block` | real folders and files, block add/move/group/split/merge/rename/delete/info |
| `plovane_place`, `plovane_style` | placement, alignment, size, grid, styles, presets, theme |
| `plovane_connect`, `plovane_endpoint` | arrows, imports, trace, dependencies, API endpoints and their client calls |
| `plovane_compile` | English step blocks, edit, compile with a test run, replace a step with a library helper |
| `plovane_template`, `plovane_batch` | templates and all-or-nothing batches |
| `plovane_library`, `plovane_code`, `plovane_history`, `plovane_agent` | library, code, scans, health, undo, Git, agent logs |
| `plovane_command` | any slash command as text |

The full command list is in `backend/whiteboard/README.md`. Commands that grant or
revoke access are not available here: a person runs them in the whiteboard.

## Settings (environment variables)

| Variable | Effect |
|---|---|
| `PLOVANE_RERANK_DEPTH` | How many candidates the cross-encoder rereads. The default is 20 on CPU (a few seconds; @1 67%) and 100 on GPU (@1 70%). |
| `PLOVANE_NO_CROSS_ENCODER` | Skips the cross-encoder. Faster and needs less RAM, but @1 drops to 52%. |
| `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, `DEEPSEEK_API_KEY` | Enable `ask_llm` for that provider. |
| `PLOVANE_ANTHROPIC_MODEL`, `PLOVANE_OPENAI_MODEL`, `PLOVANE_DEEPSEEK_MODEL` | Change the default model (`claude-sonnet-5-5`, `gpt-5`, `deepseek-chat`). |
| `PLOVANE_PROJECT` | The project the whiteboard tools edit. The default is the folder the server starts in. |
| `PLOVANE_AGENT` | The agent name the whiteboard tools act as, checked against its grants. Unset means a person. |
| `PLOVANE_LLM_PROVIDER` | `anthropic` (the default), `openai` or `deepseek`, for the whiteboard's English blocks and `/explain`. |

## Speed (laptop CPU, measured)

The first tool call loads the models in about 10 s. On the very first start it
also embeds the library, which takes about 80 s; the result is cached in
`ml/serving/.cache/`. After that, a search takes about 2-3 s and a file check
takes under 1 s. The server needs about 4 GB of RAM.

## Known limits

- The accuracy numbers come from assistant-written queries and are not
  certified (issue 19).
- The `in_library` cutoff (cosine 0.92) has been checked on one file only.
- `recommend_for_file` builds its query from the file's function names and
  docstrings. It is the least reliable tool and its results are loose
  suggestions.
- Some library entries' `code` begins with stray dependency names, e.g.
  `toFormData` on its own line. That's a known ingest bug that hasn't been
  fixed yet.
