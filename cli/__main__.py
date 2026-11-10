"""Allow ``python -m cli`` as a fallback for ``plovane``."""

from __future__ import annotations

from cli.app import main


if __name__ == "__main__":
    raise SystemExit(main())
