# Plovane

Plovane recommends verified, reusable code. You describe what you need, or
point it at a file, and it finds a tested helper from the library. The design,
and every contract between components, is in `Plovane_Handbook.md`.

## Repository layout

```
Plovane/
├── README.md               this file
├── Plovane_Handbook.md     architecture, block ownership and JSON contracts
├── pyproject.toml
├── ml/                     all ML work: model code, data, weights, training, serving, tests
├── backend/                MCP server, whiteboard (built) and the handbook's backend blocks (skeletons)
├── frontend/               desktop app (B01) and CLI (B02)
└── docs/                   proposals for the handbook
```

## Where to start

| You work on | Start in |
|---|---|
| ML models, training, the recommendation API | `ml/README.md` |
| Calling the recommender from the backend | `ml/serving/API.md` |
| Backend: MCP server, whiteboard, local backend, cloud server, library, credits | `backend/README.md` |
| Connecting AI agents (Claude Code, Claude Desktop) | `backend/mcp_server/README.md` |
| Frontend (desktop app, CLI) | `frontend/README.md` |

Every folder has a README saying what belongs in it.

## Running Plovane on macOS and Windows

Plovane has five parts. You only need the ones you use:

| Part | What it is | Address |
|---|---|---|
| Whiteboard server | Draws your project as blocks; everything else talks to it | http://127.0.0.1:8700/whiteboard |
| Desktop app | A window around the whiteboard; starts the server for you | — |
| Recommendation API | Searches the verified library | http://127.0.0.1:8600 |
| MCP server | Lets Claude Code or Claude Desktop use Plovane | runs inside Claude |
| Landing page | The product website | http://127.0.0.1:5500/landing/ |

### 1. Install the tools (once)

| Tool | Version | macOS | Windows |
|---|---|---|---|
| Python | 3.11 or newer | `brew install python@3.11` | [python.org](https://www.python.org/downloads/); tick **Add python.exe to PATH** |
| Node.js | 20 or newer | `brew install node` | [nodejs.org](https://nodejs.org) (LTS) |
| Git | any | `xcode-select --install` | [git-scm.com](https://git-scm.com/download/win) |

Check them in a new terminal: `python3 --version` (macOS) or `py --version` (Windows), then `node --version` and `git --version`.

On Windows, use **PowerShell** for every command below.

### 2. Get the code and a Python environment

The desktop app looks for a `.venv` folder at the repository root, so create it there.

**macOS**

```sh
git clone https://github.com/Sourish-Plovane/Plovane.git
cd Plovane
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev,neural,boost,agents]" huggingface_hub "mcp>=1.2"
```

**Windows**

```powershell
git clone https://github.com/Sourish-Plovane/Plovane.git
cd Plovane
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e ".[dev,neural,boost,agents]" huggingface_hub "mcp>=1.2"
```

If PowerShell refuses to run `Activate.ps1`, run `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned` once and try again.

Activate the environment again (the `source` or `Activate.ps1` line) in every new terminal. This also installs the `plovane` command.

### 3. Download the models and build the library (once)

The trained model weights live in a private release, so you need a GitHub token that can read this repository ([create one](https://github.com/settings/tokens)). The commands below ask for it without showing it on screen.

**macOS**

```sh
read -s "GITHUB_TOKEN?Paste GitHub token: "; export GITHUB_TOKEN; echo
python -m ml.serving.setup
```

**Windows**

```powershell
$env:GITHUB_TOKEN = [Net.NetworkCredential]::new('', (Read-Host "Paste GitHub token" -AsSecureString)).Password
python -m ml.serving.setup
```

This downloads about 1 GB, clones the library's source projects into `~/sources`, builds `ml/data/entries.jsonl` and indexes it. The first run takes 10–20 minutes; later runs skip what is already done. It needs about 4 GB of free RAM.

If the library changes later (for example after a pull), delete `ml/data/entries.jsonl` and run setup again.

### 4. Run it

**Desktop app (recommended).** It starts the whiteboard server, then asks which project folder to open.

macOS:

```sh
cd frontend/desktop
npm install          # first time only
env -u ELECTRON_RUN_AS_NODE npm start
```

Windows:

```powershell
cd frontend\desktop
npm install          # first time only
Remove-Item Env:ELECTRON_RUN_AS_NODE -ErrorAction SilentlyContinue
npm start
```

Clearing `ELECTRON_RUN_AS_NODE` matters in VS Code's terminal, which sets it and stops the window from opening.

You can also open a folder straight from the command line: `plovane open --dir <project folder>`.

**Whiteboard in a browser (no desktop app).** From the repository root:

```sh
python -m backend.whiteboard.server --project <project folder>
```

Then open http://127.0.0.1:8700/whiteboard. Use `--project .` to look at Plovane itself.

**Recommendation API.** In another terminal:

```sh
python -m ml.serving.service.app
```

It listens on http://127.0.0.1:8600. The first request loads the models, which takes about 10 seconds.

**MCP server for Claude Code.** Run once from the repository root, with the environment active:

```sh
claude mcp add plovane -- plovane mcp --dir <project folder> --agent claude
```

For Claude Desktop, see `backend/mcp_server/README.md`.

**Landing page.** Serve the `frontend` folder (not `frontend/landing`, or the logo won't load):

```sh
cd frontend
python -m http.server 5500
```

Then open http://127.0.0.1:5500/landing/. On Windows, `python` works once the environment is active.

### 5. Run the tests

```sh
python -m pytest
```

### Optional settings

| Variable | Effect |
|---|---|
| `ANTHROPIC_API_KEY` | Lets whiteboard agents chat and lets `/explain` write explanations with Claude |
| `PLOVANE_PYTHON` | The Python the desktop app uses, if not `.venv` |
| `PLOVANE_NO_CROSS_ENCODER=1` | Faster, lighter search with lower accuracy, for low-RAM machines |

Set them with `export NAME=value` on macOS or `$env:NAME = "value"` in PowerShell.

### Common problems

| Problem | Fix |
|---|---|
| `python` or `py` not found (Windows) | Reinstall Python with **Add python.exe to PATH** ticked, then open a new terminal |
| `could not download ...` during setup | `GITHUB_TOKEN` is missing or can't read this repository |
| Desktop app opens no window | Clear `ELECTRON_RUN_AS_NODE` (see step 4) |
| "Port 8700 already serves another project" | Another whiteboard server is running; close it or quit the other app |
| First search is very slow | Normal: it loads the models once (about 10 s, longer on the first ever run) |

## Conventions

- Every function carries the five-field comment block (Input, Output,
  Processing, Changed by, Date). See `.claude/agents/README.md`.
- Each folder has one README.
- Adding or removing a top-level folder needs Sourish and Nirmal to approve it.
- Work happens on personal branches and merges into `dev`. A personal branch
  (such as `Sourish`) holds only work in progress: once it's merged into `dev`,
  it's reset to `dev`, so it never carries finished work.
