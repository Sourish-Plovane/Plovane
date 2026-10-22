# Handbook v0.2 — proposed ML-side changes

For the next revision of the Inter-Block Data Transfer Handbook, after
building B06 (the BM12 recommendation engine) against the v0.1 draft. These
are the fields the ML code already produces and the ones B06 needs from B07
to rank. Everything here is implemented in `ml.schemas` and
`ml.schemas_library`, with 21 passing tests pinning the wire shapes.

Scope: the §5.2 response, the §6 payload (handbook G3), one new §4.4
operational-log section, two small clarifications to §5.1 and §16, and the
G6 `accepted` rename.

Nothing proposed changes a field that is already established. §3.5 rule 1
holds: adding an optional field does not break existing consumers.

---

## 1. §5.2 — recommendation response: new required and optional fields

### Why this matters

The current §5.2 ``code_blocks[]`` element has six required fields and no
others. A client reading only that cannot:

- distinguish a high-confidence recommendation from a low-confidence one,
  because there is no score;
- distinguish "nothing matched well enough" from "the cutoff is not applied
  yet, these were the best we had";
- show attribution for pasted code, because licence and source URL do not
  travel;
- render a function signature before the user commits to pasting.

B06 produces all four signals today. They should ride on the response
instead of being dropped at the §5.2 boundary.

### Proposed required additions to `code_blocks[]` elements

| Field | Type | Meaning |
| --- | --- | --- |
| `score` | number in [0, 1] | Model's confidence in this block for this request. |
| `score_is_calibrated` | boolean | Whether `score` is a probability or just an ordering. See §4.1 extension below. |
| `license` | string | SPDX identifier (`MIT`, `BSD-3-Clause`, `Apache-2.0`, …). Required so a pasted block carries its licence with it. |

### Proposed optional additions

| Field | Type | Meaning |
| --- | --- | --- |
| `signature` | string | Helper calling signature. Omitted on templates and bundles. |
| `source_url` | string | Repository URL for attribution. |
| `source_permalink` | string | SHA-pinned link to the exact lines. |

### Proposed required addition to the §5.2 response envelope `data` object

| Field | Type | Meaning |
| --- | --- | --- |
| `cutoff_enforced` | boolean | True when the relevance cutoff was applied. False when the pipeline is in uncalibrated mode and the cutoff could not be meaningfully placed. |
| `reason` | string, optional | When `recommendations` is empty, why. Two established values: `no entry above the relevance cutoff`, `scores are uncalibrated: no cutoff was applied`. |

### Example

```json
{
  "ok": true,
  "data": {
    "query_id": "q_8f23",
    "cutoff_enforced": true,
    "recommendations": [
      {
        "block_id": "pydantic:validate_arguments",
        "name": "validate_arguments",
        "language": "python",
        "code": "def validate_arguments(func): ...",
        "comments": "Validates the arguments passed to a function.",
        "dependencies": ["pydantic"],
        "score": 0.87,
        "score_is_calibrated": true,
        "license": "MIT",
        "signature": "(func)",
        "source_url": "https://github.com/pydantic/pydantic",
        "source_permalink": "https://github.com/pydantic/pydantic/blob/3eca569/.../fields.py#L112-L156"
      }
    ]
  },
  "error": null
}
```

Empty, cutoff-enforced case:

```json
{
  "ok": true,
  "data": {
    "query_id": "q_8f23",
    "cutoff_enforced": true,
    "recommendations": [],
    "reason": "no entry above the relevance cutoff"
  },
  "error": null
}
```

Empty, uncalibrated case (what B06 returns today):

```json
{
  "ok": true,
  "data": {
    "query_id": "q_8f23",
    "cutoff_enforced": false,
    "recommendations": [],
    "reason": "scores are uncalibrated: no cutoff was applied"
  },
  "error": null
}
```

An empty array is still a successful operation per §16.3 ("domain outcomes
must be represented as operation results when the operation itself
completed successfully"), so `ok: true` and no `error`.

---

## 2. §6 — Verified code-library access payload (closes G3)

### Why this matters

The v0.1 contract register says "connections 6 and 7 only reuse the
code-block shape". It is not enough. B06 cannot rank without usage counts,
mutation score, dependency-compatibility and the retrieval scores — all
fields the §5.2 code block omits. Either B07 serves fat entries to B06 or
B06 keeps a shadow index and two services drift.

Design: thin B07, thick B06. B07 does the keyword and vector search and
returns candidates with every signal present. The ranking stays in B06.

### Two operations

**`search`** — the hot path. One call per recommendation request.

Request:

```json
{
  "keyword_text": "merge two config dictionaries",
  "query_vector": [0.13, -0.08, "... 768 floats total"],
  "limit": 1000,
  "drop_in_only": false,
  "languages": ["python"],
  "entry_types": ["helper", "template"],
  "categories": [],
  "frameworks": [],
  "installed_dependencies": ["pandas==2.1", "torch==2.1"],
  "exclude_block_ids": []
}
```

All filter fields are optional; an omitted filter does not restrict.
`installed_dependencies` accepts both `name` and `name==version` so B07 can
compute `dependency_compat` server-side.

Response:

```json
{
  "ok": true,
  "data": {
    "blocks": [{
      "block_id": "...",
      "name": "...",
      "type": "helper",
      "language": "python",
      "category": "config",
      "description": "...",
      "code": "...",
      "license": "MIT",
      "signature": "...",
      "dependencies": [],
      "source_url": "...",
      "source_permalink": "...",
      "ranking_signals": {
        "bm25_score": 12.4,
        "bm25_rank": 3,
        "cosine_score": 0.71,
        "vector_rank": 2,
        "rrf_score": 0.08,
        "rrf_rank": 2,
        "dependency_compat": "compatible"
      },
      "trust_signals": {
        "usage_count": 47,
        "impressions": 200,
        "accepts": 140,
        "mutation_score": 0.78,
        "last_retest_at": "2026-10-01T12:00:00Z"
      }
    }],
    "keyword_hits": 850,
    "vector_hits": 740,
    "shortfall": false
  },
  "error": null
}
```

`shortfall: true` is logged by B06 because it almost always means HNSW
found its vector neighbours before a filter removed them — a known class
of recall problem.

Sub-objects for `ranking_signals` and `trust_signals` keep the top level
of the block legible and let B06 drop either slice without filtering keys.
The §5.2 projection drops both sub-objects entirely.

**`get_blocks`** — the second pass. For a cross-encoder re-reading the top
100, a response resolve-by-id, or an audit lookup.

Request:

```json
{
  "block_ids": ["blk_001", "blk_002"],
  "projection": "ranking"
}
```

`projection` is one of:

- `ranking` — every signal (as in `search`)
- `text` — just `block_id`, `code`, `description`, `signature`, `license`
- `serving` — exactly the §5.2 block fields plus `source_url`

B06 picks `text` when it only needs the strings to rescore and already has
the signals from the first pass.

### What this does not change

B07's internal storage schema, retrieval algorithm, index technology and
staleness policy. The handbook §1.1 scope. These are B07's and B08's
business.

---

## 3. §4.4 — Operational log contract (new section)

### Why this matters

The handbook v0.1 covers transport errors (§4.1, §16) but says nothing
about operational logs. Without a named log contract, "the user got a bad
recommendation on Tuesday" cannot be traced to the model version that
served it, so a regression cannot be rolled back.

### Proposed contract

Every component that serves a request records a log entry with at least:

| Field | Type | Meaning |
| --- | --- | --- |
| `query_id` | string | The request id, matching the one echoed to the caller. |
| `timestamp` | ISO 8601 UTC | When the response was produced. |
| `model_versions` | object | Per-component version string (`stage1`, `cross_encoder`, `stage3`, `calibrator`, `retrieval_embedding`). |
| `request_summary` | object | The query text, project context fields that affected the result. |
| `candidates_retrieved` | integer | Count before ranking. |
| `returned_block_ids` | array of strings | What was sent back. |
| `scores` | array of numbers | Score per returned block, in the same order. |
| `cutoff` | number | The cutoff value that was configured. |
| `cutoff_enforced` | boolean | Whether that cutoff could actually be applied. |

The log entry is never part of the response payload. It is persisted
server-side with a retention policy B06, B14 and B16 each define
independently.

---

## 4. G6 — `accepted` overload

The current draft uses `status: "accepted"` for three distinct outcomes:

| Section | Meaning today | Proposed rename |
| --- | --- | --- |
| §5.5 project submission response | Queued for processing | `queued` |
| §14.2 library admission | Admitted to the permanent library | `admitted` |
| §15.2 contribution outcome | Contribution recorded | `recorded` |

`if resp.status == "accepted"` reads correctly against the draft and
means something different depending on which endpoint produced it. Rename
so a mistaken switch is caught by the type checker rather than at
runtime.

---

## 5. §5.1 — `tech_stack` element shape (small clarification)

The draft says `tech_stack` is "array of strings". The §6 shape above
accepts both `name` and `name==version`. §5.1 should say the same, so the
request from B01 and the request from B06 to B07 use the same vocabulary.
B06 handles both today via `classify_dependency_compat`.

---

## 6. §16 — `RATE_LIMITED` error code

B05 is a rate limiter with no rate-limit code in the §16.2 vocabulary.
Clients cannot distinguish "slow down, retry in 30 seconds" from "stop
forever" today. Add:

| Code | Meaning |
| --- | --- |
| `RATE_LIMITED` | The caller has made too many requests and should retry after an interval. The `message` should include the retry hint (`"retry after 30s"` or an epoch timestamp). |

---

## Not proposed, worth flagging

**§10 validator issue codes and §16 error codes should be enumerated,
not promised stable by example.** Both sections currently say the codes
must be documented and remain stable, but only one or two are named.
Enumerate before freezing.

**§13 revision has no stop condition.** `revision` is a positive integer,
but a block that fails verification repeatedly has no abandonment path.
Suggest a `max_revisions` setting or an `abandoned` terminal status.

**§7.2 does not echo `request_id` while §8.2 does.** Symmetric or not,
pick one. Asymmetric means every MCP client has to track correlation
outside the message.

Both are owner-decisions (G10 for the first, out of scope for the ML side
for the others). Flagged, not drafted.

---

## Verification

21 new tests in `ml/tests/test_handbook_contract.py` pin every shape above.
Running `python -m pytest ml/tests/test_handbook_contract.py -q` executes
them in under 50 ms.

The schemas live in `ml/schemas.py` (SlimCard, Response) and
`ml/schemas_library.py` (LibrarySearchRequest, LibraryBlock,
LibrarySearchResponse, LibraryGetBlocksRequest). The pipeline's
`recommend()` emits the new envelope via `Response.to_handbook_response()`.
