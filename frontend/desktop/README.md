# Desktop app (B01)

The whiteboard GUI: the zoomable canvas, the prompt panel where people type
slash commands, and the library palette. It renders what
`GET /api/v1/whiteboard/board` returns and sends every change as a command,
so a person and an AI agent always do the same thing. See `../README.md` for
the endpoints.
