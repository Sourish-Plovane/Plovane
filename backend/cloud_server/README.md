# cloud_server (B05): built

**Owner:** Nirmal. **Status:** rate limiter, §8.1 forwarding, §8.3 auth,
§5.5 project submission and §15 contributions all built.

The Flask server in the cloud. Three jobs:

- **B05 rate limiter and auth.** Authenticates the local backend's requests
  (§8.3 bearer token on `/v1/*` endpoints when `PLOVANE_REQUIRE_AUTH=1`),
  rate-limits each user, forwards to the right upstream.
- **Project route.** `POST /v1/project` runs the B09 ingestion in-process and
  stores the §5.4 diagram.
- **Credits route.** `POST /v1/contributions` and `GET /v1/credits/<user_id>`
  back the handbook §15 payloads.

B07 verified-library access lives in `backend/library_access/`; this process
holds the façade and reads from B08 (sqlite in dev, Postgres+pgvector in prod).

## Contracts it follows

| Connection | Handbook | Status |
|---|---|---|
| B03 → B05 recommendation | §8.1 / §8.2 | built; forwarded unchanged to B06 |
| B03 → B05 login/register | §8.3 | built (OD-4 still open) |
| B05 → B06 recommender | gap G2 | `ml/serving/service/contract.py` is the mapping |
| B05 ↔ B09 project ingestion | §5.5 → §9.1 → §5.4 | built in-process (closes gap G8 for this path) |
| B16 ↔ B08 contribution/credits | §15 | built via `backend/credits` |

## Routes

`app.py` runs on `127.0.0.1:8500`:

| Route | Handbook |
|---|---|
| `GET /healthz` | liveness |
| `POST /v1/recommend` | §8.1 in, §8.2 out; forwarded to B06 |
| `POST /v1/project` | §5.5 in, `{submission_id, project_id, status: accepted}` out |
| `GET /v1/project/<id>/diagram` | §5.4 read |
| `POST /v1/contributions` | §15.1 in, §15.2 out |
| `GET /v1/credits/<user_id>` | balances per credit type |
| `POST /v1/auth/register` | §8.3 register |
| `POST /v1/auth/login` | §8.3 login |

Errors use `{"request_id", "error": {"code", "message"}}` with the §16.2
vocabulary (gap G9). `rate_limit.py` is a token bucket per authenticated
`user_id`, falling back to the peer address when the server runs with
`PLOVANE_REQUIRE_AUTH=0` for development.

```sh
python -m backend.cloud_server.app                               # 127.0.0.1:8500
python -m ml.serving.service.app                                 # B06 on 127.0.0.1:8600
PLOVANE_REQUIRE_AUTH=1 python -m backend.cloud_server.app        # require §8.3 token
```

## Identity + sessions

`identity.py` is a sqlite-backed store (env `PLOVANE_IDENTITY_DB`). Passwords
are PBKDF2-HMAC-SHA256; Argon2id is the recommended prod swap. Session tokens
are random 32-byte URL-safe strings with a 24-hour TTL by default.

`auth.py` is the request-time guard: `/healthz` and `/v1/auth/*` stay public;
everything else needs an `Authorization: Bearer <token>` header when
`PLOVANE_REQUIRE_AUTH=1`.

## Open items

- **OD-4:** §8.3 schemas and TTL policy still need engineering sign-off before
  the auth endpoints are frozen for production.
- Multi-worker deploys need a shared rate-limit store (Redis). Documented in
  `rate_limit.py` already; swap in before scaling out.
