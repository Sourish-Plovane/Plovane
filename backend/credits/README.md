# credits (B16): built

**Owner:** Nirmal. **Status:** sqlite-backed contribution and credit store.
OD-3 (credit types/units) stays open; v1 awards one `compute` credit per
admitted block as a placeholder, matching the handbook's example.

Records every contribution (manual submission or completed project) and the
credit each one earns once it is accepted into the library.

## Contracts it follows

| Connection | Handbook |
|---|---|
| Contribution submission | §15.1 (`source`: `manual_submission` or `completed_project`) |
| Contribution outcome | §15.2 (`status: accepted`, `credit_outcome.{status, credit_type, amount}`) |
| B16 ↔ B08 | §15 |

## What is built

- `engine.py`: sqlite3 schema for `contribution`, `contribution_block`, and
  `credit_award`. `PLOVANE_CREDITS_DB` chooses the path.
- `repo.py`:
  - `record(§15.1)` → §15.2 outcome with `credit_outcome.status: pending`
  - `mark_awarded(contribution_id, credit_type)` → §15.2 outcome with `awarded`
  - `balances(user_id)` → one row per credit type for the user
  - `contribution_from_admission(user_id, block_ids)` builds a
    `completed_project` contribution for the B14→B07→B16 trigger (gap G7)

## Open items

- **OD-3:** full credit vocabulary. v1 only accepts `compute`.
- **G7:** the trigger from B14's admission into B16 lives in the flow script
  `scripts/flow_admit.py` for now. A dedicated hook belongs in B14 once it is
  built.
- **G6:** `accepted` is overloaded (§5.5, §14.2, §15.2). The schema honours
  the handbook's use; a distinct value would need a handbook revision first.

## Related

`/save` in the whiteboard queues blocks for the library in
`.plovane/submissions.jsonl`. Those submissions are the natural input to a
`manual_submission` contribution.
