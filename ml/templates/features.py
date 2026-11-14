"""Template-specific features.

The spec keeps separate rankers for templates and helpers, and the reason
shows up most clearly in the features. Two of the shared features mean close
to the opposite thing for a template:

**Dependency compatibility.** For a helper it is friction -- a helper that
needs a new package is worse than one that drops in. For a template it is the
*point*: a FastAPI service template is supposed to bring FastAPI with it.
Penalising a template for its dependencies would rank the emptiest scaffold
first, so the shared ``dependency_compat`` feature is weighted near zero here
and replaced by a plain count of what is new.

**Framework.** A helper is often framework-agnostic. A template usually is
not, and a Next.js template dropped into a Vue project is not a worse
suggestion, it is a useless one. So framework match earns its own feature
rather than being left to the filter, which only removes hard mismatches.

The three features below are *appended* to the shared set. Appending is safe;
reordering is not, because a trained model's splits are bound to positions.
"""

from __future__ import annotations

import math
from collections.abc import Sequence

import numpy as np

from ml.ranking.features import FEATURE_NAMES, FeatureContext, build_feature_matrix
from ml.schemas import Candidate, EntryType

TEMPLATE_EXTRA_FEATURES: tuple[str, ...] = (
    "framework_match",
    "new_dependency_count",
    "member_count",
)

TEMPLATE_FEATURE_NAMES: tuple[str, ...] = (*FEATURE_NAMES, *TEMPLATE_EXTRA_FEATURES)
TEMPLATE_STAGE3_FEATURE_NAMES: tuple[str, ...] = (*TEMPLATE_FEATURE_NAMES, "cross_encoder_score")


# Input: candidate (Candidate), framework (str | None)
# Output: float
# Processing: 1.0 exact match, 0.5 framework-agnostic, 0.0 mismatch.
# Changed by: Sourish
# Date: 2026-10-12
def framework_match(candidate: Candidate, framework: str | None) -> float:
    """1.0 exact match, 0.5 framework-agnostic, 0.0 mismatch.

    The middle value is the one that matters. A template with no framework of
    its own fits anywhere, so it should not be scored as a mismatch; but it is
    also not the targeted answer that an exact match is, so it should not score
    as one either.

    With no framework in the request there is nothing to match on, so every
    candidate gets the neutral 0.5 rather than a misleading 1.0.
    """
    entry_framework = candidate.entry.framework
    if framework is None:
        return 0.5
    if entry_framework is None:
        return 0.5
    return 1.0 if entry_framework.lower() == framework.lower() else 0.0


# Input: candidate (Candidate), installed (Sequence[str])
# Output: float
# Processing: How many of the template's dependencies the project does not have, log-scaled.
# Changed by: Sourish
# Date: 2026-10-12
def new_dependency_count(candidate: Candidate, installed: Sequence[str]) -> float:
    """How many of the template's dependencies the project does not have, log-scaled.

    Informational, not a penalty -- see the module docstring. It is here so the
    trained ranker can learn the shape of the preference from accept data
    rather than having one imposed: all else equal a smaller install is nicer,
    but "all else equal" almost never holds between two templates.
    """
    have = {dep.partition("==")[0].strip().lower() for dep in installed}
    new = sum(1 for dep in candidate.entry.dependencies if dep.partition("==")[0].strip().lower() not in have)
    return math.log1p(new)


# Input: candidate (Candidate)
# Output: float
# Processing: Files or entries the template brings. 1 for a single-file template.
# Changed by: Sourish
# Date: 2026-10-12
def member_count(candidate: Candidate) -> float:
    """Files or entries the template brings. 1 for a single-file template.

    A proxy for scope, and deliberately not signed: "set up a service" wants
    many files, "add a lint config" wants one, so neither direction is better
    in general. Left for the ranker to combine with the query rather than
    given a hand weight.
    """
    entry = candidate.entry
    if entry.members is not None:
        return float(entry.members)
    return 1.0


# Input: candidate (Candidate), ctx (FeatureContext), framework (str | None)
# Output: list[float]
# Processing: Template feature row
# Changed by: Sourish
# Date: 2026-10-12
def template_feature_row(candidate: Candidate, ctx: FeatureContext, *, framework: str | None) -> list[float]:
    return [
        framework_match(candidate, framework),
        new_dependency_count(candidate, ctx.installed_dependencies),
        member_count(candidate),
    ]


# Input: candidates (Sequence[Candidate]), ctx (FeatureContext), framework (str | None)
# Output: np.ndarray
# Processing: Shared features plus the three template features, one row per candidate.
# Changed by: Sourish
# Date: 2026-10-12
def build_template_feature_matrix(
    candidates: Sequence[Candidate], ctx: FeatureContext, *, framework: str | None = None
) -> np.ndarray:
    """Shared features plus the three template features, one row per candidate."""
    base = build_feature_matrix(candidates, ctx)
    if base.size == 0:
        return np.zeros((0, len(TEMPLATE_FEATURE_NAMES)), dtype=np.float32)
    extra = np.asarray(
        [template_feature_row(c, ctx, framework=framework) for c in candidates], dtype=np.float32
    )
    return np.hstack([base, extra])


# Input: candidates (Sequence[Candidate]), ctx (FeatureContext), framework (str | None)
# Output: np.ndarray
# Processing: Template features plus the cross-encoder score, appended last.
# Changed by: Sourish
# Date: 2026-10-12
def build_template_stage3_matrix(
    candidates: Sequence[Candidate], ctx: FeatureContext, *, framework: str | None = None
) -> np.ndarray:
    """Template features plus the cross-encoder score, appended last."""
    base = build_template_feature_matrix(candidates, ctx, framework=framework)
    if base.size == 0:
        return np.zeros((0, len(TEMPLATE_STAGE3_FEATURE_NAMES)), dtype=np.float32)
    ce = np.asarray(
        [[c.cross_encoder_score if c.cross_encoder_score is not None else 0.0] for c in candidates],
        dtype=np.float32,
    )
    return np.hstack([base, ce])


# Input: candidate (Candidate), include_bundles (bool)
# Output: bool
# Processing: Whether a candidate belongs in template results.
# Changed by: Sourish
# Date: 2026-10-12
def is_template_like(candidate: Candidate, *, include_bundles: bool = True) -> bool:
    """Whether a candidate belongs in template results.

    Bundles count by default: the spec describes a bundle as entries that work
    together, which is what a multi-file template is. Set
    ``include_bundles=False`` to return single templates only.
    """
    entry_type = candidate.entry.type
    if entry_type is EntryType.TEMPLATE:
        return True
    return include_bundles and entry_type is EntryType.BUNDLE
