# Plovane

Plovane recommends verified, reusable code. You describe what you need, or
point it at a file, and it finds a tested helper from the library. The design,
and every contract between components, is in `Plovane_Handbook.md`.

## Repository layout

```
Plovane/
├── README.md               this file
├── Plovane_Handbook.md     architecture, block ownership and JSON contracts
├── pyproject.toml
├── ml/                     all ML work: model code, data, weights, training, serving, tests
├── whiteboard/             whiteboard backend: board, slash commands, local API, agent tools
├── frontend/               desktop app (B01) and CLI (B02)
└── docs/                   proposals for the handbook
```

## Where to start

| You work on | Start in |
|---|---|
| ML models, training, the recommendation API | `ml/README.md` |
| Calling the recommender from the backend | `ml/serving/API.md` |
| Whiteboard backend and its commands | `whiteboard/README.md` |
| Frontend (desktop app, CLI) | `frontend/README.md` |

Every folder has a README saying what belongs in it.

## Quick start

```sh
pip install torch transformers numpy huggingface_hub flask "mcp>=1.2"
read -s "GITHUB_TOKEN?Paste GitHub token: "; export GITHUB_TOKEN; echo
python -m ml.serving.setup            # download and verify the models
python -m pytest                      # run the tests
python -m ml.serving.service.app      # recommendation API on 127.0.0.1:8600
python -m whiteboard.server --project .   # whiteboard API on 127.0.0.1:8700
```

## Conventions

- Every function carries the five-field comment block (Input, Output,
  Processing, Changed by, Date). See `.claude/agents/README.md`.
- Each folder has one README.
- Adding or removing a top-level folder needs Sourish and Nirmal to approve it.
- Work happens on branches and merges into `dev`.
