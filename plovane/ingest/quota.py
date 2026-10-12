"""How the 1,200 seed entries are divided up.

A harvest left to its own devices returns what GitHub has most of: thousands of
JavaScript utilities and almost no CUDA. That would quietly break the spec's
requirement to report every metric per language, because a language with a
handful of entries cannot be measured -- its NDCG@10 swings by whole points on
one query, so a regression there is indistinguishable from noise.

So the plan allocates deliberately, in two passes. Every MVP language first
gets a floor large enough for its own metrics to mean something; the remainder
is then distributed by how much of the library each language plausibly
supports. The floor is the part that matters: it is what buys a usable CUDA
number, and CUDA is exactly where the spec warns training data is scarcest.

The category list below is **not** from the spec, which only names
``data-processing``, ``machine-learning`` and ``scaffolding`` in passing. It is
a proposal, and it is the first thing to correct if it does not match how the
library is actually organised.
"""

from __future__ import annotations

from dataclasses import dataclass

from plovane.config import MVP_LANGUAGES

CATEGORIES: tuple[str, ...] = (
    "data-processing",
    "machine-learning",
    "api-http",
    "auth-security",
    "database",
    "testing",
    "ui-components",
    "styling-layout",
    "build-tooling",
    "file-io",
    "math-numeric",
    "gpu-kernels",
    "concurrency-async",
    "validation-parsing",
    "logging-observability",
    "scaffolding",
)

CATEGORY_LANGUAGES: dict[str, tuple[str, ...]] = {
    # Only the pairs that genuinely exist. Asking for a CUDA UI component or a
    # CSS database helper wastes search budget and returns noise.
    "data-processing": ("python", "javascript", "cpp"),
    "machine-learning": ("python", "cpp", "cuda"),
    "api-http": ("python", "javascript"),
    "auth-security": ("python", "javascript"),
    "database": ("python", "javascript"),
    "testing": ("python", "javascript", "cpp"),
    "ui-components": ("javascript", "html"),
    "styling-layout": ("css", "html"),
    "build-tooling": ("javascript", "python"),
    "file-io": ("python", "javascript", "cpp"),
    "math-numeric": ("python", "cpp", "cuda"),
    "gpu-kernels": ("cuda", "cpp"),
    "concurrency-async": ("python", "javascript", "cpp"),
    "validation-parsing": ("python", "javascript"),
    "logging-observability": ("python", "javascript"),
    "scaffolding": ("javascript", "python", "html", "css", "cpp", "cuda"),
}

LANGUAGE_WEIGHTS: dict[str, float] = {
    # Rough share of a general code library each language supports, used only
    # to split the remainder after every language has its floor.
    "python": 3.0,
    "javascript": 3.0,
    "cpp": 1.6,
    "css": 1.0,
    "html": 1.0,
    "cuda": 0.6,
}

MINIMUM_PER_LANGUAGE = 60
"""Floor per MVP language, so per-language metrics are not noise.

Sixty entries is not a statistical guarantee; it is the point at which a
per-language NDCG@10 stops moving by more than a point on a single query. Raise
it if the gold set grows.
"""

TEMPLATE_SHARE = 0.25
"""Share of each cell harvested as templates rather than helpers.

The library serves both, and the template recommender needs its own training
population. A quarter is a guess to revise once real query mix is known.
"""


@dataclass(frozen=True)
class Cell:
    """One (category, language) bucket of the plan."""

    category: str
    language: str
    total: int

    @property
    def templates(self) -> int:
        return round(self.total * TEMPLATE_SHARE)

    @property
    def helpers(self) -> int:
        return self.total - self.templates


@dataclass
class HarvestPlan:
    target: int
    cells: tuple[Cell, ...]

    @property
    def allocated(self) -> int:
        return sum(cell.total for cell in self.cells)

    def by_language(self) -> dict[str, int]:
        totals: dict[str, int] = {}
        for cell in self.cells:
            totals[cell.language] = totals.get(cell.language, 0) + cell.total
        return dict(sorted(totals.items()))

    def by_category(self) -> dict[str, int]:
        totals: dict[str, int] = {}
        for cell in self.cells:
            totals[cell.category] = totals.get(cell.category, 0) + cell.total
        return dict(sorted(totals.items()))

    def thin_languages(self, minimum: int = MINIMUM_PER_LANGUAGE) -> tuple[str, ...]:
        """Languages the plan cannot bring up to the floor.

        Worth surfacing before a harvest rather than after: a language that
        cannot be filled is a language whose metrics will not be trustworthy,
        and that is a decision for a person, not a rounding step.
        """
        counts = self.by_language()
        return tuple(
            lang for lang in MVP_LANGUAGES if counts.get(lang, 0) < minimum
        )

    def summary(self) -> str:
        lines = [
            f"Harvest plan: {self.allocated} entries across "
            f"{len(self.by_category())} categories and {len(self.by_language())} languages",
            "",
            "By language:",
        ]
        lines += [f"  {lang:<12} {count:>4}" for lang, count in self.by_language().items()]
        lines += ["", "By category:"]
        lines += [f"  {cat:<24} {count:>4}" for cat, count in self.by_category().items()]
        thin = self.thin_languages()
        if thin:
            lines += ["", f"WARNING: below the {MINIMUM_PER_LANGUAGE}-entry floor: {', '.join(thin)}"]
        return "\n".join(lines)


def _cells_for_language(language: str) -> list[str]:
    return [cat for cat, langs in CATEGORY_LANGUAGES.items() if language in langs]


def build_plan(
    target: int = 1200,
    *,
    minimum_per_language: int = MINIMUM_PER_LANGUAGE,
    languages: tuple[str, ...] = MVP_LANGUAGES,
) -> HarvestPlan:
    """Allocate ``target`` entries across the feasible (category, language) cells."""
    if target <= 0:
        raise ValueError(f"target must be positive, got {target}")

    floor_total = minimum_per_language * len(languages)
    if floor_total > target:
        raise ValueError(
            f"target {target} cannot give {len(languages)} languages a floor of "
            f"{minimum_per_language} each ({floor_total} needed)"
        )

    # Pass 1: the floor, so no language is left unmeasurable.
    per_language = {lang: minimum_per_language for lang in languages}

    # Pass 2: the remainder, by weight.
    remainder = target - floor_total
    weights = {lang: LANGUAGE_WEIGHTS.get(lang, 1.0) for lang in languages}
    weight_total = sum(weights.values())
    for lang in languages:
        per_language[lang] += int(remainder * weights[lang] / weight_total)

    # Rounding leftovers go to the heaviest languages, which have the deepest
    # pool to draw from and so are least likely to come up short.
    shortfall = target - sum(per_language.values())
    for lang in sorted(languages, key=lambda l: -weights[l])[: max(0, shortfall)]:
        per_language[lang] += 1

    cells: list[Cell] = []
    for lang in languages:
        categories = _cells_for_language(lang)
        if not categories:
            continue
        budget = per_language[lang]
        base, extra = divmod(budget, len(categories))
        for index, category in enumerate(sorted(categories)):
            total = base + (1 if index < extra else 0)
            if total > 0:
                cells.append(Cell(category=category, language=lang, total=total))

    return HarvestPlan(target=target, cells=tuple(cells))
