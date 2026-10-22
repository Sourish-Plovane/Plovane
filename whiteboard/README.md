# Whiteboard backend

Backend for the Plovane whiteboard: the board state, every slash command from
the command sheet, and the MCP tools agents call. Being built; the layout is
below.

```
whiteboard/
├── README.md        this file
├── __init__.py
├── model.py         board data: layers, blocks, arrows, endpoints, presets, templates, agents
├── store.py         reads and writes .plovane/board.json and the operation log
├── engine.py        runs operations: permissions, confirmation, undo/redo, batch, dry-run
├── commands.py      parses "/block add ..." into an operation
├── mcp_tools.py     the grouped MCP tools (one per noun) agents call
└── ops/             one module per command category
```
