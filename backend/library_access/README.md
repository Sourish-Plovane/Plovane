# library_access (B07): built

**Owner:** Nirmal. **Status:** façade over B08. One admitter, one reader.

Thin layer in front of B08 so B06 and B14 both go through one API. The
module enforces §14.1's requirement that admission carry a passed
verification, and returns §14.2 (`{block_id, status: "accepted"}`) on success.

## Contracts it follows

| Connection | Handbook |
|---|---|
| B06 → B07 candidate lookup | §5.2 code-block shape (gap G3; language + name filter for now) |
| B14 → B07 admission | §14.1 in, §14.2 out |
| B07 ↔ B08 | §5.2 |

## What is built

`access.py` exports `LibraryAccess`:

| Method | Handbook |
|---|---|
| `admit(payload)` | §14.1 → §14.2 |
| `get(block_id)` | §5.2 |
| `search_candidates(language=, name_like=, limit=)` | §5.2 |
| `record_feedback(block_id, outcome)` | bumps `usage_accepted` / `usage_rejected` |

## Related

- `ml/retrieval/filters.py:FilterSpec` is the richer filter language used by
  B06's recommender pipeline. When B07 grows a vector search path (prod), it
  will reuse `FilterSpec` so both sides share one grammar.
