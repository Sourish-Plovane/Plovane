# Frontend

The parts people use directly: the desktop app with the whiteboard (B01) and
the command-line tool (B02). Owner: Aarav. This layout is a starting proposal,
so change it as the code arrives.

```
frontend/
├── README.md     this file
├── desktop/      B01 desktop GUI: whiteboard canvas, prompt panel, palette
└── cli/          B02 command-line interface
```

## What the frontend talks to

Everything goes through the local backend on `http://127.0.0.1`, using the
handbook's §4.1 envelope (`{"ok": ..., "data": ..., "error": ...}`).

| Need | Endpoint | Details |
|---|---|---|
| Run a whiteboard command | `POST /api/v1/whiteboard/command` with `{"command": "/block add service auth"}` | `whiteboard/README.md` |
| Run a structured operation | `POST /api/v1/whiteboard/op` with `{"op": "block.add", "args": {...}}` | `whiteboard/README.md` |
| Draw the board | `GET /api/v1/whiteboard/board` | `whiteboard/README.md` |
| Undo / redo | `POST /api/v1/whiteboard/undo`, `/redo` | `whiteboard/README.md` |
| Search the helper library | `POST /v1/recommend` (model service) | `docs/b06_recommendation_api.md` |

Start the whiteboard API with `python -m whiteboard.server --project <path>`.
Start the model service with `python -m ml_models.service.app`.

The full list of slash commands, with parameters and examples, is in
`whiteboard/README.md`. The frontend sends the text a person types, and the
backend parses it, so the frontend never needs its own parser.
