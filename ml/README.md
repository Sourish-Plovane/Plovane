# ml

All of Plovane's machine-learning work lives here: the model code, its data,
weights, training, serving and tests. It is one Python package, `ml`, so
everything runs from the repository root as `python -m ml.<something>`.

## Layout

```
ml/
├── README.md          this file
├── __init__.py
├── config.py          settings, and the paths below (ML_ROOT, DATA_DIR, WEIGHTS_DIR, ARTIFACTS_DIR)
├── schemas.py         shared types: Request, Entry, Candidate, Response
├── schemas_library.py library request/response shapes
│
│   ── Model code: how recommendations are made ──
├── ingest/            turns source repositories into library entries; finds helpers in a file
├── retrieval/         keyword + vector search over the library (the bi-encoder lives here)
├── ranking/           Stage 1, Stage 2 (cross-encoder) and Stage 3 rankers
├── reuse/             is a function worth saving: duplicates and reusability score
├── evaluation/        metrics, gold sets and certification gates
├── templates/         whole-file templates and their recommender
│
│   ── Running the models ──
├── serving/           loads the models once and serves them (see serving/README.md)
│   └── service/       the Flask HTTP API: /v1/recommend, /v1/feedback, ...
├── mcp_server/        the same models as MCP tools for Claude Code / Claude Desktop
│
│   ── Making the models ──
├── training/          training, GPU rental and weight-download scripts
├── data/              library sources and training queries (the library itself is generated)
├── weights/           weight manifest; the weight files are downloaded here, not committed
├── artifacts/         training results: metrics and trained ranker files
│
└── tests/             pytest suite for everything above
```

## The models

| Model | What it does | Trained? | Code |
|---|---|---|---|
| Bi-encoder (UniXcoder) | Turns queries and code into vectors for search | Pretrained | `retrieval/bi_encoder.py` |
| Cross-encoder (Stage 2) | Reads query and code together and scores the match | Fine-tuned, 126M params (issue 35) | `ranking/cross_encoder.py` |
| Stage 1 / Stage 3 rankers | Blend retrieval and usage signals into the final order | Hand weights; trained boosters are uncertified | `ranking/` |
| Helper finder | Lists the standalone functions in a file | Rule-based | `ingest/find_helpers.py` |
| Hosted LLMs | Claude or OpenAI, for text generation | Not ours | `serving/llm.py` |

`python -m ml.serving.registry` prints this with what is present on the machine.

## Quick start

```sh
pip install torch transformers numpy huggingface_hub flask "mcp>=1.2"
read -s "GITHUB_TOKEN?Paste GitHub token: "; export GITHUB_TOKEN; echo
python -m ml.serving.setup            # once: weights, library, embedding cache
python -m pytest                      # tests (no weights or network needed)
python -m ml.serving.service.app      # HTTP API on 127.0.0.1:8600
claude mcp add plovane -- python -m ml.mcp_server   # MCP tools for Claude Code
```

Measured on 350 held-out queries, the right helper comes first 69.7% of the
time and is in the top 3 88.3% of the time. These numbers aren't certified,
because the queries were written by an assistant rather than real users
(issue 19).

## Conventions

Every function outside `tests/` carries the five-field comment block (see
`.claude/agents/README.md`). A test enforces it, and
`python ml/tests/check_comments.py ml` checks it by hand.
