# serving

Loads the models once and answers requests. The cloud server, the MCP server
and any Python caller all go through `engine.py`.

```
serving/
├── README.md       this file
├── API.md          the B06 contract for backend developers: input, output, errors
├── engine.py       loads the library, encoder and cross-encoder; search, file checks, reranking
├── registry.py     one entry per model: what it is, its weights, status and accuracy
├── llm.py          calls hosted LLMs (Claude, OpenAI) by API key
├── setup.py        prepares a machine: downloads and verifies weights, rebuilds the library
├── service/        the HTTP API (Flask)
│   ├── app.py      routes: /v1/recommend, /v1/feedback, /v1/check-file, /v1/find-helpers,
│   │               /v1/rerank, /v1/blocks/<id>, /v1/models, /healthz, /readyz
│   ├── contract.py handbook §8.1 request -> B06 request, and back to §8.2 (gap G2)
│   └── store.py    append-only log of served recommendations and feedback
├── .cache/         library embeddings, regenerated on demand (gitignored)
└── .logs/          served recommendations and feedback, JSONL (gitignored)
```

```sh
python -m ml.serving.setup                # once per machine
python -m ml.serving.service.app          # development server, 127.0.0.1:8600
gunicorn -w 2 -b 0.0.0.0:8600 'ml.serving.service.app:create_app()'   # production
```

Each gunicorn worker loads its own copy of the models (about 2 GB). Use sync
workers: the cross-encoder must not serve two threads at once.
