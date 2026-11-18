"""Entry point and REPL for ``plovane``.

Launching the binary with no args drops the user into a fullscreen menu
on the terminal's alt-screen buffer. Each prompt reads a line, dispatches
to a registered handler, prints the result, and loops until ``quit`` or
EOF. Running ``plovane <command>`` executes a single command and exits
without taking over the screen.

Handlers call three service clients:

    cli.services.local      B03 /api/v1/{auth,recommendations}
    cli.services.health     liveness probes for B03/B05/B06
    cli.services.supabase   Supabase gotrue + PostgREST probe
    cli.services.whiteboard find or launch the whiteboard app, call it as the person

Whiteboard commands (``open``, ``wb``, ``mcp``, ``agent``) open the desktop
app first when it is not running, the way ``docker`` starts Docker Desktop.
In the interactive prompt, any line that is not a CLI command is sent to the
whiteboard as a slash command, so ``/block add service payments`` just works.
"""

from __future__ import annotations

import argparse
import atexit
import json
import shlex
import shutil
import signal
import sys
from pathlib import Path
from typing import Any, Callable

from cli import config as config_mod
from cli import credentials as creds_mod
from cli import theme
from cli.services import health as health_mod
from cli.services import local as local_mod
from cli.services import supabase as supabase_mod
from cli.services import whiteboard as wb_mod

try:
    import readline
except ImportError:
    readline = None  # type: ignore[assignment]

ALT_SCREEN_ON = "\x1b[?1049h"
ALT_SCREEN_OFF = "\x1b[?1049l"
CLEAR = "\x1b[2J"
HOME = "\x1b[H"
SHOW_CURSOR = "\x1b[?25h"

HANDLERS: dict[str, Callable[[list[str], dict], None]] = {}


# Input: name (str), ...
# Output: Callable
# Processing: Decorator that registers a handler under one or more aliases.
# Changed by: Sourish
# Date: 2026-10-31
def _command(*names: str):
    def wrap(func: Callable[[list[str], dict], None]) -> Callable[[list[str], dict], None]:
        for alias in names:
            HANDLERS[alias] = func
        return func
    return wrap


# -- entry point ----------------------------------------------------------


# Input: argv (list[str] | None)
# Output: int
# Processing: Parse flags (passing unknown ones to the command), load config and theme, run one command or loop.
# Changed by: Claude Code
# Date: 2026-11-09
def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="plovane", description="Plovane interactive CLI")
    parser.add_argument("command", nargs="*", help="Run a single command and exit")
    parser.add_argument("--no-fullscreen", action="store_true", help="Disable alt-screen mode")
    args, extra = parser.parse_known_args(argv)
    args.command = [*args.command, *extra]
    state = config_mod.load()
    theme.apply(state["settings"].get("theme", "yellow"))
    if args.command:
        _dispatch(args.command, state)
        return 0
    fullscreen = not args.no_fullscreen and sys.stdout.isatty()
    if fullscreen:
        _enter_fullscreen()
    _setup_readline()
    try:
        _banner(state)
        while True:
            try:
                line = input(f" {theme.c('primary')}>{theme.c('reset')} ").strip()
            except (EOFError, KeyboardInterrupt):
                print()
                return 0
            if not line:
                continue
            if line.startswith("/"):
                line = line[1:]
            try:
                tokens = shlex.split(line)
            except ValueError as error:
                print(f"{theme.c('bad')}parse error:{theme.c('reset')} {error}")
                continue
            if tokens[0] in {"quit", "q", "exit"}:
                print(f" {theme.c('dim')}bye.{theme.c('reset')}")
                return 0
            if tokens[0] in {"clear", "cls"}:
                _banner(state)
                continue
            print()
            _dispatch(tokens, state)
            print()
    finally:
        if fullscreen:
            _exit_fullscreen()


# Input: tokens (list[str]), state (dict)
# Output: None
# Processing: Call the first token's handler, or send the line to the whiteboard; print errors.
# Changed by: Claude Code
# Date: 2026-11-09
def _dispatch(tokens: list[str], state: dict) -> None:
    name = tokens[0]
    handler = HANDLERS.get(name)
    if handler is None:
        from backend.whiteboard.commands import COMMANDS

        if name.lstrip("/") not in {c.phrase.split()[0] for c in COMMANDS}:
            print(f"{theme.c('bad')}unknown command:{theme.c('reset')} {name}. Type 'help'.")
            return
        # A whiteboard command word (block, connect, endpoint, ...): send the line to the whiteboard.
        handler, tokens = HANDLERS["wb"], ["wb", *tokens]
    try:
        handler(tokens[1:], state)
    except Exception as error:
        print(f"{theme.c('bad')}error:{theme.c('reset')} {error}")


# -- line editing + history ----------------------------------------------


# Input: none
# Output: None
# Processing: Enable readline arrow-key editing, history, and tab complete.
# Changed by: Sourish
# Date: 2026-10-31
def _setup_readline() -> None:
    if readline is None:
        return
    history_path = config_mod.CONFIG_DIR / "history"
    try:
        config_mod.CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        if history_path.exists():
            readline.read_history_file(str(history_path))
    except OSError:
        pass
    readline.set_history_length(1000)
    atexit.register(_save_history, str(history_path))
    try:
        readline.set_completer(_completer)
        if "libedit" in getattr(readline, "__doc__", "") or "":
            readline.parse_and_bind("bind ^I rl_complete")
        else:
            readline.parse_and_bind("tab: complete")
    except Exception:
        pass


# Input: text (str), state (int)
# Output: str | None
# Processing: Return the ``state``-th completion for ``text`` from HANDLERS.
# Changed by: Sourish
# Date: 2026-10-31
def _completer(text: str, state: int) -> str | None:
    options = [name for name in HANDLERS if name.startswith(text)]
    return options[state] if state < len(options) else None


# Input: path (str)
# Output: None
# Processing: Persist readline history on exit, swallowing OS errors.
# Changed by: Sourish
# Date: 2026-10-31
def _save_history(path: str) -> None:
    if readline is None:
        return
    try:
        readline.write_history_file(path)
    except OSError:
        pass


# -- fullscreen helpers ---------------------------------------------------


# Input: none
# Output: None
# Processing: Switch to the alt-screen buffer so exit restores the terminal.
# Changed by: Sourish
# Date: 2026-10-31
def _enter_fullscreen() -> None:
    sys.stdout.write(ALT_SCREEN_ON + CLEAR + HOME + SHOW_CURSOR)
    sys.stdout.flush()
    atexit.register(_exit_fullscreen)
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            signal.signal(sig, _signal_exit)
        except (ValueError, OSError):
            pass


# Input: none
# Output: None
# Processing: Leave the alt-screen buffer and make the cursor visible again.
# Changed by: Sourish
# Date: 2026-10-31
def _exit_fullscreen() -> None:
    sys.stdout.write(SHOW_CURSOR + ALT_SCREEN_OFF)
    sys.stdout.flush()


# Input: signum (int), frame (Any)
# Output: None
# Processing: Restore the terminal before letting the default signal fire.
# Changed by: Sourish
# Date: 2026-10-31
def _signal_exit(signum: int, frame) -> None:
    _exit_fullscreen()
    raise SystemExit(0)


# -- banner ---------------------------------------------------------------


# Input: state (dict)
# Output: None
# Processing: Draw the yellow banner, welcome card, and shortcut footer.
# Changed by: Sourish
# Date: 2026-10-31
def _banner(state: dict) -> None:
    user = state.get("user", "local")
    session = creds_mod.load_session()
    account = session.get("email") or session.get("user_id") or f"guest ({user})"
    cols = shutil.get_terminal_size((80, 24)).columns
    sys.stdout.write(CLEAR + HOME)
    art = [
        "  ____  _                             ",
        " |  _ \\| | _____   ____ _ _ __   ___  ",
        " | |_) | |/ _ \\ \\ / / _` | '_ \\ / _ \\ ",
        " |  __/| | (_) \\ V / (_| | | | |  __/ ",
        " |_|   |_|\\___/ \\_/ \\__,_|_| |_|\\___| ",
    ]
    pad = " " * max((cols - len(art[0])) // 2, 0)
    print()
    for line in art:
        print(pad + f"{theme.c('primary')}{theme.c('bold')}{line}{theme.c('reset')}")
    print()
    card_width = min(max(cols - 4, 40), 72)
    top = "╭" + "─" * (card_width - 2) + "╮"
    bot = "╰" + "─" * (card_width - 2) + "╯"
    card_pad = " " * max((cols - card_width) // 2, 1)
    lines = [
        f" {theme.c('bold')}Welcome to Plovane{theme.c('reset')}  {theme.c('dim')}account={theme.c('secondary')}{account}{theme.c('reset')}",
        "",
        f" {theme.c('dim')}Type a command. Prefix with / is also fine (e.g. /status).{theme.c('reset')}",
        f" {theme.c('dim')}Try: {theme.c('secondary')}help{theme.c('reset')}{theme.c('dim')}, "
        f"{theme.c('secondary')}login{theme.c('reset')}{theme.c('dim')}, "
        f"{theme.c('secondary')}rank{theme.c('reset')}{theme.c('dim')}, "
        f"{theme.c('secondary')}settings{theme.c('reset')}{theme.c('dim')}, "
        f"{theme.c('secondary')}quit{theme.c('reset')}",
    ]
    print(card_pad + f"{theme.c('primary')}{top}{theme.c('reset')}")
    for line in lines:
        print(card_pad + f"{theme.c('primary')}│{theme.c('reset')}{line}")
    print(card_pad + f"{theme.c('primary')}{bot}{theme.c('reset')}")
    print()
    footer = (f"{theme.c('dim')}shortcuts:{theme.c('reset')}  "
              f"{theme.c('secondary')}help{theme.c('reset')}  "
              f"{theme.c('secondary')}status{theme.c('reset')}  "
              f"{theme.c('secondary')}login{theme.c('reset')}  "
              f"{theme.c('secondary')}rank{theme.c('reset')}  "
              f"{theme.c('secondary')}settings{theme.c('reset')}  "
              f"{theme.c('secondary')}supabase{theme.c('reset')}  "
              f"{theme.c('secondary')}quit{theme.c('reset')}  "
              f"{theme.c('dim')}| ctrl-d exits{theme.c('reset')}")
    print(card_pad + footer)
    print()


# -- commands -------------------------------------------------------------


# Input: args (list[str]), state (dict)
# Output: None
# Processing: Print the registered commands, whiteboard ones included, with a one-line description.
# Changed by: Claude Code
# Date: 2026-11-09
@_command("help", "?")
def _help(args: list[str], state: dict) -> None:
    rows = [
        ("help",                     "Show this list"),
        ("open [dir]",               "Open the whiteboard window (launches the app)"),
        ("wb \"/block add ...\"",     "Run a whiteboard command; it shows up live"),
        ("/<command>",               "Same as wb: any slash command goes to the whiteboard"),
        ("agent run \"task\" --provider claude|deepseek", "Let an AI build on the whiteboard"),
        ("agent add <name> [--access read-write]", "Add an AI agent (the only place agents are added)"),
        ("agent token <name>",       "API token for an agent (for your own scripts)"),
        ("connect [claude|deepseek|openai]", "Add an API key / show which are set"),
        ("mcp [--agent claude]",     "MCP server for Claude Code / Desktop / Cursor"),
        ("serve [dir] [--headless]", "Run the whiteboard server in this terminal"),
        ("status",                   "Ping B03/B05/B06"),
        ("register <email>",         "Register with B03 /api/v1/auth/register"),
        ("login <email>",            "Log in and store the session token"),
        ("logout",                   "Discard the stored session token"),
        ("whoami",                   "Show the stored session"),
        ("rank [--desc ...]",        "Call B06 pipeline via B03 /api/v1/recommendations"),
        ("settings",                 "Show ranker tuning + theme"),
        ("settings set <k> <v>",     "Set e.g. ranker.requested_count 5, theme cyan"),
        ("settings reset",           "Restore defaults"),
        ("supabase",                 "Ping configured Supabase project"),
        ("supabase set <k> <v>",     "Store url, anon_key, service_role_key"),
        ("config",                   "Show endpoint config"),
        ("config set <k> <v>",       "Set endpoints.local, endpoints.cloud, user, ..."),
        ("clear",                    "Repaint banner"),
        ("quit",                     "Exit"),
    ]
    width = max(len(label) for label, _ in rows)
    print(f"{theme.c('bold')} Commands{theme.c('reset')}")
    print(f" {theme.c('dim')}{'-' * (width + 32)}{theme.c('reset')}")
    for label, text in rows:
        print(f"  {theme.c('secondary')}{label.ljust(width)}{theme.c('reset')}  "
              f"{theme.c('dim')}{text}{theme.c('reset')}")


# Input: args (list[str]), state (dict)
# Output: None
# Processing: Probe every configured Plovane service and print the result.
# Changed by: Sourish
# Date: 2026-10-31
@_command("status")
def _status(args: list[str], state: dict) -> None:
    results = health_mod.probe_all(state["endpoints"])
    name_w = max(len(result.name) for result in results)
    url_w = max(len(result.url) for result in results)
    bar = f" {theme.c('dim')}+-{'-' * name_w}-+-{'-' * 6}-+-{'-' * url_w}-+{theme.c('reset')}"
    header = (
        f" {theme.c('dim')}|{theme.c('reset')} {theme.c('bold')}{'name'.ljust(name_w)}{theme.c('reset')} "
        f"{theme.c('dim')}|{theme.c('reset')} {theme.c('bold')}{'state'.ljust(6)}{theme.c('reset')} "
        f"{theme.c('dim')}|{theme.c('reset')} {theme.c('bold')}{'url'.ljust(url_w)}{theme.c('reset')} "
        f"{theme.c('dim')}|{theme.c('reset')}"
    )
    print(bar)
    print(header)
    print(bar)
    for result in results:
        state_text = "OK" if result.ok else "DOWN"
        state_color = theme.c("ok") if result.ok else theme.c("bad")
        print(
            f" {theme.c('dim')}|{theme.c('reset')} {result.name.ljust(name_w)} "
            f"{theme.c('dim')}|{theme.c('reset')} {state_color}{state_text.ljust(6)}{theme.c('reset')} "
            f"{theme.c('dim')}|{theme.c('reset')} {result.url.ljust(url_w)} "
            f"{theme.c('dim')}|{theme.c('reset')}"
        )
    print(bar)
    for result in results:
        if not result.ok:
            print(f"  {theme.c('warn')}!{theme.c('reset')} {result.name}: "
                  f"{theme.c('dim')}{result.detail}{theme.c('reset')}")


# Input: args (list[str]), state (dict)
# Output: None
# Processing: Print or mutate endpoint config; ``config set a.b value`` writes.
# Changed by: Sourish
# Date: 2026-10-31
@_command("config")
def _config(args: list[str], state: dict) -> None:
    if not args:
        print(json.dumps({"user": state["user"], "endpoints": state["endpoints"]},
                         indent=2, sort_keys=True))
        return
    if args[0] == "set" and len(args) == 3:
        _apply_set(state, args[1], args[2])
        path = config_mod.save(state)
        print(f"wrote {path}")
        return
    print("usage: config | config set <key> <value>")


# Input: args (list[str]), state (dict)
# Output: None
# Processing: Show settings, set a value, or reset to defaults.
# Changed by: Sourish
# Date: 2026-10-31
@_command("settings")
def _settings(args: list[str], state: dict) -> None:
    if not args:
        print(json.dumps(state["settings"], indent=2, sort_keys=True))
        return
    if args[0] == "reset":
        state["settings"] = config_mod._deep_copy(config_mod.DEFAULTS["settings"])
        theme.apply(state["settings"]["theme"])
        path = config_mod.save(state)
        print(f"reset settings, wrote {path}")
        return
    if args[0] == "set" and len(args) == 3:
        key, raw_value = args[1], args[2]
        value = _coerce(raw_value)
        if "." in key:
            section, inner = key.split(".", 1)
            bucket = state["settings"].setdefault(section, {})
            if not isinstance(bucket, dict):
                raise ValueError(f"{section} is not a section")
            bucket[inner] = value
        else:
            state["settings"][key] = value
        if key == "theme":
            theme.apply(str(value))
        path = config_mod.save(state)
        print(f"wrote {path}")
        return
    print("usage: settings | settings set <key> <value> | settings reset")


# Input: args (list[str]), state (dict)
# Output: None
# Processing: Register through B03 /api/v1/auth/register.
# Changed by: Sourish
# Date: 2026-10-31
@_command("register")
def _register(args: list[str], state: dict) -> None:
    email = args[0] if args else _prompt("email: ")
    password = _prompt("password: ", secret=True)
    import socket
    machine_id = socket.gethostname()
    data = local_mod.register(state["endpoints"]["local"], email, password, machine_id, state["user"])
    print(f"{theme.c('ok')}registered{theme.c('reset')} user_id={data.get('user_id')}")
    print(f"{theme.c('dim')}next: login {email}{theme.c('reset')}")


# Input: args (list[str]), state (dict)
# Output: None
# Processing: Log in through B03 and persist the session token.
# Changed by: Sourish
# Date: 2026-10-31
@_command("login")
def _login(args: list[str], state: dict) -> None:
    email = args[0] if args else _prompt("email: ")
    password = _prompt("password: ", secret=True)
    import socket
    machine_id = socket.gethostname()
    data = local_mod.login(state["endpoints"]["local"], email, password, machine_id, state["user"])
    data["email"] = email
    creds_mod.save_session(data)
    print(f"{theme.c('ok')}logged in{theme.c('reset')} as {email}  "
          f"{theme.c('dim')}expires {data.get('expires_at')}{theme.c('reset')}")


# Input: args (list[str]), state (dict)
# Output: None
# Processing: Delete the stored session token.
# Changed by: Sourish
# Date: 2026-10-31
@_command("logout")
def _logout(args: list[str], state: dict) -> None:
    removed = creds_mod.clear_session()
    msg = "cleared session" if removed else "no session to clear"
    print(f"{theme.c('dim')}{msg}{theme.c('reset')}")


# Input: args (list[str]), state (dict)
# Output: None
# Processing: Print the stored session (or ``not signed in``).
# Changed by: Sourish
# Date: 2026-10-31
@_command("whoami")
def _whoami(args: list[str], state: dict) -> None:
    session = creds_mod.load_session()
    if not session:
        print(f"{theme.c('dim')}not signed in{theme.c('reset')}")
        return
    masked = dict(session)
    token = masked.get("session_token")
    if isinstance(token, str) and len(token) > 10:
        masked["session_token"] = token[:4] + "..." + token[-4:]
    print(json.dumps(masked, indent=2, sort_keys=True))


# Input: args (list[str]), state (dict)
# Output: None
# Processing: Call B06 pipeline via B03 /api/v1/recommendations and print §5.2 fields.
# Changed by: Sourish
# Date: 2026-11-08
@_command("rank")
def _rank(args: list[str], state: dict) -> None:
    parser = argparse.ArgumentParser(prog="rank", add_help=False)
    parser.add_argument("--desc", default=None)
    parser.add_argument("--project", default="proj_demo")
    parser.add_argument("--language", default=None)
    parser.add_argument("--k", type=int, default=None)
    parser.add_argument("--no-cross-encoder", action="store_true")
    parsed, _ = parser.parse_known_args(args)
    tuning = state["settings"]["ranker"]
    body = {
        "project_id": parsed.project,
        "description": parsed.desc or _prompt("description: "),
        "language": parsed.language or tuning.get("language", "python"),
        "tech_stack": [tuning.get("language", "python")],
        "context_code": "",
        "requested_count": parsed.k or tuning.get("requested_count", 3),
        "ranker_options": {
            "cross_encoder": (not parsed.no_cross_encoder) and tuning.get("cross_encoder", True),
            "stage1_weight": tuning.get("stage1_weight", 1.0),
            "stage3_weight": tuning.get("stage3_weight", 1.0),
        },
    }
    session = creds_mod.load_session()
    data = local_mod.recommend(state["endpoints"]["local"], body,
                               state["user"], session.get("session_token"))
    recs = data.get("recommendations") or []
    if not recs:
        print(f"{theme.c('dim')}no recommendations{theme.c('reset')}")
        return
    print(f"{theme.c('bold')} {len(recs)} recommendation(s){theme.c('reset')}")
    for index, rec in enumerate(recs, start=1):
        title = rec.get("name") or rec.get("block_id") or "<unnamed>"
        score = rec.get("score")
        score_text = f" {theme.c('dim')}score={score:.3f}{theme.c('reset')}" if isinstance(score, (int, float)) else ""
        print(f"  {theme.c('secondary')}{index}.{theme.c('reset')} {title}{score_text}")
        summary = rec.get("comments")
        if rec.get("source_permalink"):
            summary = f"{summary or ''}  {rec['source_permalink']}".strip()
        if summary:
            print(f"     {theme.c('dim')}{summary}{theme.c('reset')}")


# Input: args (list[str]), state (dict)
# Output: None
# Processing: Ping Supabase or set credentials. ``supabase set url ...``.
# Changed by: Sourish
# Date: 2026-10-31
@_command("supabase")
def _supabase(args: list[str], state: dict) -> None:
    if args and args[0] == "set" and len(args) == 3:
        creds = creds_mod.load_supabase()
        creds[args[1]] = args[2]
        path = creds_mod.save_supabase(creds)
        print(f"wrote {path}")
        return
    creds = creds_mod.load_supabase()
    url = creds.get("url")
    anon = creds.get("anon_key")
    if not url:
        print(f"{theme.c('warn')}no Supabase URL configured{theme.c('reset')}")
        print(f"{theme.c('dim')}  supabase set url https://<project>.supabase.co{theme.c('reset')}")
        print(f"{theme.c('dim')}  supabase set anon_key <key>{theme.c('reset')}")
        return
    results = supabase_mod.ping(url, anon)
    for result in results:
        mark = f"{theme.c('ok')}OK{theme.c('reset')}" if result.ok else f"{theme.c('bad')}DOWN{theme.c('reset')}"
        code = f" {result.status}" if result.status is not None else ""
        print(f"  {mark}{code}  {result.url}  {theme.c('dim')}{result.detail}{theme.c('reset')}")


# -- whiteboard and AI connectors ----------------------------------------


# Input: args (list[str]), prog (str), extra (Callable | None)
# Output: tuple[argparse.Namespace, list[str]]
# Processing: Parse the flags every whiteboard command shares (--dir, --headless, --port) plus its own.
# Changed by: Claude Code
# Date: 2026-11-09
def _wb_args(args: list[str], prog: str, extra: Callable[[argparse.ArgumentParser], None] | None = None):
    parser = argparse.ArgumentParser(prog=prog, add_help=False)
    parser.add_argument("--dir", default=".", help="project folder (default: current)")
    parser.add_argument("--headless", action="store_true", help="no window: server only")
    parser.add_argument("--port", type=int, default=wb_mod.PORT)
    if extra:
        extra(parser)
    return parser.parse_known_args(args)


# Input: reply (dict[str, Any])
# Output: None
# Processing: Print a whiteboard envelope: the result on success, the error code and message otherwise.
# Changed by: Claude Code
# Date: 2026-11-09
def _print_reply(reply: dict[str, Any]) -> None:
    if reply.get("ok"):
        data = reply.get("data") or {}
        print(f"{theme.c('ok')}ok{theme.c('reset')}")
        shown = {k: v for k, v in data.items() if k in ("result", "changes", "notes", "dry_run")} or data
        print(json.dumps(shown, indent=2, default=str))
    else:
        error = reply.get("error") or {}
        print(f"{theme.c('bad')}{error.get('code', 'ERROR')}{theme.c('reset')} {error.get('message', reply)}")


# Input: args (list[str]), state (dict)
# Output: None
# Processing: Open the whiteboard window for a folder, launching the desktop app if it is not running.
# Changed by: Claude Code
# Date: 2026-11-09
@_command("open")
def _open(args: list[str], state: dict) -> None:
    opts, rest = _wb_args(args, "open")
    folder = rest[0] if rest else opts.dir
    info = wb_mod.ensure_running(folder, headless=opts.headless, port=opts.port, focus=True)
    print(f"{theme.c('ok')}Plovane ready{theme.c('reset')}  {info['project']}  "
          f"{theme.c('dim')}{info['url']}/whiteboard{theme.c('reset')}")


# Input: args (list[str]), state (dict)
# Output: None
# Processing: Run one whiteboard slash command as the person, opening the app first if needed.
# Changed by: Claude Code
# Date: 2026-11-09
@_command("wb", "whiteboard")
def _wb(args: list[str], state: dict) -> None:
    opts, rest = _wb_args(args, "wb", lambda p: (
        p.add_argument("--confirm", action="store_true"), p.add_argument("--dry-run", action="store_true")))
    if not rest:
        print(f"{theme.c('dim')}usage: wb \"/block add service payments\" [--confirm] [--dry-run]{theme.c('reset')}")
        return
    text = " ".join(rest)
    text = text if text.startswith("/") else "/" + text
    client = wb_mod.PersonClient(wb_mod.ensure_running(opts.dir, headless=opts.headless, port=opts.port))
    reply = client.command(text, confirm=opts.confirm, dry_run=opts.dry_run)
    data = reply.get("data") or {}
    if reply.get("ok") and data.get("status") == "needs_confirmation":
        print(f"{theme.c('secondary')}{data.get('message')}{theme.c('reset')}")
        print(json.dumps(data.get("preview"), indent=2, default=str)[:4000])
        if sys.stdin.isatty() and _prompt("apply it? [y/N] ").lower() in {"y", "yes"}:
            reply = client.command(text, confirm=True)
        else:
            print(f"{theme.c('dim')}not applied; add --confirm to apply{theme.c('reset')}")
            return
    _print_reply(reply)


# Input: args (list[str]), state (dict)
# Output: None
# Processing: Run the whiteboard server in this terminal (foreground) for a folder.
# Changed by: Claude Code
# Date: 2026-11-09
@_command("serve")
def _serve(args: list[str], state: dict) -> None:
    opts, rest = _wb_args(args, "serve")
    from backend.whiteboard.server import main as serve_main

    serve_main(["--project", rest[0] if rest else opts.dir, "--port", str(opts.port)])


# Input: args (list[str]), state (dict)
# Output: None
# Processing: Serve the whiteboard tools over MCP (stdio) as one agent, forwarding to the open whiteboard.
# Changed by: Claude Code
# Date: 2026-11-09
@_command("mcp")
def _mcp(args: list[str], state: dict) -> None:
    import os

    opts, _ = _wb_args(args, "mcp", lambda p: (
        p.add_argument("--agent", default="claude"),
        p.add_argument("--access", choices=["read", "read-write"], default=None,
                       help="grant this agent access to the whole project on connect"),
        p.add_argument("--no-launch", action="store_true", help="do not open the app; edit the project in-process")))
    project = Path(opts.dir).expanduser().resolve()
    os.environ["PLOVANE_PROJECT"] = str(project)
    os.environ["PLOVANE_AGENT"] = opts.agent
    if not opts.no_launch:
        try:
            # stdout is the MCP channel, so everything here reports on stderr.
            info = wb_mod.ensure_running(project, headless=opts.headless, port=opts.port)
            issued = wb_mod.PersonClient(info).agent_token(opts.agent, opts.access)
            if not issued.get("ok"):
                raise wb_mod.WhiteboardError((issued.get("error") or {}).get("message", "token refused"))
            os.environ["PLOVANE_URL"] = info["url"]
            os.environ["PLOVANE_AGENT_TOKEN"] = issued["data"]["token"]
            grants = issued["data"].get("grants") or []
            print(f"plovane mcp: agent {opts.agent!r} connected to {info['url']} with grants {grants}"
                  + ("" if grants else " (none yet: grant access in the Agents panel or pass --access)"),
                  file=sys.stderr, flush=True)
        except Exception as exc:  # noqa: BLE001  (still serve, editing the project directly)
            print(f"plovane mcp: whiteboard unavailable ({exc}); editing {project} in-process",
                  file=sys.stderr, flush=True)
    from backend.mcp_server.server import main as mcp_main

    mcp_main()


# Input: args (list[str]), state (dict)
# Output: None
# Processing: Manage AI agents: add/token (print a token), run (let Claude or DeepSeek work on the board).
# Changed by: Claude Code
# Date: 2026-11-09
@_command("agent")
def _agent(args: list[str], state: dict) -> None:
    if not args or args[0] not in {"add", "token", "run", "revoke"}:
        print(f"{theme.c('dim')}usage: agent add|token <name> [--access read|read-write] [--path P]\n"
              f"       agent revoke <name>\n"
              f"       agent run \"<task>\" --provider claude|deepseek|openai [--model M] [--agent NAME]{theme.c('reset')}")
        return
    action, rest = args[0], args[1:]
    opts, rest = _wb_args(rest, "agent", lambda p: (
        p.add_argument("--access", choices=["none", "read", "read-write", "existing"], default=None),
        p.add_argument("--path", default="."),
        p.add_argument("--provider", default="claude"),
        p.add_argument("--model", default=None),
        p.add_argument("--agent", default=None),
        p.add_argument("--max-steps", type=int, default=30)))
    client = wb_mod.PersonClient(wb_mod.ensure_running(opts.dir, headless=opts.headless, port=opts.port))
    if action in {"add", "token"}:
        if not rest:
            raise ValueError(f"agent {action} needs a name")
        reply = client.agent_token(rest[0], opts.access, opts.path)
        if not reply.get("ok"):
            _print_reply(reply)
            return
        data = reply["data"]
        print(f"{theme.c('ok')}agent {data['agent']}{theme.c('reset')}  grants={data.get('grants')}")
        print(f"  token: {theme.c('secondary')}{data['token']}{theme.c('reset')}  "
              f"{theme.c('dim')}(shown once; send as Authorization: Bearer <token>){theme.c('reset')}")
        return
    if action == "revoke":
        _print_reply(client.api(f"agents/{rest[0]}/token", method="DELETE"))
        return

    from backend.whiteboard import llm_agent

    task = " ".join(rest).strip()
    if not task:
        raise ValueError('agent run needs a task, e.g. agent run "add a payments service" --provider deepseek')
    provider = llm_agent.canonical(opts.provider)
    name = opts.agent or ("claude" if provider == "anthropic" else provider)
    issued = client.agent_token(name, opts.access or "read-write", opts.path)
    if not issued.get("ok"):
        _print_reply(issued)
        return
    agent_client = llm_agent.HttpClient(client.url, issued["data"]["token"])
    print(f"{theme.c('dim')}{name} ({provider}, {llm_agent.model_for(provider, opts.model)}) is working; "
          f"watch the whiteboard. Ctrl-C stops it.{theme.c('reset')}")

    # Input: event (dict[str, Any])
    # Output: None
    # Processing: Print one step of the agent run.
    # Changed by: Claude Code
    # Date: 2026-11-09
    def show(event: dict[str, Any]) -> None:
        if event["type"] == "thought":
            print(f"  {theme.c('dim')}{event['text']}{theme.c('reset')}")
        elif event["type"] == "command":
            mark = f"{theme.c('ok')}✓" if event["ok"] else f"{theme.c('bad')}✗"
            print(f"  {mark}{theme.c('reset')} {event['command']}"
                  + (f"  {theme.c('bad')}{event['error']}{theme.c('reset')}" if event.get("error") else ""))

    try:
        result = llm_agent.run(task, provider=provider, client=agent_client, model=opts.model,
                               max_steps=opts.max_steps, on_step=show)
    except KeyboardInterrupt:
        print(f"{theme.c('dim')}stopped{theme.c('reset')}")
        return
    print(f"{theme.c('ok')}{result['status']}{theme.c('reset')}  {len(result['commands'])} commands, "
          f"{result['usage']['input_tokens']}+{result['usage']['output_tokens']} tokens")
    if result.get("summary"):
        print(result["summary"])


# Input: args (list[str]), state (dict)
# Output: None
# Processing: Save an API key for Claude, DeepSeek or OpenAI in ~/.plovane/keys.json (mode 600).
# Changed by: Claude Code
# Date: 2026-11-09
@_command("connect")
def _connect(args: list[str], state: dict) -> None:
    from backend.whiteboard import llm_agent

    if not args:
        for row in llm_agent.providers():
            mark = f"{theme.c('ok')}connected" if row["configured"] else f"{theme.c('dim')}no key"
            print(f"  {row['label'].ljust(9)} {mark}{theme.c('reset')}  {theme.c('dim')}{row['model']} "
                  f"@ {row['base_url']}{theme.c('reset')}")
        print(f"{theme.c('dim')}connect <claude|deepseek|openai> to add a key{theme.c('reset')}")
        return
    provider = llm_agent.canonical(args[0])
    key = args[1] if len(args) > 1 else _prompt(f"{args[0]} API key: ", secret=True)
    if not key:
        raise ValueError("no key given")
    llm_agent.save_key(provider, key)
    up = wb_mod.running(Path(".").resolve())
    if up:  # let the open whiteboard use it without a restart
        wb_mod.PersonClient(up).api(f"providers/{provider}/key", {"key": key})
    print(f"{theme.c('ok')}connected {args[0]}{theme.c('reset')}  {theme.c('dim')}saved to "
          f"{llm_agent.KEYS_PATH}{theme.c('reset')}")


# -- small helpers --------------------------------------------------------


# Input: state (dict), key (str), value (str)
# Output: None
# Processing: Apply ``a.b`` -> ``state["a"]["b"] = value``; else top-level.
# Changed by: Sourish
# Date: 2026-10-31
def _apply_set(state: dict, key: str, value: str) -> None:
    if "." in key:
        head, tail = key.split(".", 1)
        bucket = state.setdefault(head, {})
        if not isinstance(bucket, dict):
            raise ValueError(f"{head} is not a section")
        bucket[tail] = value
    else:
        state[key] = value


# Input: message (str), secret (bool)
# Output: str
# Processing: Ask the user for a value; mask input when ``secret`` is True.
# Changed by: Sourish
# Date: 2026-10-31
def _prompt(message: str, secret: bool = False) -> str:
    if secret:
        import getpass
        return getpass.getpass(f" {theme.c('primary')}?{theme.c('reset')} {message}")
    return input(f" {theme.c('primary')}?{theme.c('reset')} {message}").strip()


# Input: raw (str)
# Output: Any
# Processing: Parse "true"/"false"/int/float; otherwise return the string.
# Changed by: Sourish
# Date: 2026-10-31
def _coerce(raw: str) -> Any:
    low = raw.lower()
    if low in {"true", "false"}:
        return low == "true"
    try:
        return int(raw)
    except ValueError:
        pass
    try:
        return float(raw)
    except ValueError:
        pass
    return raw


if __name__ == "__main__":
    raise SystemExit(main())
