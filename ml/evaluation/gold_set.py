"""The gold set: a few hundred hand-labelled queries, and a frozen holdout.

Two things make it worth the labelling effort, and both are easy to lose:

The queries are written from real projects, not from the library's own
descriptions. A query paraphrased from an entry's docstring is a test the
models pass by construction -- it measures nothing except that the embedding
model can match a sentence to itself.

The holdout is frozen and never used for training or tuning. The moment it
informs a hyperparameter it stops being held out, and every metric measured
on it becomes an estimate of how well the model fits the test set.

The set grows monthly from real usage, especially failures, and it must cover
every MVP language and category -- because every metric is reported per
language, and a language absent from the gold set is a language nobody is
measuring.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from ml.config import MVP_LANGUAGES
from ml.schemas import ProjectContext

Split = Literal["train", "validation", "holdout"]


@dataclass(frozen=True)
class GoldQuery:
    """One labelled query: what was asked, and which entries are correct."""

    query_id: str
    query: str
    language: str
    relevance: Mapping[str, float]
    """``entry_id -> grade``. Grades are graded, not binary: 3 for the entry a
    reviewer would pick, 1 for one that would also do. NDCG reads the grades;
    recall and MRR read anything above 0 as relevant."""

    project_context: ProjectContext = field(default_factory=ProjectContext)
    category: str | None = None
    split: Split = "train"
    source: str = "hand-labelled"
    """``hand-labelled`` or ``usage:<date>`` for queries harvested from real
    traffic, so a drift in the set's composition is visible."""

    # Input: none
    # Output: None
    # Processing: Post init
    # Changed by: Sourish
    # Date: 2026-10-12
    def __post_init__(self) -> None:
        if self.source and "synthetic" in self.source.lower():
            # A query generated from an entry's description makes that entry
            # the correct answer by construction, so NDCG@10 measured on it
            # reports paraphrase matching as relevance -- and reports it as a
            # high score. The check lives here, where the object is built,
            # because that failure produces plausible numbers rather than an
            # error and so would never be caught by review.
            raise ValueError(
                f"gold query {self.query_id} is synthetic ({self.source!r}); synthetic "
                "queries are training data and must not enter an evaluation set"
            )
        if not self.relevance:
            raise ValueError(f"gold query {self.query_id} has no relevant entries")
        if any(grade < 0 for grade in self.relevance.values()):
            raise ValueError(f"gold query {self.query_id} has a negative relevance grade")

    # Input: none
    # Output: set[str]
    # Processing: Relevant ids
    # Changed by: Sourish
    # Date: 2026-10-12
    @property
    def relevant_ids(self) -> set[str]:
        return {entry_id for entry_id, grade in self.relevance.items() if grade > 0}


# Input: query_id (str), holdout_fraction (float), validation_fraction (float)
# Output: Split
# Processing: Deterministic split by hash of the query id.
# Changed by: Sourish
# Date: 2026-10-12
def assign_split(query_id: str, *, holdout_fraction: float = 0.3, validation_fraction: float = 0.2) -> Split:
    """Deterministic split by hash of the query id.

    Hashing rather than shuffling, so a query lands in the same split on every
    machine and in every run, forever. A random split re-rolled per run would
    leak holdout queries into training the first time someone forgets a seed,
    and the leak would be invisible.
    """
    if not 0.0 <= holdout_fraction < 1.0 or not 0.0 <= validation_fraction < 1.0:
        raise ValueError("fractions must be in [0, 1)")
    if holdout_fraction + validation_fraction >= 1.0:
        raise ValueError("holdout and validation fractions must leave room for training data")

    digest = hashlib.blake2b(query_id.encode("utf-8"), digest_size=8).digest()
    position = int.from_bytes(digest, "big") / float(1 << 64)
    if position < holdout_fraction:
        return "holdout"
    if position < holdout_fraction + validation_fraction:
        return "validation"
    return "train"


@dataclass
class GoldSet:
    """A labelled query set with its splits and coverage checks."""

    queries: list[GoldQuery] = field(default_factory=list)

    # Input: none
    # Output: int
    # Processing: Len
    # Changed by: Sourish
    # Date: 2026-10-12
    def __len__(self) -> int:
        return len(self.queries)

    # Input: query (GoldQuery)
    # Output: None
    # Processing: Add
    # Changed by: Sourish
    # Date: 2026-10-12
    def add(self, query: GoldQuery) -> None:
        if any(q.query_id == query.query_id for q in self.queries):
            raise ValueError(f"duplicate gold query id {query.query_id}")
        self.queries.append(query)

    # Input: split (Split)
    # Output: list[GoldQuery]
    # Processing: Split
    # Changed by: Sourish
    # Date: 2026-10-12
    def split(self, split: Split) -> list[GoldQuery]:
        return [q for q in self.queries if q.split == split]

    # Input: none
    # Output: list[GoldQuery]
    # Processing: The frozen portion. Read it to measure; never to decide.
    # Changed by: Sourish
    # Date: 2026-10-12
    @property
    def holdout(self) -> list[GoldQuery]:
        """The frozen portion. Read it to measure; never to decide."""
        return self.split("holdout")

    # Input: none
    # Output: dict[str, int]
    # Processing: Languages
    # Changed by: Sourish
    # Date: 2026-10-12
    def languages(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for query in self.queries:
            counts[query.language.lower()] = counts.get(query.language.lower(), 0) + 1
        return counts

    # Input: mvp_languages (Sequence[str])
    # Output: tuple[str, ...]
    # Processing: MVP languages with no labelled queries at all.
    # Changed by: Sourish
    # Date: 2026-10-12
    def missing_languages(self, *, mvp_languages: Sequence[str] = MVP_LANGUAGES) -> tuple[str, ...]:
        """MVP languages with no labelled queries at all."""
        present = self.languages()
        return tuple(lang for lang in mvp_languages if not present.get(lang.lower()))

    # Input: mvp_languages (Sequence[str]), minimum_per_language (int)
    # Output: dict[str, object]
    # Processing: What the set can and cannot measure.
    # Changed by: Sourish
    # Date: 2026-10-12
    def coverage_report(
        self, *, mvp_languages: Sequence[str] = MVP_LANGUAGES, minimum_per_language: int = 20
    ) -> dict[str, object]:
        """What the set can and cannot measure.

        A language below ``minimum_per_language`` is reported as thin: its
        per-language metrics will swing by whole percentage points on a single
        query, so a regression there is indistinguishable from noise.
        """
        counts = self.languages()
        return {
            "total": len(self.queries),
            "by_split": {
                split: len(self.split(split)) for split in ("train", "validation", "holdout")
            },
            "by_language": dict(sorted(counts.items())),
            "missing_languages": list(self.missing_languages(mvp_languages=mvp_languages)),
            "thin_languages": sorted(
                lang for lang in (l.lower() for l in mvp_languages)
                if 0 < counts.get(lang, 0) < minimum_per_language
            ),
            "holdout_languages": sorted({q.language.lower() for q in self.holdout}),
        }

    # -- Persistence -------------------------------------------------------

    # Input: path (str | Path)
    # Output: None
    # Processing: To jsonl
    # Changed by: Sourish
    # Date: 2026-10-12
    def to_jsonl(self, path: str | Path) -> None:
        with Path(path).open("w", encoding="utf-8") as handle:
            for query in self.queries:
                handle.write(
                    json.dumps(
                        {
                            "query_id": query.query_id,
                            "query": query.query,
                            "language": query.language,
                            "relevance": dict(query.relevance),
                            "category": query.category,
                            "split": query.split,
                            "source": query.source,
                            "project_context": {
                                "languages": list(query.project_context.languages),
                                "framework": query.project_context.framework,
                                "category": query.project_context.category,
                                "dependencies": list(query.project_context.dependencies),
                                "file_summary": query.project_context.file_summary,
                            },
                        }
                    )
                    + "\n"
                )

    # Input: path (str | Path)
    # Output: GoldSet
    # Processing: From jsonl
    # Changed by: Sourish
    # Date: 2026-10-12
    @classmethod
    def from_jsonl(cls, path: str | Path) -> GoldSet:
        gold = cls()
        with Path(path).open(encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                raw = json.loads(line)
                gold.add(
                    GoldQuery(
                        query_id=raw["query_id"],
                        query=raw["query"],
                        language=raw["language"],
                        relevance={k: float(v) for k, v in raw["relevance"].items()},
                        project_context=ProjectContext.from_dict(raw.get("project_context") or {}),
                        category=raw.get("category"),
                        split=raw.get("split", "train"),
                        source=raw.get("source", "hand-labelled"),
                    )
                )
        return gold

    # Input: queries (Iterable[GoldQuery]), assign_splits (bool)
    # Output: GoldSet
    # Processing: From queries
    # Changed by: Sourish
    # Date: 2026-10-12
    @classmethod
    def from_queries(cls, queries: Iterable[GoldQuery], *, assign_splits: bool = False) -> GoldSet:
        gold = cls()
        for query in queries:
            if assign_splits:
                from dataclasses import replace

                query = replace(query, split=assign_split(query.query_id))
            gold.add(query)
        return gold
