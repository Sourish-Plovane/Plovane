"""One entry per model: what it is, where its weights are, and how good it is.

The single place that answers "which models does Plovane have?". The HTTP
service's ``/v1/models`` route and ``python -m ml.serving.registry`` both read
it, so the answer cannot drift between them.

    python -m ml.serving.registry
"""

from __future__ import annotations

import json
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


@dataclass(frozen=True)
class ModelCard:
    """What one model is, in terms a reader outside the ML work can use."""

    name: str
    block: str
    """Handbook block the model belongs to, e.g. ``B06``."""
    purpose: str
    kind: str
    """``pretrained``, ``fine-tuned``, ``trained``, ``hand-weighted``, ``rule-based`` or ``hosted``."""
    code: str
    weights: str | None
    """Path under the repository root, or None when there is nothing to load."""
    status: str
    """``serving``, ``optional`` or ``uncertified``."""
    accuracy: dict[str, float] | None = None
    """Held-out @k on the 350 assistant-written queries (issue 35); uncertified."""

    # Input: none
    # Output: bool
    # Processing: Whether this model's weights are on this machine.
    # Changed by: Sourish
    # Date: 2026-10-22
    @property
    def present(self) -> bool:
        """True when nothing needs loading or the weights are on disk."""
        return self.weights is None or (ROOT / self.weights).exists()


MODELS: tuple[ModelCard, ...] = (
    ModelCard(
        name="bi-encoder",
        block="B06",
        purpose="Turns queries and code into vectors so search can match meaning, not just words",
        kind="pretrained",
        code="ml/retrieval/bi_encoder.py",
        weights="ml/weights/unixcoder-base/pytorch_model.bin",
        status="serving",
        accuracy={"@1": 0.520, "@3": 0.697, "@10": 0.814},
    ),
    ModelCard(
        name="cross-encoder-stage2",
        block="B06",
        purpose="Reads a query and a candidate's code together and scores how well they match",
        kind="fine-tuned",
        code="ml/ranking/cross_encoder.py",
        weights="ml/weights/cross-encoder-stage2/cross_encoder_final.pt",
        status="serving",
        accuracy={"@1": 0.697, "@3": 0.883, "@10": 0.960},
    ),
    ModelCard(
        name="stage1-ranker",
        block="B06",
        purpose="Cuts retrieved candidates to a shortlist from retrieval and usage signals",
        kind="hand-weighted",
        code="ml/ranking/stage1.py",
        weights=None,
        status="serving",
    ),
    ModelCard(
        name="stage3-ranker",
        block="B06",
        purpose="Sets the final order; with the cross-encoder loaded it orders by its score",
        kind="hand-weighted",
        code="ml/ranking/stage3.py",
        weights=None,
        status="serving",
    ),
    ModelCard(
        name="stage1-booster",
        block="B06",
        purpose="Trained LightGBM replacement for the Stage 1 hand weights",
        kind="trained",
        code="ml/ranking/training.py",
        weights="ml/artifacts/stage1_retrieval_fusion.txt",
        status="uncertified",
    ),
    ModelCard(
        name="stage3-booster",
        block="B06",
        purpose="Trained LightGBM replacement for the Stage 3 hand weights",
        kind="trained",
        code="ml/ranking/stage3_training.py",
        weights="ml/artifacts/stage3_final.txt",
        status="uncertified",
    ),
    ModelCard(
        name="helper-finder",
        block="B12",
        purpose="Lists the functions in a file that stand alone as reusable helpers",
        kind="rule-based",
        code="ml/ingest/find_helpers.py",
        weights=None,
        status="serving",
    ),
    ModelCard(
        name="hosted-llm",
        block="B04",
        purpose="Claude or OpenAI, for prompts that need text or code generated",
        kind="hosted",
        code="ml/serving/llm.py",
        weights=None,
        status="optional",
    ),
)


# Input: none
# Output: list[dict]
# Processing: Every model card as JSON, with whether its weights are present.
# Changed by: Sourish
# Date: 2026-10-22
def as_json() -> list[dict]:
    return [asdict(card) | {"present": card.present} for card in MODELS]


# Input: argv (list[str] | None)
# Output: int
# Processing: Print the registry as a table, or as JSON with --json.
# Changed by: Sourish
# Date: 2026-10-22
def main(argv: list[str] | None = None) -> int:
    if "--json" in (sys.argv[1:] if argv is None else argv):
        print(json.dumps(as_json(), indent=2))
        return 0
    for card in MODELS:
        mark = "ok " if card.present else "-- "
        accuracy = f"  @1 {card.accuracy['@1']:.1%}" if card.accuracy else ""
        print(f"{mark}{card.name:22} {card.kind:14} {card.status:12}{accuracy}")
        print(f"    {card.purpose}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
