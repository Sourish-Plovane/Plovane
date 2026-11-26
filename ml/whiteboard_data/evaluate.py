"""Score the whiteboard AI against projects' own architecture diagrams.

For each held-out repository (``split == "eval"`` in the harvested examples)
it clones the pinned commit, removes every Mermaid diagram from the docs (so
the AI cannot copy the answer), builds the board with the whiteboard AI, and
compares the board's parts and arrows with the authors' diagram.

    python -m ml.whiteboard_data.evaluate --limit 12 --use-saved-key

Scores per repository, then averaged:

* parts recall / precision / F1 -- the diagram's boxes matched by blocks the AI drew (words in common);
* arrow recall -- the diagram's arrows whose two ends were matched and joined on the board;
* failed commands, duplicate names, share of the AI's arrows with a label, overlays switched on, long labels left;
* tokens and seconds.

Results go to ``ml/artifacts/whiteboard_eval.json`` and a table to ``ml/artifacts/whiteboard_eval.md``.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from ml.whiteboard_data.harvest import EXAMPLES, ROOT, words

CACHE = Path.home() / ".cache" / "plovane" / "whiteboard_repos"
"""Clones live outside the project, so their code is never scanned, tested or committed with it."""
RESULTS = ROOT / "ml" / "artifacts" / "whiteboard_eval.json"
TABLE = ROOT / "ml" / "artifacts" / "whiteboard_eval.md"
PART_KINDS = {"service", "server", "database", "queue", "external", "page"}
"""Top-level parts only: code-level steps and groups (from explaining a file) are not architecture."""
MAX_SOURCE_FILES = 1500
EMOJI = re.compile(r"[^\w\s/&+().,:'-]", re.UNICODE)


# Input: entry (dict[str, Any])
# Output: Path | None
# Processing: A clean checkout of a repository at its pinned commit (shallow, cached), with every Mermaid diagram removed from its docs.
# Changed by: Claude Code
# Date: 2026-11-26
def checkout(entry: dict[str, Any]) -> Path | None:
    from backend.whiteboard.mermaid import FENCE

    target = CACHE / entry["repo"].replace("/", "__")
    if not (target / ".git").is_dir():
        target.mkdir(parents=True, exist_ok=True)
        steps = [["git", "init", "-q"], ["git", "remote", "add", "origin", f"https://github.com/{entry['repo']}.git"],
                 ["git", "fetch", "-q", "--depth", "1", "origin", entry["commit"]]]
        for step in steps:
            if subprocess.run(step, cwd=target, capture_output=True, timeout=300).returncode != 0:
                shutil.rmtree(target, ignore_errors=True)
                return None
    subprocess.run(["git", "checkout", "-q", "-f", "FETCH_HEAD"], cwd=target, capture_output=True, timeout=120)
    subprocess.run(["git", "clean", "-q", "-fdx"], cwd=target, capture_output=True, timeout=120)
    for doc in list(target.rglob("*.md")) + list(target.rglob("*.mdx")):
        if ".git" in doc.parts:
            continue
        try:
            text = doc.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if "```mermaid" in text.lower():
            doc.write_text(FENCE.sub("(diagram removed for evaluation)\n", text), encoding="utf-8")
    return target


# Input: a (str), b (str)
# Output: float
# Processing: How alike two part names are: shared words over the shorter name's words, on whole words and on camel-case pieces, whichever is higher (1.0 when one name contains the other).
# Changed by: Claude Code
# Date: 2026-11-26
def similarity(a: str, b: str) -> float:
    a, b = EMOJI.sub(" ", a), EMOJI.sub(" ", b)
    whole_a = {w.lower() for w in re.split(r"[^A-Za-z0-9]+", a) if len(w) > 2}
    whole_b = {w.lower() for w in re.split(r"[^A-Za-z0-9]+", b) if len(w) > 2}
    best = 0.0
    for wa, wb in ((whole_a, whole_b), (words(a), words(b))):
        if not wa or not wb:
            continue
        x, y = " ".join(sorted(wa)), " ".join(sorted(wb))
        best = max(best, 1.0 if x in y or y in x else len(wa & wb) / min(len(wa), len(wb)))
    return best


# Input: reference (dict[str, str]), drawn (dict[str, str])
# Output: dict[str, str]
# Processing: Pair diagram boxes with drawn blocks, best pairs first, one-to-one, keeping pairs at least half alike.
# Changed by: Claude Code
# Date: 2026-11-26
def match(reference: dict[str, str], drawn: dict[str, str]) -> dict[str, str]:
    pairs = sorted(((similarity(r, d), rid, did) for rid, r in reference.items() for did, d in drawn.items()), reverse=True)
    out: dict[str, str] = {}
    used: set[str] = set()
    for score, rid, did in pairs:
        if score < 0.5:
            break
        if rid not in out and did not in used:
            out[rid] = did
            used.add(did)
    return out


# Input: example (dict[str, Any]), ws (Workspace), result (dict[str, Any])
# Output: dict[str, Any]
# Processing: Compare the board the AI built with the authors' diagram, and add the quality and cost measures.
# Changed by: Claude Code
# Date: 2026-11-26
def score(example: dict[str, Any], ws: Any, result: dict[str, Any]) -> dict[str, Any]:
    from backend.whiteboard.engine import USER
    from backend.whiteboard.overlays import active

    chart, board = example["chart"], ws.board
    reference = {nid: n["label"] for nid, n in chart["nodes"].items()}
    drawn = {b.id: b.meta.get("short_name") or b.name for b in board.blocks.values() if b.kind in PART_KINDS}
    pairs = match(reference, drawn)
    recall = len(pairs) / max(len(reference), 1)
    precision = len(pairs) / max(len(drawn), 1) if drawn else 0.0
    joined = {frozenset((a.source, a.target)) for a in board.arrows.values()}
    edges = [e for e in chart["edges"] if e["from"] != e["to"]]
    hit = sum(1 for e in edges if e["from"] in pairs and e["to"] in pairs
              and frozenset((pairs[e["from"]], pairs[e["to"]])) in joined)
    organise = result.get("organise") or {}
    commands = organise.get("commands") or []
    names = [n.lower() for n in drawn.values()]
    ai_arrows = [a for a in board.arrows.values() if a.source in drawn and a.target in drawn]
    custom_on = [o for o in active(board, USER) if board.overlays.get(o, {}).get("source") == "custom"]
    usage = dict(organise.get("usage") or {})
    for key, value in ((result.get("draw") or {}).get("usage") or {}).items():
        usage[key] = usage.get(key, 0) + value  # the one-shot draw call counts too
    return {
        "parts_recall": round(recall, 3), "parts_precision": round(precision, 3),
        "parts_f1": round(2 * recall * precision / (recall + precision), 3) if recall + precision else 0.0,
        "arrow_recall": round(hit / max(len(edges), 1), 3),
        "commands": len(commands), "failed": sum(not c.get("ok") for c in commands),
        "failed_examples": [f"{c.get('command', '')[:90]} -> {str(c.get('error'))[:90]}" for c in commands if not c.get("ok")][:6],
        "duplicates": len(names) - len(set(names)),
        "labelled_arrows": round(sum(bool(a.style.get("label")) for a in ai_arrows) / max(len(ai_arrows), 1), 3),
        "overlays_on": len(custom_on),
        "long_labels_left": sum(1 for b in board.blocks.values()
                                if len(b.name) > 28 and not b.meta.get("short_name") and b.kind != "agent"),
        "input_tokens": usage.get("input_tokens", 0), "output_tokens": usage.get("output_tokens", 0),
        "status": organise.get("status") or result.get("status"), "note": result.get("note"),
        "reference_parts": len(reference), "drawn_parts": len(drawn),
        "matched": {reference[r]: drawn[d] for r, d in pairs.items()},
    }


SKETCH_PART = re.compile(r'^\s*(service|database|queue|external|server)\s+"([^"]+)"', re.IGNORECASE)
SKETCH_ARROW = re.compile(r'^\s*"([^"]+)"\s*->\s*"([^"]+)"')


# Input: text (str), chart (dict[str, Any])
# Output: dict[str, Any]
# Processing: Score /sketch lines (a model's one-shot answer) against the authors' diagram, with no board: parts F1, arrow recall, and how many lines were not valid sketch.
# Changed by: Claude Code
# Date: 2026-11-26
def score_sketch(text: str, chart: dict[str, Any]) -> dict[str, Any]:
    drawn: dict[str, str] = {}
    arrows: set[frozenset] = set()
    bad = 0
    lines = [line for line in text.splitlines() if line.strip() and not line.strip().startswith("```")]
    for line in lines:
        part, arrow = SKETCH_PART.match(line), SKETCH_ARROW.match(line)
        if part:
            drawn.setdefault(part.group(2).lower(), part.group(2))
        elif arrow:
            for name in arrow.groups():
                drawn.setdefault(name.lower(), name)
            arrows.add(frozenset((arrow.group(1).lower(), arrow.group(2).lower())))
        else:
            bad += 1
    reference = {nid: n["label"] for nid, n in chart["nodes"].items()}
    pairs = match(reference, drawn)
    recall = len(pairs) / max(len(reference), 1)
    precision = len(pairs) / max(len(drawn), 1) if drawn else 0.0
    edges = [e for e in chart["edges"] if e["from"] != e["to"]]
    hit = sum(1 for e in edges if e["from"] in pairs and e["to"] in pairs
              and frozenset((pairs[e["from"]], pairs[e["to"]])) in arrows)
    return {"parts_recall": round(recall, 3), "parts_precision": round(precision, 3),
            "parts_f1": round(2 * recall * precision / (recall + precision), 3) if recall + precision else 0.0,
            "arrow_recall": round(hit / max(len(edges), 1), 3), "bad_lines": bad, "lines": len(lines),
            "drawn_parts": len(drawn), "reference_parts": len(reference)}


# Input: provider (str), model (str), limit (int)
# Output: dict[str, Any]
# Processing: The one-shot task (the same one a fine-tuned model learns) on every held-out diagram: prompt, answer, score.
# Changed by: Claude Code
# Date: 2026-11-26
def sketch_mode(provider: str, model: str, limit: int) -> dict[str, Any]:
    from backend.whiteboard import llm_agent
    from ml.whiteboard_data.prepare import SYSTEM, prompt_for

    rows = []
    for example in (json.loads(line) for line in EXAMPLES.read_text().splitlines()):
        if example["split"] != "eval" or len(rows) >= limit:
            continue
        started = time.time()
        try:
            text, usage = llm_agent.complete(provider, model, SYSTEM, prompt_for(example))
            rows.append({"repo": example["repo"], "diagram": example["diagram"], "seconds": round(time.time() - started, 1),
                         **usage, **score_sketch(text, example["chart"]), "answer": text})
        except Exception as exc:  # noqa: BLE001
            rows.append({"repo": example["repo"], "diagram": example["diagram"], "error": str(exc)[:300]})
        print(json.dumps({k: v for k, v in rows[-1].items() if k != "answer"}), file=sys.stderr, flush=True)
    scored = [r for r in rows if "error" not in r]
    keys = ("parts_recall", "parts_precision", "parts_f1", "arrow_recall", "bad_lines", "input_tokens", "output_tokens", "seconds")
    mean = {k: round(sum(r[k] for r in scored) / len(scored), 3) for k in keys} if scored else {}
    return {"mode": "sketch", "provider": provider, "model": model, "examples": len(rows), "scored": len(scored),
            "mean": mean, "rows": rows}


# Input: argv (list[str] | None)
# Output: int
# Processing: Evaluate the whiteboard AI on the held-out repositories and write the results and a table.
# Changed by: Claude Code
# Date: 2026-11-26
def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--limit", type=int, default=12)
    parser.add_argument("--provider", default=None)
    parser.add_argument("--model", default=None)
    parser.add_argument("--use-saved-key", action="store_true",
                        help="use the key saved with 'plovane connect' even if the environment has another")
    parser.add_argument("--mode", choices=("build", "sketch"), default="build",
                        help="build: the whiteboard AI builds a real board; sketch: one call, scored offline")
    parser.add_argument("--examples", default=None,
                        help="read examples from this file instead of the harvested set (to compare runs on a fixed set)")
    parser.add_argument("--rescore", action="store_true",
                        help="score the boards the last build run left in the clone cache again, without calling a model")
    args = parser.parse_args(argv)

    from backend.whiteboard import board_ai, llm_agent
    from backend.whiteboard.engine import Workspace
    from backend.whiteboard.ops.project import codegen

    if args.use_saved_key:
        for provider, key in llm_agent.saved_keys().items():
            os.environ[llm_agent.KEY_NAMES[provider]] = key
    llm_agent.load_saved_keys()
    provider, model = board_ai.provider_and_model(args.provider, args.model)
    if args.mode == "sketch":
        report = sketch_mode(provider, model, args.limit)
        out = RESULTS.with_name(f"whiteboard_sketch_{provider}.json")
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(report, indent=1) + "\n")
        print(json.dumps({"mode": "sketch", "provider": provider, "model": model, **report["mean"], "scored": report["scored"]}))
        return 0
    previous = {r["repo"]: r for r in json.loads(RESULTS.read_text())["rows"]} if args.rescore and RESULTS.exists() else {}
    seen, rows = set(), []
    source = Path(args.examples) if args.examples else EXAMPLES
    for example in (json.loads(line) for line in source.read_text().splitlines()):
        if example["split"] != "eval" or example["repo"] in seen:
            continue
        seen.add(example["repo"])
        if len(rows) >= args.limit:
            break
        if args.rescore:
            root = CACHE / example["repo"].replace("/", "__")
            old = previous.get(example["repo"])
            if not (root / "whiteboard.plo").exists() or not old or "error" in old:
                continue
            kept = {"organise": {"commands": [{"ok": True}] * (old["commands"] - old["failed"]) + [{"ok": False}] * old["failed"],
                                 "usage": {"input_tokens": old["input_tokens"], "output_tokens": old["output_tokens"]},
                                 "status": old.get("status")}, "note": old.get("note")}
            rows.append({"repo": example["repo"], "seconds": old["seconds"], **score(example, Workspace(root), kept)})
            continue
        root = checkout(example)
        if root is None:
            print(f"skip {example['repo']}: could not fetch", file=sys.stderr)
            continue
        sources = sum(1 for p in root.rglob("*") if p.is_file() and ".git" not in p.parts and codegen.language_of(p.name))
        if sources > MAX_SOURCE_FILES:
            print(f"skip {example['repo']}: {sources} source files", file=sys.stderr)
            continue
        started = time.time()
        try:
            ws = Workspace(root)
            result = board_ai.build(ws, provider=args.provider, model=args.model)
            row = {"repo": example["repo"], "seconds": round(time.time() - started, 1), **score(example, ws, result)}
        except Exception as exc:  # noqa: BLE001  (one bad project must not stop the run)
            row = {"repo": example["repo"], "error": f"{type(exc).__name__}: {exc}"[:300]}
        rows.append(row)
        print(json.dumps({k: v for k, v in row.items() if k != "matched"}), file=sys.stderr, flush=True)
    scored = [r for r in rows if "error" not in r]
    keys = ("parts_recall", "parts_precision", "parts_f1", "arrow_recall", "failed", "commands", "duplicates",
            "labelled_arrows", "overlays_on", "long_labels_left", "input_tokens", "output_tokens", "seconds")
    mean = {k: round(sum(r[k] for r in scored) / len(scored), 3) for k in keys} if scored else {}
    report = {"provider": provider, "model": model, "repositories": len(rows), "scored": len(scored),
              "mean": mean, "rows": rows}
    RESULTS.parent.mkdir(parents=True, exist_ok=True)
    RESULTS.write_text(json.dumps(report, indent=1) + "\n")
    head = "| Repository | Parts F1 | Recall | Precision | Arrows | Failed cmds | Tokens in | Seconds |\n|---|---|---|---|---|---|---|---|\n"
    body = "".join(f"| {r['repo']} | {r['parts_f1']} | {r['parts_recall']} | {r['parts_precision']} | {r['arrow_recall']} | "
                   f"{r['failed']}/{r['commands']} | {r['input_tokens']} | {r['seconds']} |\n" for r in scored)
    errors = "".join(f"| {r['repo']} | error: {r['error'][:80]} |||||||\n" for r in rows if "error" in r)
    total = (f"| **mean** | **{mean.get('parts_f1')}** | {mean.get('parts_recall')} | {mean.get('parts_precision')} | "
             f"{mean.get('arrow_recall')} | {mean.get('failed')}/{mean.get('commands')} | {mean.get('input_tokens')} | "
             f"{mean.get('seconds')} |\n") if mean else ""
    TABLE.write_text(f"# Whiteboard AI eval ({provider} {model})\n\nHeld-out projects scored against their own "
                     f"architecture diagrams (removed from their docs before the build).\n\n{head}{body}{errors}{total}")
    print(json.dumps({"provider": provider, "model": model, **mean, "scored": len(scored)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
