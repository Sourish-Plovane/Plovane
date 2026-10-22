"""Get this machine ready to run the MCP server, then load it once.

    GITHUB_TOKEN=... python -m ml_models.setup

1. Base encoder and trained cross-encoder (scripts/setup_cross_encoder.py,
   which verifies both hashes and checks the model ranks correctly).
2. The library, seed/entries.jsonl. It is gitignored, so a fresh clone
   rebuilds it from the repositories and commits seed/sources.json pins.
3. Loads the engine, which computes and caches the library embeddings so the
   first real tool call is not the slow one.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


# Input: none
# Output: None
# Processing: Rebuild seed/entries.jsonl from the pinned sources.
# Changed by: Claude Code
# Date: 2026-10-18
def rebuild_library() -> None:
    """Same steps as the "== dataset ==" stage of scripts/gpu_box_setup.sh."""
    from ml.ingest.local_source import harvest_clones

    sources = json.loads((ROOT / "seed" / "sources.json").read_text())
    clones = Path.home() / "sources"
    roots = []
    for source in sources["sources"]:
        root = clones / source["repo"].replace("/", "_")
        if not (root / ".git").exists():
            root.mkdir(parents=True, exist_ok=True)

            def git(*args: str) -> None:
                subprocess.run(["git", "-C", str(root), *args], check=True)

            git("init", "-q")
            git("remote", "add", "origin", f"https://github.com/{source['repo']}.git")
            git("fetch", "-q", "--depth", "1", "origin", source["commit"])
            git("checkout", "-q", "FETCH_HEAD")
        roots.append(root)
    report = harvest_clones(roots, ROOT / "seed" / "entries.jsonl")
    print(f"OK  library           rebuilt {report['written']} entries")


# Input: none
# Output: int
# Processing: CLI entry point.
# Changed by: Claude Code
# Date: 2026-10-18
def main() -> int:
    models = subprocess.run([sys.executable, str(ROOT / "scripts" / "setup_cross_encoder.py")])
    if models.returncode != 0:
        return models.returncode
    if (ROOT / "seed" / "entries.jsonl").exists():
        print("OK  library           seed/entries.jsonl")
    else:
        rebuild_library()

    from ml_models.engine import Engine

    engine = Engine.load()
    status = engine.status()
    print(f"OK  engine loaded in {status['load_seconds']}s on {status['device']}: "
          f"{status['library_entries']} entries, cross-encoder {status['cross_encoder']}")
    for note in status["notes"]:
        print(f"    {note}")
    print("Ready. Register it with:  claude mcp add plovane -- "
          f"{sys.executable} -m mcp_server  (run from {ROOT})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
