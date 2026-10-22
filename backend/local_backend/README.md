# local_backend (B03): skeleton

**Owner:** Nirmal. **Status:** not started; this README is the brief.

The Python process that runs on the user's machine. The desktop app (B01),
the CLI (B02) and the MCP interface (B04) talk only to it. It talks to the
cloud server for anything that needs the library or an account.

## Contracts it must follow

| Connection | Transport | Handbook |
|---|---|---|
| Desktop GUI ↔ B03 | HTTP on `127.0.0.1`, prefix `/api/v1/`, `{"ok", "data", "error"}` envelope | §4.1, §5 |
| CLI ↔ B03 | not finalized; reuses the §5 operation schemas | §6 |
| MCP ↔ B03 | `{"operation", "input"}` in, §4.1 envelope out | §7 |
| B03 ↔ cloud server | HTTPS, JSON: recommendation §8.1/§8.2, login and registration §8.3 | §4.2, §8 |

Operations the app needs (§5): recommendation request/response (§5.1,
§5.2), diagram request/response (§5.3, §5.4), project submission (§5.5).
Error codes come from §16.1.

## What already exists to build on

- `backend/whiteboard/server.py` already serves `/api/v1/whiteboard/...` on
  127.0.0.1 in the §4.1 envelope. B03 can mount its Flask app, or proxy to it.
- `ml/serving/service/` is the B06 HTTP service, and `contract.py` there maps
  the §8.1 body onto it.

## Open decisions

- **G9:** the cloud server has no error format. B03 has to map cloud errors
  into §16.1 codes for the GUI.
- Whether B03 and the whiteboard API run as one process or two.
