# Whiteboard backend

The backend for the Plovane whiteboard. People and AI agents edit a project
by running the same slash commands. Each visual change also changes the
project itself: folders, files, imports, route handlers and client calls.
Every change can be undone and is logged.

## Layout

```
whiteboard/
├── README.md        this file, with the full command reference at the end
├── __init__.py
├── model.py         board data: layers, blocks, arrows, endpoints, presets, templates, agents
├── registry.py      the table of operations, plus argument helpers
├── engine.py        runs operations: transactions, undo/redo, confirmation, dry-run, agent permissions
├── store.py         reads and writes .plovane/ and journals every file change
├── commands.py      parses "/block add ..." into an operation
├── codegen.py       the code the whiteboard writes (handlers, client calls, imports, file templates)
├── refactor.py      safe code changes: symbols, real references, imports that follow a move
├── sandbox.py       runs the affected tests on a temporary copy of the project before code is written
├── server.py        local HTTP API for the desktop app and CLI (127.0.0.1:8700)
├── mcp_tools.py     grouped MCP tools for AI agents (registered by backend/mcp_server/)
└── ops/             one module per command category
    ├── layers.py      layers and zoom
    ├── project.py     real folders and files, /project import
    ├── blocks.py      add, move, group, split, merge, rename, delete, info
    ├── english.py     English step blocks, /compile, /block replace
    ├── placement.py   coordinates, relative placement, align, distribute, grid, size
    ├── style.py       styles, presets, themes
    ├── arrows.py      connections, trace, dependencies
    ├── endpoints.py   API endpoints, client calls, tests, mocks, OpenAPI
    ├── templates.py   reusable layouts with library-filled slots
    ├── batch.py       many operations as one
    ├── agents.py      agent access by path and by canvas region
    ├── library.py     /rank, /insert, /palette, /suggest, /save, /cite
    ├── code.py        open, diff, explain
    ├── health.py      security scan, alerts, health, live monitor
    ├── history.py     undo, redo, snapshots, history, Git
    └── view.py        find, focus, layout, filter
└── tests/           pytest suite for the whiteboard
```

## Running it

```sh
python -m backend.whiteboard.server --project /path/to/project     # HTTP API for the frontend
python -m backend.mcp_server                                    # MCP tools; set PLOVANE_PROJECT to the project
```

The board is saved in the project as `.plovane/board.json` and committed with
the code. The code in Git is the source of truth: the board stores only what
code can't hold (positions, styles, layers, endpoints, presets).
`/project import` maps an existing project's folders, files and imports onto
blocks. The undo history, the operation log, previews and monitor output stay
local, and `.plovane/.gitignore` excludes them.

## Calling it from the frontend

Every reply uses the handbook's §4.1 envelope.

```http
POST /api/v1/whiteboard/command
{"command": "/endpoint add payments POST /charge --request ChargeRequest --response Receipt"}
```

```json
{"ok": true, "error": null, "data": {
  "result": {"id": "payments:POST /charge", "method": "POST", "path": "/charge",
             "handler": "services/payments/routes.py", "status": "built", "callers": []},
  "changes": {"endpoints": {"added": ["payments:POST /charge"], "removed": [], "changed": []},
              "files": ["services/payments/routes.py"]}}}
```

`changes` lists what to redraw. `notes`, when present, are messages to show
the user.

**Commands that need confirmation** reply with a preview instead of acting:

```json
{"ok": true, "error": null, "data": {"status": "needs_confirmation",
  "message": "/block delete removes a block and its code after showing dependents. ...",
  "preview": {"result": {"dependents": {"importers": ["src/orders.py"]}}, "changes": {...}}}}
```

Show the preview, and if the person agrees, send the same command again with
`"confirm": true`. `"dry_run": true` previews any command without changing
anything.

**Errors** come back as `{"ok": false, "data": null, "error": {"code", "message"}}`
with HTTP 400 (bad input), 403 (`FORBIDDEN`), 404 (`NOT_FOUND`) or 409
(`CONFLICT`, `AMBIGUOUS`, `TESTS_FAILED`).

**Drawing:** `GET /api/v1/whiteboard/board` returns the board, each block's
resolved style (its preset plus its own flags), absolute positions, and
arrows with the style that matches their type and status:
- built arrows are solid
- planned arrows are dashed
- failing arrows are red

`GET /api/v1/whiteboard/commands` lists every command with an example, which
you can use for autocomplete.

## AI agents

Agents use the same operations through MCP, grouped into 15 tools by noun
(`plovane_block` with `action: "add"`, and so on) plus `plovane_command`,
which takes the slash command text. Grouping keeps the tool list small enough
not to crowd an agent's context.

Agents are held to their grants:
- `/agent grant NAME PATH --mode read|read-write` gives access by path, and the most specific grant wins.
- `/agent region` gives access to the blocks inside a canvas rectangle.

An MCP agent is named by `PLOVANE_AGENT`; an HTTP caller sends the
`X-Plovane-Agent` header. Only a person can add agents or change access:
those commands are left out of MCP and refused when an agent sends them.

## What it writes into a project

Generated code sits between `plovane:begin` and `plovane:end` comments, so
later commands change exactly what earlier ones wrote. Generated code follows
the repository's five-field comment standard.

- `/endpoint add` writes a Flask handler (Python) or an Express handler
  (JavaScript) into `<service folder>/routes.py|js`. The handler returns 501
  until someone implements it.
- `/endpoint link` writes a client function into the caller's file.
- `/connect --type import` adds the import statement. `/disconnect` removes it.
- `/insert` copies a library entry's exact code with its licence and source link.
- `/block edit` and `/compile` show the diff and run the affected tests in a
  sandbox. Nothing is written until `/compile apply`, and only if the tests
  pass.

## Changing code safely

- **Rename** follows real references, read from the Python syntax tree:
  - the definition
  - uses that aren't shadowed by a local of the same name
  - `from pkg.mod import name`, aliased or not
  - `mod.name` and `pkg.mod.name`
  - `__all__`

  A local variable or parameter that only shares the name is left alone.
- **Methods** are renamed inside their class, including their `self.` and
  `cls.` calls. A call through some other object can't be traced reliably,
  so those files are listed in the reply's `notes` for checking by hand.
- **Move and rename of files and folders** rewrite every import that pointed
  at them. A moved file's own relative imports are recomputed too.
  JavaScript relative paths are recomputed the same way.
- **Classes** are blocks that contain their methods, and methods get English
  steps like functions.

Tested end to end on a copy of this repository: after a file move, a file
rename, a function rename and a package rename, its 658 tests still passed,
and undoing all of it restored every file.

## Limits

- `/block edit`, `/compile blocks-to-code` and `/explain` with AI need an LLM
  key: `ANTHROPIC_API_KEY`, or `OPENAI_API_KEY` / `DEEPSEEK_API_KEY` with
  `PLOVANE_LLM_PROVIDER=openai` / `deepseek`.
  Without a key, the edit is kept and `/explain` gives a structural summary.
  Compiling English back to code hasn't been tried with a real LLM yet.
- JavaScript and TypeScript are read with the library's heuristic extractor:
  - renames there match whole words, but only in the defining file and in the files that import it
  - classes and methods aren't mapped
  - English steps are one per line unless `--ai` is passed
- Only Python (Flask) handlers can be sent a real test request
  (`/endpoint test`). For other languages, use `/endpoint mock`.
- The sandbox runs Python tests only (pytest), chosen by file name and imports.
- `/save` checks the code and queues it in `.plovane/submissions.jsonl`. It
  joins the library only after the verification pipeline (B12 to B14), which
  isn't built yet.
- `/monitor` follows processes started by the same running server.

## Command reference

Generated by `python -m backend.whiteboard.commands --markdown`. "Confirms" means the
command answers `needs_confirmation` first.

| Command | Operation | MCP tool | Confirms | What it does | Example |
|---|---|---|---|---|---|
| `/layer define` | `layer.define` | `plovane_layer` |  | Creates a layer with its zoom range and the block types it shows. | `/layer define 1 --zoom 10-20 --show services` |
| `/layer edit` | `layer.edit` | `plovane_layer` |  | Changes a layer's zoom range or contents. | `/layer edit 2 --zoom 20-40` |
| `/layer list` | `layer.list` | `plovane_layer` |  | Shows all layers with their ranges and contents. | `/layer list` |
| `/layer rename` | `layer.rename` | `plovane_layer` |  | Gives a layer a readable name. | `/layer rename 1 "Services"` |
| `/layer remove` | `layer.remove` | `plovane_layer` | yes | Deletes a layer; its blocks move to the nearest layer. | `/layer remove 4` |
| `/zoom into` | `view.zoom_into` | `plovane_view` |  | Zooms into a specific block. | `/zoom into payments-service` |
| `/zoom out` | `view.zoom_out` | `plovane_view` |  | Moves up one layer. | `/zoom out` |
| `/zoom` | `view.zoom` | `plovane_view` |  | Jumps to a zoom level or a layer. | `/zoom 15  or  /zoom layer 2` |
| `/folder create` | `folder.create` | `plovane_project` |  | Creates a real folder in the project and its block on the whiteboard. | `/folder create src/payments --right-of src/auth` |
| `/file create` | `file.create` | `plovane_project` |  | Creates a real file, optionally from a starter template. | `/file create src/payments/charge.py --template python-module` |
| `/folder delete` | `folder.delete` | `plovane_project` | yes | Deletes a folder after showing what depends on it. | `/folder delete src/old-cache` |
| `/project import` | `project.import` | `plovane_project` |  | Maps existing folders, files and imports onto blocks; Git stays the source of truth. | `/project import` |
| `/block add` | `block.add` | `plovane_block` |  | Adds a block to a layer. | `/block add service "auth" --layer 1` |
| `/block move` | `block.move` | `plovane_block` |  | Moves a block on the canvas or into a group; moving a file block moves the real file. | `/block move auth --into backend` |
| `/block group` | `block.group` | `plovane_block` |  | Groups blocks into a folder or server. | `/block group auth,users --as "identity"` |
| `/block split` | `block.split` | `plovane_block` |  | Splits a block into smaller blocks. | `/block split payments --by file` |
| `/block merge` | `block.merge` | `plovane_block` | yes | Combines blocks into one. | `/block merge invoice,billing` |
| `/block rename` | `block.rename` | `plovane_block` |  | Renames a block. | `/block rename svc1 "checkout"` |
| `/block delete` | `block.delete` | `plovane_block` | yes | Removes a block and its code after showing dependents. | `/block delete old-cache` |
| `/block info` | `block.info` | `plovane_block` |  | Shows a block's code, connections, and source. | `/block info checkout` |
| `/blocks view` | `file.blocks` | `plovane_compile` |  | Shows a code file as plain-English step blocks at the chosen detail level. | `/blocks view src/orders.py --detail 2` |
| `/block edit` | `block.edit_text` | `plovane_compile` |  | Rewrites an English block; the AI regenerates the code underneath as a reviewable diff. | `/block edit step-3 "Charge the card, retry twice on failure"` |
| `/block replace` | `block.replace` | `plovane_compile` |  | Replaces a block with a verified palette block. | `/block replace step-3 --with retry_request --times 3` |
| `/compile apply` | `compile.apply` | `plovane_compile` | yes | Applies a previewed compile after tests pass. | `/compile apply c_102` |
| `/compile` | `compile` | `plovane_compile` | sometimes | Compiles changed blocks to code (or code to blocks), shows a diff, and runs affected tests. | `/compile blocks-to-code src/orders.py --preview` |
| `/place` | `place` | `plovane_place` |  | Places a block at exact coordinates, or relative to another block. | `/place auth --at 200,120  or  /place billing --right-of auth --gap 80` |
| `/align` | `align` | `plovane_place` |  | Aligns blocks along an edge or center. | `/align auth,users,billing --top` |
| `/distribute` | `distribute` | `plovane_place` |  | Spaces blocks evenly. | `/distribute auth,users,billing --horizontal --gap 60` |
| `/grid` | `grid` | `plovane_place` |  | Sets grid size and snapping. | `/grid 20 --snap on` |
| `/coords` | `coords` | `plovane_place` |  | Shows a block's position and size. | `/coords auth` |
| `/size` | `size` | `plovane_place` |  | Sets a block's width and height, or fits it to its contents. | `/size auth 240x120  or  /size auth --fit` |
| `/style` | `style` | `plovane_style` |  | Sets visual properties on a block, or applies a saved preset. | `/style auth --preset service` |
| `/preset define` | `preset.define` | `plovane_style` |  | Saves a named style for reuse. | `/preset define service --fill #E8F0FE --border #1A73E8 --radius 12 --icon server` |
| `/theme` | `theme` | `plovane_style` |  | Changes all colors at once. | `/theme dark` |
| `/connect` | `connect` | `plovane_connect` |  | Draws a typed arrow; creates or links real code. Unbuilt endpoints show as dashed "planned". | `/connect checkout -> payments --type api --endpoint "POST /charge"` |
| `/disconnect` | `disconnect` | `plovane_connect` | yes | Removes a connection. | `/disconnect checkout -> payments` |
| `/connections` | `connections` | `plovane_connect` |  | Lists everything a block connects to. | `/connections payments` |
| `/trace` | `trace` | `plovane_connect` |  | Highlights the full path a request takes. | `/trace "POST /checkout"` |
| `/deps` | `deps` | `plovane_connect` |  | Shows what a block depends on, or what depends on it. | `/deps payments --reverse` |
| `/arrow style` | `arrow.style` | `plovane_connect` |  | Restyles an existing arrow. | `/arrow style checkout->payments --width 2 --dash 4` |
| `/arrow route` | `arrow.route` | `plovane_connect` |  | Changes how an arrow bends. | `/arrow route checkout->payments --curve orthogonal` |
| `/endpoint add` | `endpoint.add` | `plovane_endpoint` |  | Adds an endpoint block to a service and creates the real route handler. | `/endpoint add payments POST /charge --request ChargeRequest --response Receipt` |
| `/endpoint link` | `endpoint.link` | `plovane_endpoint` |  | Connects a block to an endpoint with an API arrow and generates the client call code. | `/endpoint link checkout -> payments:POST /charge` |
| `/endpoint unlink` | `endpoint.unlink` | `plovane_endpoint` | yes | Removes an API connection and its client call code. | `/endpoint unlink checkout -> payments:POST /charge` |
| `/endpoint list` | `endpoint.list` | `plovane_endpoint` |  | Lists a service's endpoints and who calls each one. | `/endpoint list payments` |
| `/endpoint info` | `endpoint.info` | `plovane_endpoint` |  | Shows an endpoint's method, path, schemas, handler code, and callers. | `/endpoint info payments:POST /charge` |
| `/endpoint edit` | `endpoint.edit` | `plovane_endpoint` | yes | Changes an endpoint's path, method, or schemas and flags callers that break. | `/endpoint edit payments:POST /charge --path /charges` |
| `/endpoint delete` | `endpoint.delete` | `plovane_endpoint` | yes | Deletes an endpoint after showing every block that calls it. | `/endpoint delete payments:GET /legacy` |
| `/endpoint test` | `endpoint.test` | `plovane_endpoint` |  | Sends a test request in the sandbox and shows the response on the arrow. | `/endpoint test payments:POST /charge --body sample` |
| `/endpoint mock` | `endpoint.mock` | `plovane_endpoint` |  | Creates a mock response so callers can be built before the endpoint exists. | `/endpoint mock payments:POST /charge --response sample` |
| `/endpoint check` | `endpoint.check` | `plovane_endpoint` |  | Checks every API arrow: caller request matches endpoint schema; mismatches turn red. | `/endpoint check all` |
| `/endpoint import` | `endpoint.import` | `plovane_endpoint` |  | Creates endpoint blocks from an OpenAPI spec. | `/endpoint import openapi.yaml` |
| `/endpoint export` | `endpoint.export` | `plovane_endpoint` |  | Exports a service's endpoints as an OpenAPI spec. | `/endpoint export payments` |
| `/api external add` | `external.add` | `plovane_endpoint` |  | Adds a third-party API (such as a payment provider) as an external block with its endpoints. | `/api external add stripe --spec stripe-openapi.yaml` |
| `/template create` | `template.create` | `plovane_template` |  | Saves the current layer or selection as a reusable layout template with placeholders. | `/template create "rest-api-service" --from layer 1` |
| `/template apply` | `template.apply` | `plovane_template` |  | Places a template's blocks, positions, styles, and connections in one step. | `/template apply rest-api-service --layer 1 --at 0,0 --name orders` |
| `/template list` | `template.list` | `plovane_template` |  | Shows available templates. | `/template list --layer services` |
| `/template preview` | `template.preview` | `plovane_template` |  | Shows what a template would place without placing it. | `/template preview rest-api-service` |
| `/template edit` | `template.edit` | `plovane_template` |  | Changes a template's blocks or slots. | `/template edit rest-api-service --add-slot cache` |
| `/template fill` | `template.fill` | `plovane_template` |  | Fills a template's empty slots with verified library entries. | `/template fill orders --from-library` |
| `/batch` | `batch` | `plovane_batch` | sometimes | Runs many operations as one; all succeed or none do. | `/batch  (one operation per line)` |
| `/agent add` | `agent.add` | `plovane_agent` |  | Places an agent on the whiteboard. | `/agent add "test-writer"` |
| `/agent grant` | `agent.grant` | `plovane_agent` | yes | Gives an agent access to a path or region. | `/agent grant test-writer tests/ --mode read-write` |
| `/agent revoke` | `agent.revoke` | `plovane_agent` | yes | Removes an agent's access. | `/agent revoke test-writer payments/` |
| `/agent region` | `agent.region` | `plovane_agent` | yes | Grants access by canvas coordinates. | `/agent region reviewer --area 100,200,400,500 --mode read` |
| `/agent show` | `agent.show` | `plovane_agent` |  | Highlights what an agent can see and change. | `/agent show test-writer` |
| `/agent log` | `agent.log` | `plovane_agent` |  | Shows everything an agent has done. | `/agent log test-writer --last 20` |
| `/rank` | `library.rank` | `plovane_library` |  | Searches the verified library for a task and returns ranked slim cards. | `/rank "retry failed HTTP requests" --k 10` |
| `/insert` | `library.insert` | `plovane_library` |  | Inserts a library entry as a block, copying the exact tested code. | `/insert hf_0412 --into data/` |
| `/palette` | `library.palette` | `plovane_library` |  | Opens a palette of verified blocks for a category. | `/palette ui-components` |
| `/suggest` | `library.suggest` | `plovane_library` |  | Runs the reusability model on a block or file. | `/suggest checkout` |
| `/save` | `library.save` | `plovane_library` |  | Saves a block to the library after naming, tests, and verification. | `/save normalize_batch --private` |
| `/cite` | `library.cite` | `plovane_library` |  | Shows which library entries a block uses. | `/cite checkout` |
| `/open` | `code.open` | `plovane_code` |  | Opens a block's code in the editor. | `/open checkout.ts` |
| `/diff` | `code.diff` | `plovane_code` |  | Shows what changed in a block. | `/diff payments --since yesterday` |
| `/explain` | `code.explain` | `plovane_code` |  | Explains a block in plain English. | `/explain charge.ts` |
| `/scan` | `health.scan` | `plovane_code` |  | Places a security detection block over an area and scans it. | `/scan payments/` |
| `/alerts` | `health.alerts` | `plovane_code` |  | Lists active alerts on the diagram. | `/alerts --severity high` |
| `/health` | `health.check` | `plovane_code` |  | Marks blocks with failing tests or broken connections. | `/health` |
| `/monitor` | `health.monitor` | `plovane_code` |  | Runs the project and shows live values on blocks and arrows. | `/monitor start checkout` |
| `/undo` | `history.undo` | `plovane_history` |  | Undoes the last change. | `/undo` |
| `/redo` | `history.redo` | `plovane_history` |  | Redoes the last undone change. | `/redo` |
| `/snapshot` | `history.snapshot` | `plovane_history` |  | Saves the current whiteboard state. | `/snapshot "before refactor"` |
| `/history` | `history.show` | `plovane_history` |  | Shows changes over time. | `/history payments` |
| `/git status` | `git.status` | `plovane_history` |  | Shows changed blocks compared to GitHub. | `/git status` |
| `/git sync` | `git.sync` | `plovane_history` | yes | Pulls or pushes changes. | `/git sync --push` |
| `/find` | `view.find` | `plovane_view` |  | Finds and highlights a block. | `/find "stripe"` |
| `/focus` | `view.focus` | `plovane_view` |  | Dims everything except a block and its connections. | `/focus payments` |
| `/layout` | `view.layout` | `plovane_view` |  | Rearranges the canvas automatically. | `/layout left-to-right` |
| `/filter` | `view.filter` | `plovane_view` |  | Shows only certain block or arrow types. | `/filter --arrows api` |
