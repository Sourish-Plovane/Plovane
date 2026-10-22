# Plovane

Plovane recommends verified, reusable code. You describe what you need, or
point it at a file, and it finds a tested helper from the library. A whiteboard
lets people and AI agents lay out and edit a project visually. The design is in
`Plovane_Handbook.md`.

## Repository layout

```
Plovane/
├── README.md               this file
├── Plovane_Handbook.md     architecture and the JSON contracts between components
├── ml/                     model code: ingestion, retrieval, ranking, evaluation
├── ml_models/              running the models: engine, HTTP service, trained artefacts (see its README)
│   ├── service/            Flask backend for the recommender (B06)
│   └── trained/            trained ranker files and their status
├── mcp_server/             the models as MCP tools for Claude Code / Claude Desktop (B04)
├── whiteboard/             whiteboard backend: board state, slash commands, MCP tools
├── scripts/                training, GPU rental and weight download scripts
├── weights/                manifest of model weights; the files themselves are downloaded
├── seed/                   library sources and training queries
├── artifacts/              metrics from training runs
├── docs/                   contracts for other teams, e.g. the B06 API
└── tests/                  pytest suite
```

## Quick start

```sh
pip install torch transformers numpy huggingface_hub flask "mcp>=1.2"
read -s "GITHUB_TOKEN?Paste GitHub token: "; export GITHUB_TOKEN; echo
python -m ml_models.setup          # download and verify the models
python -m pytest                   # run the tests
```

Then start the HTTP backend with `python -m ml_models.service.app`, or
register the MCP server with `claude mcp add plovane -- python -m mcp_server`.
