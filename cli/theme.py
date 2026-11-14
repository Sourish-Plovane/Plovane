"""Theme palette for the plovane CLI.

Three presets, selected by ``settings set theme <name>``:

    yellow  -- default, matches the handbook accent
    cyan    -- cooler palette, primary swaps to cyan
    mono    -- no color, for piping / minimal terminals

Each handler reads colors through ``c("primary")`` rather than hard-coded
ANSI constants so a theme switch takes effect on the next prompt.
"""

from __future__ import annotations

PALETTES: dict[str, dict[str, str]] = {
    "yellow": {
        "reset":     "\x1b[0m",
        "bold":      "\x1b[1m",
        "dim":       "\x1b[2m",
        "primary":   "\x1b[33m",
        "secondary": "\x1b[36m",
        "ok":        "\x1b[32m",
        "bad":       "\x1b[31m",
        "warn":      "\x1b[33m",
        "muted":     "\x1b[35m",
    },
    "cyan": {
        "reset":     "\x1b[0m",
        "bold":      "\x1b[1m",
        "dim":       "\x1b[2m",
        "primary":   "\x1b[36m",
        "secondary": "\x1b[33m",
        "ok":        "\x1b[32m",
        "bad":       "\x1b[31m",
        "warn":      "\x1b[33m",
        "muted":     "\x1b[35m",
    },
    "mono": {
        "reset": "", "bold": "", "dim": "", "primary": "",
        "secondary": "", "ok": "", "bad": "", "warn": "", "muted": "",
    },
}

_current: str = "yellow"


# Input: name (str)
# Output: str
# Processing: Switch active palette; falls back to yellow for unknown names.
# Changed by: Sourish
# Date: 2026-10-31
def apply(name: str) -> str:
    global _current
    _current = name if name in PALETTES else "yellow"
    return _current


# Input: key (str)
# Output: str
# Processing: ANSI escape for ``key`` under the active palette (empty on mono).
# Changed by: Sourish
# Date: 2026-10-31
def c(key: str) -> str:
    return PALETTES[_current].get(key, "")


# Input: none
# Output: str
# Processing: Return the name of the active theme.
# Changed by: Sourish
# Date: 2026-10-31
def name() -> str:
    return _current
