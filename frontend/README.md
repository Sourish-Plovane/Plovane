# Frontend

The parts people use directly: the desktop app (B01) and the command-line
tool (B02). Owner: Aarav. This layout is a starting proposal, so change it
as the code arrives.

```
frontend/
├── README.md     this file
├── desktop/      B01 desktop GUI
└── cli/          B02 command-line interface
```

## What the frontend talks to

The local backend (B03), over HTTP on `127.0.0.1` with the prefix `/api/v1/`,
using the handbook's §4.1 envelope (`{"ok": ..., "data": ..., "error": ...}`).
To get recommendations, the backend calls the ML service:

| Need | Endpoint | Details |
|---|---|---|
| Run a whiteboard command | `POST /api/v1/whiteboard/command` with `{"command": "/block add service auth"}` | `whiteboard/README.md` |
| Draw the board | `GET /api/v1/whiteboard/board` | `whiteboard/README.md` |
| Undo / redo | `POST /api/v1/whiteboard/undo`, `/redo` | `whiteboard/README.md` |
| Autocomplete and help | `GET /api/v1/whiteboard/commands` | every command with an example |
| Search the helper library | `POST /v1/recommend` | `ml/serving/API.md` |
| Report accept or reject | `POST /v1/feedback` | `ml/serving/API.md` |
| Check a file against the library | `POST /v1/check-file` | `ml/serving/service/app.py` |

Start them locally with `python -m whiteboard.server --project <path>` and
`python -m ml.serving.service.app`.
