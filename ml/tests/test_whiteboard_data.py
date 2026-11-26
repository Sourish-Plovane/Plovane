"""Tests for the whiteboard data harvester and eval scoring (no network)."""

from __future__ import annotations

from backend.whiteboard.mermaid import parse
from ml.whiteboard_data.evaluate import match, similarity
from ml.whiteboard_data.harvest import grounding, split_of, words


# Input: none
# Output: None
# Processing: Names and paths split into the same lowercase words, whatever their casing style.
# Changed by: Claude Code
# Date: 2026-11-26
def test_words_split_every_naming_style() -> None:
    assert words("src/PaymentService/stripe_client.py") >= {"src", "payment", "service", "stripe", "client"}
    assert words("API (FastAPI)") >= {"api", "fast"}


# Input: none
# Output: None
# Processing: Grounding is the share of diagram boxes named in the file tree.
# Changed by: Claude Code
# Date: 2026-11-26
def test_grounding() -> None:
    chart = parse("flowchart LR\n W[Web app] --> A[Payments API] --> D[(Postgres)] --> X[Mystery]")
    assert grounding(chart, ["web/app.tsx", "api/payments.py"]) == 0.5


# Input: none
# Output: None
# Processing: The train/eval split is fixed by the repository name and holds out roughly one in five.
# Changed by: Claude Code
# Date: 2026-11-26
def test_split_is_stable_and_about_one_in_five() -> None:
    names = [f"owner/repo{i}" for i in range(500)]
    assert [split_of(n) for n in names] == [split_of(n) for n in names]
    held = sum(split_of(n) == "eval" for n in names)
    assert 60 <= held <= 140


# Input: none
# Output: None
# Processing: Part names match by shared words (emoji and casing ignored), one-to-one, best pairs first.
# Changed by: Claude Code
# Date: 2026-11-26
def test_similarity_and_matching() -> None:
    assert similarity("🐘 PostgreSQL", "postgresql database") == 1.0
    assert similarity("Checkout API", "checkout service") == 0.5
    assert similarity("Web front end", "Stripe") == 0.0
    reference = {"W": "Web app", "A": "Checkout API", "D": "PostgreSQL"}
    drawn = {"web": "shop web app", "api": "checkout API", "db": "order database (postgresql)", "x": "stripe"}
    assert match(reference, drawn) == {"W": "web", "A": "api", "D": "db"}


# Input: none
# Output: None
# Processing: One-shot /sketch answers are scored offline against the diagram; prose lines count as bad lines.
# Changed by: Claude Code
# Date: 2026-11-26
def test_score_sketch() -> None:
    from ml.whiteboard_data.evaluate import score_sketch

    chart = parse('flowchart LR\n W[Web app] --> A[Checkout API] --> D[(PostgreSQL)]\n A --> S{{Stripe}}')
    answer = ('Here is the board:\nservice "Web app"\nservice "Checkout API"\ndatabase "Postgres DB"\n'
              '"Web app" -> "Checkout API" "sends cart"\n"Checkout API" -> "Stripe" "charges"')
    got = score_sketch(answer, chart)
    assert got["bad_lines"] == 1 and got["reference_parts"] == 4
    # "Postgres DB" counts as PostgreSQL; the API -> database arrow was not drawn.
    assert got["parts_recall"] == 1.0 and got["arrow_recall"] == round(2 / 3, 3)


# Input: tmp_path, monkeypatch
# Output: None
# Processing: prepare writes chat rows (system, user with tree and README, assistant with plain sketch lines) split by repository.
# Changed by: Claude Code
# Date: 2026-11-26
def test_prepare_writes_chat_rows(tmp_path, monkeypatch) -> None:
    import json

    from ml.whiteboard_data import prepare

    examples = tmp_path / "examples.jsonl"
    rows = [{"repo": "a/b", "diagram": 1, "split": "train", "tree": ["app.py"], "readme": "# B",
             "target": 'service "🧑 User"\n"🧑 User" -> "API"'},
            {"repo": "c/d", "diagram": 1, "split": "eval", "tree": ["x.ts"], "readme": "# D", "target": 'service "X"'}]
    examples.write_text("\n".join(json.dumps(r) for r in rows))
    monkeypatch.setattr(prepare, "EXAMPLES", examples)
    monkeypatch.setattr(prepare, "TRAIN", tmp_path / "train.jsonl")
    monkeypatch.setattr(prepare, "EVAL", tmp_path / "eval.jsonl")
    assert prepare.main() == {"train": 1, "eval": 1}
    row = json.loads((tmp_path / "train.jsonl").read_text())
    assert [m["role"] for m in row["messages"]] == ["system", "user", "assistant"]
    assert "app.py" in row["messages"][1]["content"]
    assert row["messages"][2]["content"] == 'service "User"\n"User" -> "API"'


# Input: none
# Output: None
# Processing: The whiteboard GPU job runs its own setup script, asks for a 24 GB card, ships the adapter as a release asset, and carries no secret.
# Changed by: Claude Code
# Date: 2026-11-26
def test_vast_whiteboard_job() -> None:
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from ml.training import vast_run

    script = vast_run.build_onstart("run1", max_hours=2, job="whiteboard")
    assert "bash ml/training/whiteboard_box_setup.sh" in script and "timeout 7200 " in script
    assert "whiteboard_lora_adapter.tar.gz" in script and "${GH_TOKEN}" in script and "ghp_" not in script
    assert vast_run.JOBS["whiteboard"]["gpu_ram"] >= 24000
    assert "gpu_box_setup.sh" in vast_run.build_onstart("run1", max_hours=2)  # the Stage 2 job is unchanged
