# Desktop app (B01)

Electron shell for the whiteboard. It lets a person choose a project folder,
starts the existing local whiteboard server, waits for `/healthz`, then shows
Connected and opens the whiteboard.

## Run from the repository root

```sh
cd frontend/desktop
npm install
npm start
```

The shell uses the repository `.venv` Python when present. Otherwise, it uses
`python3` (`python` on Windows). Plovane requires Python 3.11+ and the project
dependencies from `pyproject.toml`. Set `PLOVANE_PYTHON` to choose another
interpreter.

The app starts `python -m backend.whiteboard.server --project <folder>` on
`127.0.0.1:8700`. It reuses a server already serving the selected folder and
reports an error if that port serves a different project.
