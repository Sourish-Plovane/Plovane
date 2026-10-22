# cloud_server (B05, B07): skeleton

**Owner:** Nirmal. **Status:** not started; this README is the brief.

The Flask server in the cloud. It has two jobs:

- **B05 rate limiter and auth.** It authenticates the local backend's
  requests and limits each user, then forwards them. Login and registration
  payloads are in §8.3.
- **B07 verified-library access.** It reads and writes library blocks in
  B08 for the recommender and for verification.

## Contracts it must follow

| Connection | Handbook | Status |
|---|---|---|
| B03 → B05 recommendation | §8.1 request, §8.2 response | proposed |
| B03 → B05 login, registration | §8.3 | proposed; the session mechanism needs review |
| B05 → B06 recommender | none in the handbook (**gap G2**) | `ml/serving/service/contract.py` proposes the mapping |
| B06 ↔ B07, B07 ↔ B08 | §5.2 code-block shape only (**gap G3**: no lookup payloads) | open |
| B05 → B09 project ingestion | route not specified (**gap G8**) | open |

## What already exists to build on

- **B06 is built.** `POST /v1/recommend` takes §8.1 and returns §8.2, and
  `POST /v1/feedback` records accept and reject (`ml/serving/service/app.py`).
  B05 forwards to it after its auth and rate checks.

## Suggested shape

Store rate-limit counters (a token bucket per `user_id`) in Redis. Hash
passwords with argon2. Session tokens follow §8.3. Use one gunicorn worker
per CPU for B05, and run B06 as its own service, because each B06 worker
holds about 2 GB of models.
