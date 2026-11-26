"""Turn harvested examples into chat-format training data for a small whiteboard model.

The task the model learns: given a project's file tree and its README (with
diagrams removed), draw the architecture as Plovane ``/sketch`` lines, the
way the project's authors drew it. One example per diagram.

    python -m ml.whiteboard_data.prepare        # writes ml/data/whiteboard_sft_{train,eval}.jsonl

Each line is ``{"messages": [system, user, assistant], "repo", "diagram"}``,
the format Hugging Face TRL's SFTTrainer reads. Labels are cleaned of emoji
and markdown so the model learns plain names.
"""

from __future__ import annotations

import json
import re
from typing import Any

from ml.whiteboard_data.harvest import EXAMPLES, ROOT

TRAIN = ROOT / "ml" / "data" / "whiteboard_sft_train.jsonl"
EVAL = ROOT / "ml" / "data" / "whiteboard_sft_eval.jsonl"
MAX_TREE_LINES = 200
MAX_DOCS = 5000
EMOJI = re.compile("[\U0001F000-\U0001FAFF☀-➿️‍]")

SYSTEM = """You draw a software project's architecture for the Plovane whiteboard.
Read the file tree and the README, then reply with /sketch lines only, one per line:
  service "Name"      database "Name"      queue "Name"      external "Name"
  "From" -> "To" "short label"
Use one block per real part of the system (apps, services, workers, databases, queues,
outside APIs, the user), named in plain words. Join them in the direction data or
requests flow, with a few-word label where it helps. Usually 4 to 12 blocks."""


# Input: text (str)
# Output: str
# Processing: A sketch line with every quoted label cleaned of emoji and stray spaces.
# Changed by: Claude Code
# Date: 2026-11-26
def plain(text: str) -> str:
    text = re.sub(r'"([^"]*)"', lambda m: '"' + " ".join(EMOJI.sub("", m.group(1)).split()) + '"', text)
    return " ".join(EMOJI.sub("", text).split())


# Input: example (dict[str, Any])
# Output: str
# Processing: The user message for one project: its file tree (trimmed) and README (diagrams removed, trimmed).
# Changed by: Claude Code
# Date: 2026-11-26
def prompt_for(example: dict[str, Any]) -> str:
    tree = "\n".join(example["tree"][:MAX_TREE_LINES])
    more = len(example["tree"]) - MAX_TREE_LINES
    if more > 0:
        tree += f"\n... and {more} more files"
    return f"Project: {example['repo']}\n\nFiles:\n{tree}\n\nREADME:\n{example['readme'][:MAX_DOCS]}\n\nDraw the architecture."


# Input: example (dict[str, Any])
# Output: str
# Processing: The answer the model should give: the authors' diagram as /sketch lines, with plain labels.
# Changed by: Claude Code
# Date: 2026-11-26
def answer_for(example: dict[str, Any]) -> str:
    return "\n".join(plain(line) for line in example["target"].splitlines() if plain(line))


# Input: none
# Output: dict[str, int]
# Processing: Write the train and eval chat files from the harvested examples; returns how many of each.
# Changed by: Claude Code
# Date: 2026-11-26
def main() -> dict[str, int]:
    counts = {"train": 0, "eval": 0}
    with TRAIN.open("w", encoding="utf-8") as train, EVAL.open("w", encoding="utf-8") as held:
        for line in EXAMPLES.read_text(encoding="utf-8").splitlines():
            example = json.loads(line)
            row = {"repo": example["repo"], "diagram": example["diagram"], "messages": [
                {"role": "system", "content": SYSTEM}, {"role": "user", "content": prompt_for(example)},
                {"role": "assistant", "content": answer_for(example)}]}
            (held if example["split"] == "eval" else train).write(json.dumps(row) + "\n")
            counts[example["split"]] += 1
    print(json.dumps(counts))
    return counts


if __name__ == "__main__":
    main()
