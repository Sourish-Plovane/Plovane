# local_backend (B03): built

**Owner:** Nirmal. **Status:** full handbook surface built. §7 MCP router,
§5.5 project submission, §5.4 diagram read, §8.3 register and login are all
served. Session mechanism is wired but still OD-4 at the schema level.

The Python process that runs on the user's machine. The desktop app (B01),
the CLI (B02) and the MCP interface (B04) talk only to it. It talks to the
cloud server for anything that needs the library or an account.

## Contracts it follows

| Connection | Transport | Handbook |
|---|---|---|
| Desktop GUI ↔ B03 | HTTP on `127.0.0.1`, prefix `/api/v1/`, `{"ok", "data", "error"}` envelope | §4.1, §5 |
| CLI ↔ B03 | reuses the §5 operation schemas | §6 |
| MCP ↔ B03 | `{"operation", "input"}` in, §4.1 envelope out, through `POST /api/v1/mcp` | §7 |
| B03 ↔ cloud server | HTTPS JSON: §8.1/§8.2, §5.5 submission, §5.4 read, §8.3 register and login | §4.2, §8 |

## Routes

`app.py` serves the §4.1 envelope on `127.0.0.1:8701`:

| Route | Handbook |
|---|---|
| `GET /api/v1/health` | §4.1 |
| `POST /api/v1/recommendations` | §5.1 in, §8.1 to the cloud, §8.2 back, §5.2 out |
| `POST /api/v1/projects` | §5.5 submission; forwarded to the cloud's `/v1/project` |
| `GET /api/v1/projects/<project_id>/diagram` | §5.4 read |
| `POST /api/v1/auth/register` | §8.3 registration (OD-4) |
| `POST /api/v1/auth/login` | §8.3 login (OD-4); the session_token is stored on the cloud client |
| `POST /api/v1/mcp` | §7 operation+input router |

`contracts.py` holds every validator and the response trimmers with no Flask
import, so it is testable alone. `cloud.py` is the HTTPS client; `mcp.py` is
the §7 operation dispatcher. Cloud errors are mapped onto §16.2 (gap G9).

```sh
python -m backend.local_backend.app                              # 127.0.0.1:8701
PLOVANE_CLOUD_URL=http://127.0.0.1:8500 python -m backend.local_backend.app
```

Caller identity: the `X-Plovane-User` header, then `PLOVANE_USER`, then
`"local"`. Once a user logs in through `/api/v1/auth/login`, the session
token is kept on the cloud client and sent as `Authorization: Bearer` on
subsequent cloud calls.

## Related

- `backend/whiteboard/server.py` serves `/api/v1/whiteboard/...` on 127.0.0.1
  in the §4.1 envelope. The two processes stay separate for now.
- `ml/serving/service/` is the B06 HTTP service; `contract.py` there maps
  §8.1 onto the pipeline.

## Open items

- **OD-4:** §8.3 session mechanics still need engineering review before freeze.
- **Connection 3:** today B04 (`backend/mcp_server`) runs peer to B06 over
  stdio. If the team keeps it that way, the new `/api/v1/mcp` route is still
  useful for other MCP clients.
