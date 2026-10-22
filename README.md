# Plovane

Plovane recommends verified, reusable code. You describe what you need, or
point it at a file, and it finds a tested helper from the library. A whiteboard
lets people and AI agents lay out and edit a project visually. The design and
every contract between components are in `Plovane_Handbook.md`.

## Where to start

| You work on | Start in | Then read |
|---|---|---|
| Frontend (desktop app, CLI) | `frontend/` | `frontend/README.md` lists every backend endpoint you call |
| Whiteboard backend | `whiteboard/` | `whiteboard/README.md` lists every command and its JSON |
| Recommendation backend / API | `ml_models/service/` | `docs/b06_recommendation_api.md` |
| ML models and training | `ml/`, `ml_models/` | `ml_models/README.md` |
| AI agent integration (MCP) | `mcp_server/` | `mcp_server/README.md` |

Every folder has a README saying what belongs in it.

## Repository layout

```
Plovane/
├── README.md               this file
├── Plovane_Handbook.md     architecture, block ownership and JSON contracts
│
├── frontend/               what people use (owner: Aarav)
│   ├── desktop/            B01 desktop GUI: whiteboard canvas and prompt panel
│   └── cli/                B02 command-line interface
│
├── whiteboard/             whiteboard backend: board state, slash commands, local HTTP API
│   └── ops/                one module per command category
├── mcp_server/             B04: models and whiteboard as MCP tools for AI agents
│
├── ml_models/              running the models: engine, HTTP service, trained files
│   ├── service/            B06 Flask service: /v1/recommend, /v1/feedback, ...
│   └── trained/            trained ranker files and their certification status
├── ml/                     model code: ingest, retrieval, ranking, reuse, evaluation
│
├── scripts/                training, GPU rental, weight download, checks
├── weights/                manifest of model weights (the files are downloaded, not in git)
├── seed/                   library sources and training queries
├── artifacts/              metrics from training runs
├── docs/                   contracts for other teams
└── tests/                  pytest suite for everything above
```

## How the pieces connect

```
frontend (desktop, CLI) ──HTTP 127.0.0.1──► whiteboard API ──► project files on disk
          AI agents ──MCP──► mcp_server ──┘        │
                                                   └──HTTPS──► ml_models service ──► ml (models)
```

## Quick start

```sh
pip install torch transformers numpy huggingface_hub flask "mcp>=1.2"
read -s "GITHUB_TOKEN?Paste GitHub token: "; export GITHUB_TOKEN; echo
python -m ml_models.setup                       # download and verify the models
python -m pytest                                # run the tests
python -m ml_models.service.app                 # model service on 127.0.0.1:8600
python -m whiteboard.server --project .         # whiteboard API on 127.0.0.1:8700
```

To give Claude Code the tools: `claude mcp add plovane -- python -m mcp_server`.

## Conventions

- Every function carries the five-field comment block (Input, Output,
  Processing, Changed by, Date); `python scripts/check_comments.py ml_models`
  checks a folder. See `.claude/agents/README.md`.
- One README per folder.
- Adding or removing a top-level folder needs Sourish and Nirmal to approve it.
- Work happens on branches and merges into `dev`.
