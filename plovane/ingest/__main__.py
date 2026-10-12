"""CLI entry point: ``python -m plovane.ingest``.

Separate from ``harvest.py`` because the package's ``__init__`` imports that
module for its exports. Running ``python -m plovane.ingest.harvest`` directly
then imports it twice -- once as a package member, once as ``__main__`` -- and
Python warns that module-level state may be duplicated. A thin ``__main__``
avoids it without giving up the exports.
"""

from plovane.ingest.harvest import main

if __name__ == "__main__":
    raise SystemExit(main())
