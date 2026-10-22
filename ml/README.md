# ml

The model code, as a Python package: everything that finds, scores and ranks
library code. `ml_models/` runs it, and `tests/` checks it.

```
ml/
├── schemas.py        shared data types: Request, Entry, Candidate, Response
├── config.py         settings and constants
├── ingest/           turns source repositories into library entries; finds helpers in a file
├── retrieval/        keyword and vector search over the library, fused (bi-encoder lives here)
├── ranking/          Stage 1, Stage 2 (cross-encoder) and Stage 3 rankers and their training
├── reuse/            decides whether a function is worth saving: duplicates, reusability score
├── evaluation/       metrics, gold sets and certification gates
└── templates/        whole-file templates and their recommender
```
