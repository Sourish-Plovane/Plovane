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
"""

from __future__ import annotations

import argparse
import atexit
import json
import shlex
import shutil
import signal
import sys
from typing import Any, Callable

from cli import config as config_mod
from cli import credentials as creds_mod
from cli import theme
from cli.services import health as health_mod
from cli.services import local as local_mod
from cli.services import supabase as supabase_mod

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
# Processing: Parse flags, load config and theme, run one command or loop.
# Changed by: Sourish
# Date: 2026-10-31
def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="plovane", description="Plovane interactive CLI")
    parser.add_argument("command", nargs="*", help="Run a single command and exit")
    parser.add_argument("--no-fullscreen", action="store_true", help="Disable alt-screen mode")
    args = parser.parse_args(argv)
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
# Processing: Look up the first token and call its handler, printing errors.
# Changed by: Sourish
# Date: 2026-10-31
def _dispatch(tokens: list[str], state: dict) -> None:
    name = tokens[0]
    handler = HANDLERS.get(name)
    if handler is None:
        print(f"{theme.c('bad')}unknown command:{theme.c('reset')} {name}. Type 'help'.")
        return
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
# Processing: Print the registered commands with a one-line description.
# Changed by: Sourish
# Date: 2026-10-31
@_command("help", "?")
def _help(args: list[str], state: dict) -> None:
    rows = [
        ("help",                     "Show this list"),
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
