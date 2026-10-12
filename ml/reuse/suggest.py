"""The verdict: save this, reuse what exists, improve it, or say nothing.

One similarity pass against the library drives all five outcomes. The order of
the checks is the design -- saturation is tested before "it already exists",
because a crowded neighbourhood means staying quiet whether or not there is an
exact twin, and surfacing the twin first would send the user to one of fifty
interchangeable entries as though it were the answer.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

import numpy as np

from ml.reuse.scoring import ReusabilityScore, score_unit
from ml.reuse.similarity import (
    SATURATION_LIMIT,
    LibraryIndex,
    Match,
    SimilarityReport,
)

PUBLISH_THRESHOLD = 0.55
"""Reusability below which novel code is still not worth saving.

The library's value is that an entry is worth finding. Admitting everything
novel fills it with one-off glue and makes every later search worse, so
novelty alone is not enough.
"""

IMPROVEMENT_MARGIN = 0.10
"""How much better a candidate must score to be offered as an improvement.

A margin, not a comparison, because the scores are heuristics: calling a 0.61
an improvement on a 0.60 would churn library entries on noise.
"""


class Verdict(str, Enum):
    REUSE_EXISTING = "reuse_existing"
    IMPROVE_EXISTING = "improve_existing"
    PUBLISH_NEW = "publish_new"
    SKIP_SATURATED = "skip_saturated"
    SKIP_NOT_WORTH = "skip_not_worth"

    @property
    def is_suggestion(self) -> bool:
        """Whether this verdict says anything to the user at all."""
        return self in {Verdict.IMPROVE_EXISTING, Verdict.PUBLISH_NEW, Verdict.REUSE_EXISTING}


@dataclass(frozen=True)
class Suggestion:
    """What to tell the user about one piece of their code."""

    verdict: Verdict
    reason: str
    score: ReusabilityScore
    existing: Match | None = None
    saturation: int = 0

    @property
    def should_prompt(self) -> bool:
        return self.verdict.is_suggestion

    def as_dict(self) -> dict[str, object]:
        return {
            "verdict": self.verdict.value,
            "reason": self.reason,
            "score": self.score.as_dict(),
            "existing_id": self.existing.entry.id if self.existing else None,
            "existing_similarity": round(self.existing.similarity, 3) if self.existing else None,
            "saturation": self.saturation,
        }


@dataclass
class ReusabilitySuggester:
    """Decides what, if anything, to say about a candidate."""

    library: LibraryIndex
    publish_threshold: float = PUBLISH_THRESHOLD
    saturation_limit: int = SATURATION_LIMIT
    improvement_margin: float = IMPROVEMENT_MARGIN

    def judge(
        self,
        unit,
        embedding: np.ndarray | None,
        *,
        tokens_spent: int | None = None,
        exclude_ids: tuple[str, ...] = (),
        existing_score: float | None = None,
    ) -> Suggestion:
        """Decide one candidate's fate.

        ``existing_score`` is the library entry's own reusability score, needed
        to tell an improvement from a sideways move. When it is unknown the
        candidate is never promoted to ``IMPROVE_EXISTING``: offering to
        replace a verified, tested entry on a guess is the one error here with
        a real cost, since the existing entry has passed the sandbox and the
        candidate has not.
        """
        score = score_unit(unit, tokens_spent=tokens_spent)

        report: SimilarityReport = (
            self.library.compare(
                embedding, exclude_ids=exclude_ids, language=unit.language
            )
            if embedding is not None
            else SimilarityReport(best=None, near=())
        )

        # Nothing like it in the library.
        if not report.has_duplicate:
            if score.score >= self.publish_threshold:
                return Suggestion(
                    verdict=Verdict.PUBLISH_NEW,
                    reason=f"nothing comparable in the library and it scores {score.score:.2f}",
                    score=score,
                    saturation=report.saturation,
                )
            return Suggestion(
                verdict=Verdict.SKIP_NOT_WORTH,
                reason=self._why_not_worth(score),
                score=score,
                saturation=report.saturation,
            )

        # Something like it exists. Is the neighbourhood already crowded?
        if report.is_saturated(self.saturation_limit):
            return Suggestion(
                verdict=Verdict.SKIP_SATURATED,
                reason=(
                    f"{report.saturation} comparable entries already exist; another adds nothing"
                ),
                score=score,
                existing=report.best,
                saturation=report.saturation,
            )

        best = report.best
        assert best is not None  # has_duplicate implies a best match

        if existing_score is not None and score.score >= existing_score + self.improvement_margin:
            return Suggestion(
                verdict=Verdict.IMPROVE_EXISTING,
                reason=(
                    f"{best.entry.id} matches at {best.similarity:.2f} but scores "
                    f"{existing_score:.2f} against this candidate's {score.score:.2f}"
                ),
                score=score,
                existing=best,
                saturation=report.saturation,
            )

        return Suggestion(
            verdict=Verdict.REUSE_EXISTING,
            reason=f"{best.entry.id} already does this, matching at {best.similarity:.2f}",
            score=score,
            existing=best,
            saturation=report.saturation,
        )

    @staticmethod
    def _why_not_worth(score: ReusabilityScore) -> str:
        """Name the weakest signal, so the user is not told merely "no".

        Ordered by how decisive each failure is, not by weight: a unit that
        cannot run is unreusable whatever its documentation says.
        """
        explanations = {
            "not self-contained": "depends on names from its own module, so it cannot be lifted out",
            "tied to one project": "hardcoded values tie it to this project",
            "no description": "no description, so nothing would find it later",
        }
        if score.blockers:
            return "; ".join(explanations[b] for b in score.blockers)
        if score.lines < 3:
            return "too small to be worth a library entry"
        return f"reusability {score.score:.2f} is below the {PUBLISH_THRESHOLD:.2f} bar"
