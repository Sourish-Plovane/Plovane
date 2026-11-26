"""Harvest whiteboard training and eval data: public projects that draw their own architecture.

Many READMEs carry a Mermaid flowchart of how the project fits together. Each
one, paired with the repository's file tree and its README with the diagram
removed, is an example of what a good whiteboard for that project looks like:
input (tree + docs), target (the authors' own picture, as /sketch lines).

    python -m ml.whiteboard_data.harvest --pages 2            # search, filter, write the manifest and examples
    python -m ml.whiteboard_data.harvest --from-manifest      # rebuild the examples from the committed manifest

Only kept:
* permissive licences (MIT, Apache-2.0, BSD, ISC, 0BSD, Unlicense), recorded per example;
* Python, JavaScript or TypeScript projects, not forks or archives, under 100 MB;
* "parts" diagrams (not processes) with 3 to 30 nodes;
* diagrams grounded in the code: at least a quarter of the boxes name something in the file tree.

Every repository is pinned to the commit it was read at. The manifest
(``ml/data/whiteboard_sources.json``) is committed; the examples
(``ml/data/whiteboard_examples.jsonl``) are regenerated from it and stay out of
git. Each example carries ``split``: about one in five repositories is held out
for evaluation, decided by a hash of its name so it never moves.

Uses the GitHub CLI (``gh``), so it runs with your GitHub login.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

from backend.whiteboard import mermaid

ROOT = Path(__file__).resolve().parents[2]
MANIFEST = ROOT / "ml" / "data" / "whiteboard_sources.json"
EXAMPLES = ROOT / "ml" / "data" / "whiteboard_examples.jsonl"
QUERIES = ("mermaid flowchart filename:README.md", "mermaid graph TD filename:README.md",
           "mermaid flowchart LR architecture filename:README.md", "mermaid graph LR api database filename:README.md",
           "mermaid flowchart architecture filename:ARCHITECTURE.md")
LICENSES = {"mit", "apache-2.0", "bsd-2-clause", "bsd-3-clause", "isc", "0bsd", "unlicense"}
LANGUAGES = {"Python", "JavaScript", "TypeScript"}
MAX_KB = 100_000
MAX_TREE = 400
MAX_README = 8000
MIN_GROUNDING = 0.25
EVAL_SHARE = 5  # one repository in five is held out


# Input: args (list[str]), retries (int)
# Output: Any
# Processing: Call the GitHub API through gh and parse the JSON; waits and retries when rate limited, None when refused.
# Changed by: Claude Code
# Date: 2026-11-26
def gh(args: list[str], retries: int = 3) -> Any:
    if shutil.which("gh") is None:
        return rest(args, retries)
    for attempt in range(retries):
        result = subprocess.run(["gh", "api", *args], capture_output=True, text=True, timeout=120)
        if result.returncode == 0:
            return json.loads(result.stdout or "null")
        if "rate limit" in result.stderr.lower() or "403" in result.stderr:
            time.sleep(20 * (attempt + 1))
            continue
        return None
    return None


# Input: args (list[str]), retries (int)
# Output: Any
# Processing: The same call as gh() over plain HTTPS with GH_TOKEN or GITHUB_TOKEN, for machines without the gh CLI.
# Changed by: Claude Code
# Date: 2026-11-26
def rest(args: list[str], retries: int = 3) -> Any:
    path, params = "", {}
    items = iter(args)
    for arg in items:
        if arg == "-X":
            next(items)
        elif arg == "-f":
            key, _, value = next(items).partition("=")
            params[key] = value
        else:
            path = arg
    url = f"https://api.github.com/{path}" + (("&" if "?" in path else "?") + urllib.parse.urlencode(params) if params else "")
    token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
    headers = {"Accept": "application/vnd.github+json", "User-Agent": "plovane-harvest"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=60) as reply:
                return json.loads(reply.read() or b"null")
        except urllib.error.HTTPError as exc:
            if exc.code in (403, 429):
                time.sleep(20 * (attempt + 1))
                continue
            return None
        except OSError:
            time.sleep(5)
    return None


# Input: queries (tuple[str, ...]), pages (int)
# Output: list[str]
# Processing: Repository names whose README (or architecture doc) mentions a Mermaid flowchart, from GitHub code search.
# Changed by: Claude Code
# Date: 2026-11-26
def search(queries: tuple[str, ...], pages: int) -> list[str]:
    found: dict[str, None] = {}
    for query in queries:
        for page in range(1, pages + 1):
            reply = gh(["-X", "GET", "search/code", "-f", f"q={query}", "-f", "per_page=100", "-f", f"page={page}"])
            items = (reply or {}).get("items") or []
            for item in items:
                found.setdefault(item["repository"]["full_name"], None)
            if len(items) < 100:
                break
            time.sleep(7)  # code search allows about ten requests a minute
    return list(found)


# Input: repo (str)
# Output: dict[str, Any] | None
# Processing: A repository's language, licence, size and pinned commit, or None when it does not qualify.
# Changed by: Claude Code
# Date: 2026-11-26
def qualify(repo: str) -> dict[str, Any] | None:
    meta = gh([f"repos/{repo}"])
    if not meta or meta.get("fork") or meta.get("archived") or meta.get("size", 0) > MAX_KB:
        return None
    license_id = ((meta.get("license") or {}).get("spdx_id") or "").lower()
    if license_id not in LICENSES or meta.get("language") not in LANGUAGES:
        return None
    branch = meta.get("default_branch") or "main"
    head = gh([f"repos/{repo}/commits/{branch}"])
    if not head:
        return None
    return {"repo": repo, "commit": head["sha"], "license": license_id, "language": meta["language"],
            "stars": meta.get("stargazers_count", 0)}


# Input: text (str)
# Output: set[str]
# Processing: Lowercase words of names and paths: each whole word plus its camel-case pieces (PostgreSQL gives postgresql, postgre, sql).
# Changed by: Claude Code
# Date: 2026-11-26
def words(text: str) -> set[str]:
    found: set[str] = set()
    for token in re.split(r"[^A-Za-z0-9]+", text):
        found.add(token.lower())
        found.update(re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", token).lower().split())
    return {w for w in found if len(w) > 2}


# Input: chart (dict[str, Any]), tree (list[str])
# Output: float
# Processing: The share of a diagram's boxes that name something in the repository's file tree.
# Changed by: Claude Code
# Date: 2026-11-26
def grounding(chart: dict[str, Any], tree: list[str]) -> float:
    known = set().union(*(words(p) for p in tree)) if tree else set()
    labels = [n["label"] for n in chart["nodes"].values()]
    return sum(bool(words(label) & known) for label in labels) / max(len(labels), 1)


# Input: repo (str)
# Output: str
# Processing: "train" or "eval" for a repository, fixed by a hash of its name.
# Changed by: Claude Code
# Date: 2026-11-26
def split_of(repo: str) -> str:
    return "eval" if int(hashlib.sha256(repo.lower().encode()).hexdigest(), 16) % EVAL_SHARE == 0 else "train"


# Input: entry (dict[str, Any])
# Output: list[dict[str, Any]]
# Processing: The examples one pinned repository gives: each grounded "parts" diagram in its README, with the tree and the README minus its diagrams.
# Changed by: Claude Code
# Date: 2026-11-26
def examples_for(entry: dict[str, Any]) -> list[dict[str, Any]]:
    repo, commit = entry["repo"], entry["commit"]
    readme = gh([f"repos/{repo}/readme?ref={commit}"])
    tree_reply = gh([f"repos/{repo}/git/trees/{commit}?recursive=1"])
    if not readme or not tree_reply:
        return []
    text = base64.b64decode(readme.get("content") or "").decode("utf-8", errors="replace")
    tree = [t["path"] for t in tree_reply.get("tree") or [] if t.get("type") == "blob"]
    docs = mermaid.FENCE.sub("(diagram removed)", text)[:MAX_README]
    out = []
    for number, source in enumerate(mermaid.diagrams(text), start=1):
        try:
            chart = mermaid.parse(source)
        except ValueError:
            continue
        if mermaid.is_process(chart) or not 3 <= len(chart["nodes"]) <= 30:
            continue
        score = grounding(chart, tree)
        if score < MIN_GROUNDING:
            continue
        out.append({**entry, "diagram": number, "split": split_of(repo), "grounding": round(score, 2),
                    "tree": tree[:MAX_TREE], "readme": docs, "target": mermaid.to_sketch(chart), "chart": chart})
    return out


# Input: argv (list[str] | None)
# Output: int
# Processing: Search, qualify and harvest (or rebuild from the manifest), then write the manifest and examples and print a summary.
# Changed by: Claude Code
# Date: 2026-11-26
def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--pages", type=int, default=1, help="search result pages per query (100 each)")
    parser.add_argument("--limit", type=int, default=400, help="most repositories to look at")
    parser.add_argument("--from-manifest", action="store_true", help="rebuild examples from the committed manifest")
    args = parser.parse_args(argv)
    if args.from_manifest:
        entries = json.loads(MANIFEST.read_text())["repositories"]
    else:
        names = search(QUERIES, args.pages)[: args.limit]
        print(f"{len(names)} repositories mention a Mermaid flowchart", file=sys.stderr)
        entries = [e for e in (qualify(n) for n in names) if e]
        print(f"{len(entries)} qualify (licence, language, size)", file=sys.stderr)
    examples, kept = [], []
    for entry in entries:
        found = examples_for(entry)
        if found:
            examples.extend(found)
            kept.append({k: entry[k] for k in ("repo", "commit", "license", "language", "stars")})
    MANIFEST.parent.mkdir(parents=True, exist_ok=True)
    if not args.from_manifest:
        MANIFEST.write_text(json.dumps({"format": "plovane-whiteboard-sources", "version": 1,
                                        "repositories": sorted(kept, key=lambda e: e["repo"].lower())}, indent=1) + "\n")
    with EXAMPLES.open("w", encoding="utf-8") as handle:
        for example in examples:
            handle.write(json.dumps(example) + "\n")
    splits = {s: sum(e["split"] == s for e in examples) for s in ("train", "eval")}
    print(json.dumps({"repositories": len(kept), "examples": len(examples), **splits}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
