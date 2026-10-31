# backend

Everything that runs between the frontend and the models: the whiteboard
backend, and Nirmal's handbook blocks. The ML models themselves are in `ml/`.

```
backend/
├── README.md             this file
├── mcp_server/           B04 MCP interface: models and whiteboard as tools for AI agents   BUILT
├── whiteboard/           whiteboard backend: board, slash commands, local API, agent tools BUILT
├── local_backend/        B03 local Python backend: /api/v1 full handbook surface           BUILT
├── cloud_server/         B05 rate limiter + §8.3 auth + project/credits routes             BUILT
├── validator/            B11 deterministic diagram validator (library)                     BUILT
├── library_access/       B07 verified-library access façade                                BUILT
├── library_db/           B08 permanent verified library (sqlite dev, Postgres prod)        BUILT
├── unverified_db/        B13 temporary unverified-code database                            BUILT
├── project_ingestion/    B09 project ingestion with B10 stub                               BUILT
└── credits/              B16 contribution and credits backend                              BUILT
```

Nirmal owns every block above except B04 and the whiteboard (handbook §2.2).
Each folder has its own README with the handbook contracts it honours and
the open items that still block schema freeze.

## How the pieces connect

```
frontend (B01 desktop, B02 CLI)
      │ HTTP 127.0.0.1 /api/v1, §4.1 envelope
      ▼
local_backend (B03) ─── whiteboard (board + project files)
      │                        │
      │                 mcp_server (B04) ─── AI agents (Claude Code, Claude Desktop)
      │                        │ also serves the models (ml/serving)
      │ HTTPS, §8
      ▼
cloud_server (B05 rate limit + §8.3 auth)
      ├─► ml/serving/service (B06 recommender)
      ├─► project_ingestion (B09) ─► diagram_client stub (B10) ─► validator (B11)
      ├─► library_access (B07) ─► library_db (B08) ◄── credits (B16)
      └─► credits (B16) routes
```

B13 and B07's admission write path sit alongside B05 and talk only to B14
once Sourish ships it; see `scripts/flow_admit.py` for a dry-run that fakes
B12 and B14.

## Run the full stack

```sh
# development: no auth, in-memory DBs
python -m backend.cloud_server.app                               # 127.0.0.1:8500
python -m ml.serving.service.app                                 # B06 on 127.0.0.1:8600
python -m backend.local_backend.app                              # 127.0.0.1:8701

# with auth and a seeded library on disk
PLOVANE_LIBRARY_DB=./library.db python -m backend.library_db.load_seed --db ./library.db
PLOVANE_REQUIRE_AUTH=1 PLOVANE_LIBRARY_DB=./library.db \
  PLOVANE_IDENTITY_DB=./identity.db python -m backend.cloud_server.app
```

## Flow scripts

- `scripts/flow_recommend.py` hits B03 with a §5.1 body.
- `scripts/flow_project.py` submits §5.5 and reads §5.4.
- `scripts/flow_admit.py` walks B13 → faked B14 → B07 admit → B16 credit.
