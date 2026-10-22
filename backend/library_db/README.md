# library_db (B08): skeleton

**Owner:** Nirmal. **Status:** not started; this README is the brief.

The permanent store of verified code blocks: what the recommender searches,
and what verification admits new blocks into.

## Contracts it must follow

| Connection | Handbook |
|---|---|
| Verification (B14) → B08 admission | §14.1 payload, §14.2 acknowledgement (`accepted` means admitted, never just received) |
| Credits (B16) ↔ B08 | §15 |
| Library access (B07) ↔ B08 | §5.2 code-block shape (gap G3 for lookups) |

## What already exists to build on

- **The fields an entry needs** are `ml.schemas.Entry`: id, name, language,
  code, description, licence, source permalink, dependencies, usage counters
  and embedding. Today `ml/data/entries.jsonl` holds 378 of them.
- **Postgres with pgvector is the planned store.** The SQL already exists:
  - `ml/retrieval/vector.py` has the HNSW index (`CREATE_HNSW_INDEX_SQL`) and `pgvector_session_sql`.
  - `ml/retrieval/filters.py` has `FilterSpec.where_clause()`, the parameterised `WHERE` for a request.
- **Tables to plan for:**
  - entries and their embeddings
  - recommendation logs and feedback (today JSONL in `ml/serving/.logs/`, written by `ml/serving/service/store.py`)
  - submissions waiting for verification (B13 is a separate temporary store)
