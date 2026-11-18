"""One-off seed loader for B08.

Reads ``ml/data/entries.jsonl`` (the 378 rows Sourish's branch already uses)
and writes a §5.2-shaped library_block row per entry. Nothing in ml/ is
touched; this just mirrors the catalogue so B07 has something to search.

    python -m backend.library_db.load_seed                     # into :memory: no-op
    python -m backend.library_db.load_seed --db ./library.db   # populate a file
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from backend.library_db.engine import Engine, default_engine
from backend.library_db.repo import Repo


# Input: raw (dict)
# Output: dict
# Processing: Turn one ml/data/entries.jsonl entry into a §5.2 block.
# Changed by: Nirmal
# Date: 2026-10-31
def entry_to_block(raw: dict[str, Any]) -> dict[str, Any]:
    deps = raw.get("dependencies")
    if isinstance(deps, dict):
        deps = list(deps.get("packages", []))
    elif isinstance(deps, list):
        deps = [str(d) for d in deps]
    else:
        deps = []
    return {
        "block_id": raw.get("id") or raw.get("block_id") or raw["name"],
        "name": raw.get("name") or raw.get("id") or "",
        "language": raw.get("language") or "unknown",
        "code": raw.get("code") or "",
        "comments": raw.get("description") or raw.get("comments") or "",
        "dependencies": deps,
    }


# Input: path (Path)
# Output: list[dict]
# Processing: Read a JSONL seed file and build the §5.2 blocks.
# Changed by: Nirmal
# Date: 2026-10-31
def read_entries(path: Path) -> list[dict[str, Any]]:
    blocks: list[dict[str, Any]] = []
    with path.open() as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            blocks.append(entry_to_block(json.loads(line)))
    return blocks


# Input: argv (list[str] | None)
# Output: int
# Processing: Load the seed JSONL into the configured DB.
# Changed by: Nirmal
# Date: 2026-10-31
def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Load ml/data/entries.jsonl into B08")
    parser.add_argument("--db", default=None, help="DB path (default: PLOVANE_LIBRARY_DB)")
    parser.add_argument("--entries",
                        default=str(Path("ml") / "data" / "entries.jsonl"),
                        help="path to a §5.2-compatible JSONL file")
    args = parser.parse_args(argv)
    entries_path = Path(args.entries)
    if not entries_path.exists():
        print(f"seed file not found: {entries_path}", file=sys.stderr)
        return 1
    engine = Engine(args.db) if args.db else default_engine()
    repo = Repo(engine)
    inserted = repo.bulk_insert(read_entries(entries_path))
    print(f"loaded {inserted} rows into {engine.path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
