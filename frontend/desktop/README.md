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

## Opened from the CLI

`plovane open`, `plovane wb`, `plovane mcp` and `plovane agent` launch this
app with `--project <folder>`, the way `docker` starts Docker Desktop. The app
then skips the folder picker and opens that folder's whiteboard. It holds a
single-instance lock, so launching it again hands the new folder to the
running window and brings it forward instead of starting a second copy. When
the app started the server for another folder, it stops that server and
starts one for the new folder.

The server is started with `--exit-with <app pid>`, so it stops if the app
dies without quitting cleanly. The CLI clears `ELECTRON_RUN_AS_NODE`, which
terminals inside VS Code set and which would otherwise start Electron with no
window.
