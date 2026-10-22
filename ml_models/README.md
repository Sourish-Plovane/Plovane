# ML models

Everything needed to **run** Plovane's models: loading them, serving them over
HTTP, the hosted-LLM bridge, and the trained artefacts. The model code itself
(encoders, rankers, the code extractor) lives in `ml/`; large weight files
live in `weights/` (downloaded, not in git).

## Layout

```
ml_models/
├── README.md          this file
├── __init__.py
├── registry.py        one entry per model: what it is, its weights, status and measured accuracy
├── engine.py          loads the models once and answers every request (search, file checks, reranking)
├── llm.py             calls hosted LLMs (Claude, OpenAI) by API key
├── setup.py           prepares a machine: downloads and verifies weights, rebuilds the library
├── service/           the HTTP backend (Flask), what the cloud server runs
│   ├── __init__.py
│   ├── app.py         routes: /v1/recommend, /v1/feedback, /v1/check-file, /v1/blocks, /healthz, /readyz
│   ├── contract.py    turns the handbook's §8.1 request into a B06 request, and back into §8.2
│   └── store.py       append-only log of served recommendations and user feedback
├── trained/           trained ranker artefacts and why none is certified yet
│   ├── README.md
│   ├── stage1_retrieval_fusion.txt
│   └── stage3_final.txt
└── .cache/            library embeddings, regenerated on demand (gitignored)
```

## The models

| Model | What it does | Trained? | Where |
|---|---|---|---|
| Bi-encoder (UniXcoder) | Turns queries and code into vectors for search | Pretrained, not fine-tuned | `ml/retrieval/bi_encoder.py`, `weights/unixcoder-base` |
| Cross-encoder (Stage 2) | Reads query and code together and scores the match | Yes, 126M params, issue 35 | `ml/ranking/cross_encoder.py`, GitHub release |
| Stage 1 / Stage 3 rankers | Blend retrieval and usage signals into the final order | Hand weights serve; boosters in `trained/` are uncertified | `ml/ranking/` |
| Helper finder | Lists the standalone functions in a file | Rule-based, no training | `ml/ingest/find_helpers.py` |
| Hosted LLMs | Claude or OpenAI, for prompts that need generation | Not ours | `llm.py` |

`python -m ml_models.registry` prints the same table with live status.

## Run it

```sh
read -s "GITHUB_TOKEN?Paste GitHub token: "; export GITHUB_TOKEN; echo
python -m ml_models.setup                 # once: weights, library, embedding cache
python -m ml_models.service.app           # HTTP backend on 127.0.0.1:8600
python -m mcp_server                      # or: the same models as MCP tools
```

Measured on 350 held-out queries: the right helper comes first 69.7% of the
time and is in the top 3 88.3% of the time. These numbers are not certified,
because the queries were written by an assistant rather than real users
(issue 19).
