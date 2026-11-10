# Command-line interface (B02)

The whiteboard's **CLI** button opens this terminal below the prompt builder.
It loads the command list from the same local backend and sends commands to
`POST /api/v1/whiteboard/command`, so terminal operations update the same
server-owned graph as the canvas and MCP tools.

Use `/help` for built-ins and command count, `/` for command completion, and
any slash command returned by the backend for the complete whiteboard command
set. Risky operations show their preview and require `y` before applying.

Git aliases `git status`, `git pull`, and `git push` map to `/git status` and
`/git sync`; pushes use the backend confirmation flow. This UI runs Plovane
commands, not arbitrary operating-system shell commands.

The standalone frontend is `index.html`, `cli.css`, and `cli.js`. The local
whiteboard server serves it at `/cli/`. See `../README.md` and handbook §6 for
the CLI contract.
