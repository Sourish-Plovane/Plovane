# unverified_db (B13): built

**Owner:** Nirmal. **Status:** sqlite store for §11.3 submissions and §13
revisions.

The short-lived store that sits between novelty detection (B12) and
verification (B14). Blocks arrive with `submission_id` and `block_id`, run
through B14, and either move to B08 (admitted) or get a revision from B15.

## Contracts it follows

| Connection | Handbook |
|---|---|
| Novelty detection (B12) → B13 submission | §11.3 payload, §11.3 ack (`pending_verification`) |
| B13 ↔ Verification (B14) | §12.1 in, §12.2 out |
| B15 revision → B13 | §13.1 revised block, §13.2 reverification result |

## What is built

- `engine.py`: sqlite3 connection factory + schema (`unverified_submission`,
  `unverified_block`). `PLOVANE_UNVERIFIED_DB` chooses the path.
- `repo.py`:
  - `submit(§11.3)` → `{submission_id, status: pending_verification}`
  - `pull_block(submission_id, block_id)` → §12.1 verification input
  - `record_result(§12.2)` → sets status to `passed` or `failed`
  - `record_revision(§13.1)` → bumps revision, resets status to pending
  - `drop_block` / `drop_submission` after B14 promotes blocks to B08

## Related

See `scripts/flow_admit.py` for the end-to-end walk from §11.3 through B14
(faked) and B07 admission into B16 credits.
