# B06 recommendation engine: input and output

For the backend that calls B06. Every example below is real output from the
`Sourish` branch with the trained Stage 2 cross-encoder loaded.

There are two ways to call B06:

- **Over HTTP:** `ml/serving/service/app.py` is a Flask service.
  `POST /v1/recommend` takes the handbook's §8.1 body and returns §8.2, and
  `POST /v1/feedback` records accept and reject. The full route list and the
  §8.1 to B06 field mapping (gap G2) are in `ml/serving/service/contract.py`.
  Start it with `python -m ml.serving.service.app`.
- **As a Python library:** build one `RecommendationPipeline` at startup and
  call `recommend()` per request, as shown below.

The JSON shapes below are the library's. The HTTP service returns the same
blocks inside §8.2.

## Setup (once per server)

```sh
pip install torch transformers numpy huggingface_hub flask
read -s "GITHUB_TOKEN?Paste GitHub token: "; export GITHUB_TOKEN; echo
python -m ml.serving.setup
```

The script downloads the base encoder and the trained weights (about 1 GB
on disk), checks both SHA-256 hashes, and exits 0 only if the model loads and
ranks a known-right answer above a known-wrong one. The machine needs about
4 GB of RAM.

## Calling it

```python
from ml.schemas import Request
from ml.ranking.cross_encoder import load_cross_encoder
from ml.ranking.pipeline import RecommendationPipeline, ModelVersions
from ml.ranking.stage1 import LinearFeatureRanker
from ml.ranking.stage3 import FinalRanker, LinearFinalRanker

# At startup. Loading takes about 5-30 s, so never do it per request.
pipeline = RecommendationPipeline(
    retrieval=retrieval_service,          # over the library (B07/B08); see main() in ml/training/train_cross_encoder.py
    stage1_ranker=LinearFeatureRanker(),
    final_ranker=FinalRanker(model=LinearFinalRanker(), version="hand"),
    cross_encoder=load_cross_encoder("ml/weights/cross-encoder-stage2/cross_encoder_final.pt"),
    versions=ModelVersions(cross_encoder="cross-encoder-20261017T142721Z"),
)

# Per request.
response, log = pipeline.recommend(Request.from_dict(request_json))
return response.to_handbook_response()   # send this to the caller
store(log)                               # keep this server-side, never send it
```

`cross_encoder=None` also works and needs no torch. You get retrieval order
instead, which is less accurate (see the numbers at the end).

## Input

```json
{
  "query": "join a base url and a relative path",
  "project_context": {
    "languages": ["javascript"],
    "framework": null,
    "category": null,
    "dependencies": ["axios"],
    "file_summary": null
  },
  "k": 2,
  "requester_id": "user_123",
  "drop_in_only": false
}
```

| Field | Type | Required | Default | Meaning |
|---|---|---|---|---|
| `query` | string | **yes** | | What the user or agent wants the code to do, in plain words. |
| `project_context` | object | no | `{}` | What's known about the project. Every field is optional; leaving one out lowers accuracy but breaks nothing. |
| `project_context.languages` | string[] | no | `[]` | Languages of the project, lowercase, e.g. `"python"`, `"javascript"`, `"typescript"`. **Send this whenever you know it**: results are filtered and the reranker is conditioned on it. |
| `project_context.framework` | string \| null | no | `null` | e.g. `"flask"`, `"react"`. |
| `project_context.category` | string \| null | no | `null` | Library category, if the caller knows it. |
| `project_context.dependencies` | string[] | no | `[]` | Packages already installed. Used to prefer code that needs nothing new. |
| `project_context.file_summary` | string \| null | no | `null` | Short description of the file being edited. |
| `k` | int | no | `3` | Maximum number of results. |
| `requester_id` | string \| null | no | `null` | Stable user id. Used for personalisation features and logged. |
| `drop_in_only` | bool | no | `false` | `true` returns only code that needs no new dependencies. |

A missing `query` raises `KeyError` in `Request.from_dict`. Validate it
before you call.

## Output

The §4.1 envelope with §5.2 code blocks:

```json
{
  "ok": true,
  "data": {
    "query_id": "q_6f28ca9edc185865",
    "recommendations": [
      {
        "block_id": "axios_axios:combineURLs",
        "name": "combineURLs",
        "language": "javascript",
        "code": "export default function combineURLs(baseURL, relativeURL) {\n  ...\n}",
        "comments": "Creates a new URL by combining the specified URLs",
        "dependencies": [],
        "score": 0.7311,
        "score_is_calibrated": false,
        "license": "MIT",
        "signature": "(baseURL, relativeURL)",
        "source_url": "https://github.com/axios/axios",
        "source_permalink": "https://github.com/axios/axios/blob/cf744d22.../lib/helpers/combineURLs.js#L11-L23"
      }
    ],
    "cutoff_enforced": false,
    "reason": "scores are uncalibrated: no relevance cutoff was applied, so these are the best available matches rather than matches known to be good"
  },
  "error": null
}
```

`data`:

| Field | Type | Always present | Meaning |
|---|---|---|---|
| `query_id` | string | yes | Unique id for this response, `q_` plus 16 hex characters. **Send it back with the user's accept or reject**; it's how usage becomes training data. |
| `recommendations` | block[] | yes | Best first, at most `k`. May be empty. |
| `cutoff_enforced` | bool | yes | `false` means low-quality matches were *not* filtered out, so treat results as suggestions. It is `false` today, until there is accept/reject data to calibrate on. |
| `reason` | string | only when there's something to explain | Why the list is empty, or why it wasn't filtered. Show it or log it; don't parse it. |

Each block in `recommendations`:

| Field | Type | Always present | Meaning |
|---|---|---|---|
| `block_id` | string | yes | Library id, `<owner>_<repo>:<name>`. |
| `name` | string | yes | Function or template name. |
| `language` | string | yes | Language of the code. |
| `code` | string | yes | The code to paste. |
| `comments` | string | yes | One-line description. |
| `dependencies` | string[] | yes | Packages the code needs. |
| `score` | float | yes | Higher is better. **Today it only orders results within one response.** It is not a probability and can't be compared across queries while `score_is_calibrated` is `false`. |
| `score_is_calibrated` | bool | yes | `true` only once a calibrator is fitted; then `score` is the probability the user accepts. |
| `license` | string | yes | SPDX id, e.g. `MIT`. **Must travel with pasted code** (attribution requirement). |
| `signature` | string | helpers only | Parameter list, to show before pasting. |
| `source_url` | string | when known | Upstream repository. |
| `source_permalink` | string | when known | Exact file and lines at the pinned commit. |

### Empty results

Still a success: `ok: true`, `recommendations: []`, with `reason` set (for
example `"no entry above the relevance cutoff"`). An empty list is never an
error. Tell the user nothing good matched and let them write the code.

### Errors

B06 doesn't build `ok: false` envelopes itself. A Python exception from
`recommend()` (bad input, model not loaded) is the backend's to catch and
turn into the §4.1 error envelope.

## The log (server-side only)

`recommend()` also returns a `RecommendationLog`. Store it and never send it
to the client:

```json
{
  "query_id": "q_6f28ca9edc185865",
  "model_versions": {"retrieval_embedding": "unversioned", "stage1": "unversioned",
                     "cross_encoder": "cross-encoder-20261017T142721Z",
                     "stage3": "unversioned", "calibrator": "none"},
  "query": "join a base url and a relative path",
  "candidates_retrieved": 134,
  "returned": ["axios_axios:combineURLs", "axios_axios:getUri"],
  "scores": [0.7311, 0.7281],
  "cutoff": 0.5,
  "cutoff_enforced": false,
  "created_at": "2026-10-18 04:25:28+00:00"
}
```

Join it with the user's accept or reject by `query_id`. That pair is the
data that trains the rankers and fits the calibrator. Without it the scores
can never become probabilities.

## Speed and accuracy

| Setup | Time per request | @1 | @3 | @10 |
|---|---|---|---|---|
| GPU, 100 candidates reranked | under 1 s | 69.7% | 88.3% | 96.0% |
| CPU, 100 candidates reranked | 10-30 s | 69.7% | 88.3% | 96.0% |
| CPU, 20 candidates reranked | about 2-6 s | 67.1% | 83.1% | 88.3% |
| No cross-encoder (`None`) | fast | 52.0% | 69.7% | 81.4% |

"@1" means the right answer was first. Measured on 350 held-out queries
(issue 35). **Not certified:** the queries were written by an assistant, not
real users (issue 19).

Don't share one pipeline between threads: scoring switches the model's
train/eval mode while it runs. Use one pipeline per worker process.
