# seed

The inputs the library and training are built from.

| File | In git? | What it is |
|---|---|---|
| `sources.json` | yes | The repositories and pinned commits the library is harvested from |
| `queries.jsonl` | yes | Hand-written training queries, each naming its correct entry |
| `entries.jsonl` | no (generated) | The library itself; rebuild with `python -m ml_models.setup` |
