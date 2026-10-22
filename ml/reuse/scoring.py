"""How reusable a piece of a user's code is, before it has tests.

Measures what can be read off the code itself. Deliberately excludes anything
requiring execution -- that is the verification sandbox's job, and a score that
pretended to know whether the code is correct would be the same mistake as
calling a structural metric a mutation score.

The token signal is the concept's own insight and the one that is not obvious:
code the AI spent many tokens writing is the code most worth saving, because
reuse recovers those tokens every subsequent time. It is optional, since
recording it depends on the prompt panel, and the score degrades rather than
breaks without it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# Literals that say "this belongs to one project", not "this is a utility".
PROJECT_SPECIFIC = (
    re.compile(r"""['"](?:/|\./|\.\./|[A-Za-z]:\\)[^'"]{3,}['"]"""),  # file paths
    re.compile(r"""['"]https?://[^'"]+['"]"""),                        # URLs
    re.compile(r"""['"][A-Za-z0-9_-]*(?:localhost|127\.0\.0\.1)[^'"]*['"]"""),
    re.compile(r"""\b(?:API_KEY|SECRET|TOKEN|PASSWORD|DATABASE_URL)\b""", re.IGNORECASE),
)

TOKEN_SATURATION = 4000
"""Token spend beyond which the signal stops growing.

Caps it so one expensive generation cannot dominate the score. A 400-token
helper and a 4,000-token one differ a lot; a 4,000 and a 40,000 do not, and
the larger is probably a whole file rather than a helper.
"""


@dataclass(frozen=True)
class ReusabilityScore:
    """Why a candidate is or is not worth saving."""

    self_contained: bool
    has_description: bool
    generality: float
    documentation: float
    token_value: float
    lines: int

    MIN_GENERALITY = 0.5
    BLOCKED_CEILING = 0.4

    # Input: none
    # Output: tuple[str, ...]
    # Processing: List the unmet requirements that block a library entry.
    # Changed by: Claude Code
    # Date: 2026-10-22
    @property
    def blockers(self) -> tuple[str, ...]:
        """Requirements a library entry must meet, not preferences to weigh.

        Three of these started as weights and that was wrong. A function with a
        hardcoded path to one person's home directory scored 0.61 and was
        recommended for publishing, because a neutral token value and a line-
        count bonus carried it over the bar. A missing description did the
        same. Weighted signals trade off; these do not -- code that cannot be
        lifted, cannot be reused elsewhere, or cannot be found later is not a
        library entry at any level of polish.
        """
        reasons = []
        if not self.self_contained:
            reasons.append("not self-contained")
        if self.generality < self.MIN_GENERALITY:
            reasons.append("tied to one project")
        if self.documentation == 0.0:
            reasons.append("no description")
        return tuple(reasons)

    # Input: none
    # Output: float
    # Processing: Weighted total in 0-1, capped when a requirement is unmet.
    # Changed by: Claude Code
    # Date: 2026-10-22
    @property
    def score(self) -> float:
        """Weighted total in 0 to 1, capped when a requirement is unmet.

        Capped rather than zeroed, so the number still ranks: two blocked
        candidates are not equally bad, and a reviewer triaging them wants the
        better one first.
        """
        total = 0.35 * self.generality
        total += 0.25 * self.documentation
        total += 0.25 * self.token_value
        total += 0.15 if 3 <= self.lines <= 80 else 0.0
        total = min(1.0, total)
        if self.blockers:
            total = min(total, self.BLOCKED_CEILING)
        return round(total, 3)

    # Input: none
    # Output: dict[str, object]
    # Processing: The score and its parts as a JSON-ready dict.
    # Changed by: Claude Code
    # Date: 2026-10-22
    def as_dict(self) -> dict[str, object]:
        return {
            "self_contained": self.self_contained,
            "has_description": self.has_description,
            "generality": round(self.generality, 3),
            "documentation": round(self.documentation, 3),
            "token_value": round(self.token_value, 3),
            "lines": self.lines,
            "blockers": list(self.blockers),
            "score": self.score,
        }


# Input: code (str), param_count (int), annotated (int)
# Output: float
# Processing: Score how much code looks like a general utility rather than glue.
# Changed by: Claude Code
# Date: 2026-10-22
def generality(code: str, *, param_count: int, annotated: int) -> float:
    """How much the code looks like a utility rather than one project's glue.

    Hardcoded paths, URLs and credential names are the strongest tell: a
    function with a literal path in it does one thing in one tree. Parameters
    pull the other way, because a parameterised value is a value the caller
    chooses.
    """
    penalty = 0.0
    for pattern in PROJECT_SPECIFIC:
        hits = len(pattern.findall(code))
        penalty += 0.25 * min(hits, 3)

    reward = 0.0
    if param_count > 0:
        reward += 0.2
        reward += 0.2 * (annotated / param_count)

    return max(0.0, min(1.0, 0.7 + reward - penalty))


# Input: docstring (str)
# Output: float
# Processing: Credit a docstring, more for one that says something.
# Changed by: Claude Code
# Date: 2026-10-22
def documentation(docstring: str) -> float:
    """Credit for a description, with more for one that says something.

    A one-word docstring is barely better than none: the embedding has almost
    nothing to work with, so a query will not find the entry later.
    """
    text = docstring.strip()
    if not text:
        return 0.0
    words = len(text.split())
    if words < 3:
        return 0.3
    if words < 8:
        return 0.7
    return 1.0


# Input: tokens_spent (int | None), saturation (int)
# Output: float
# Processing: Normalise token spend to 0-1, or 0.5 when unknown.
# Changed by: Claude Code
# Date: 2026-10-22
def token_value(tokens_spent: int | None, *, saturation: int = TOKEN_SATURATION) -> float:
    """Normalised token spend, or a neutral 0.5 when it is unknown.

    Neutral rather than zero: an unrecorded token count is missing data, not
    evidence the code was cheap, and treating it as zero would suppress every
    suggestion until the prompt panel ships.
    """
    if tokens_spent is None:
        return 0.5
    if tokens_spent <= 0:
        return 0.0
    return min(1.0, tokens_spent / saturation)


# Input: unit, tokens_spent (int | None)
# Output: ReusabilityScore
# Processing: Score an extracted unit's reusability.
# Changed by: Claude Code
# Date: 2026-10-22
def score_unit(unit, *, tokens_spent: int | None = None) -> ReusabilityScore:
    """Score an :class:`~ml.ingest.extract.ExtractedUnit`."""
    from ml.ingest.metrics import measure

    metrics = measure(unit)
    return ReusabilityScore(
        self_contained=unit.self_contained,
        has_description=bool(unit.docstring.strip()),
        generality=generality(
            unit.code, param_count=metrics.param_count, annotated=metrics.annotated_params
        ),
        documentation=documentation(unit.docstring),
        token_value=token_value(tokens_spent),
        lines=unit.line_count,
    )
