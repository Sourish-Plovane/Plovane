# library_db (B08): built

**Owner:** Nirmal. **Status:** sqlite implementation of the permanent
verified library. Postgres+pgvector swap is a prod concern.

The single source of truth for admitted §5.2 code blocks. B07 is the only
module that reads or writes it; B14 admits new blocks through B07.

## Contracts it follows

| Connection | Handbook |
|---|---|
| Verification (B14) → B08 admission | §14.1 payload, §14.2 acknowledgement (`accepted` means admitted, never just received) |
| Credits (B16) ↔ B08 | §15 |
| Library access (B07) ↔ B08 | §5.2 code-block shape (gap G3 for lookup payload) |

## What is built

- `engine.py`: sqlite3 connection factory with the schema applied on first
  connect. `PLOVANE_LIBRARY_DB` chooses the path (default `:memory:`).
- `repo.py`: `admit`, `get`, `search`, `bump_counter`, `bulk_insert`, `count`.
  The schema includes `block_id`, `name`, `language`, `code`, `comments`,
  `dependencies` (JSON array), the admission stamp (`submission_id`,
  `revision`, `verified_at`) and usage counters.
- `load_seed.py`: one-off loader for `ml/data/entries.jsonl`.

```sh
python -m backend.library_db.load_seed --db ./library.db          # seed B08
```

## Prod swap

- The pgvector SQL lives in `ml/retrieval/vector.py` (`CREATE_HNSW_INDEX_SQL`,
  `pgvector_session_sql`). The migration is: add `embedding vector(768)`,
  swap `dependencies` from JSON to `text[]`, keep the rest.
- Feedback counters currently live in `ml/serving/.logs/*.jsonl`
  (`ml/serving/service/store.py`). A later migration moves them onto
  `library_block.usage_accepted` / `.usage_rejected`.
