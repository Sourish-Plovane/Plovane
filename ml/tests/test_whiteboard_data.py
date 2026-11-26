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
