"""Read a harvested ``entries.jsonl`` back into :class:`Entry` objects.

The harvester writes a wide record -- licence text, attribution header, commit
SHA, the review flags -- because that file is the audit trail for where every
line of code came from. Training and retrieval need a much narrower view, so
this module is the one place that knows how to go from the stored record to
the in-memory type, and the rest of the codebase never parses the file.

Three things here are deliberate rather than incidental.

**Usage signals are not invented.** ``usage_count``, ``impressions``,
``accepts`` and ``mutation_score`` stay at zero, because the library has never
been served to anyone and so has no usage history. Filling them with plausible
random numbers would let a ranker train on them and would make the trained
model score better on every metric, while what it had actually learned was the
shape of the random number generator. Most of the Stage 1 features are
therefore constant on this dataset; that is a true statement about the data,
and :func:`feature_support` reports it rather than leaving a reader to infer
it from a zero-gain row in an importance table.

**``needs_review`` is not used as a filter.** It is set for every record that
lacks tests and a mutation score, which is every record the harvester has ever
written, so filtering on it discards the whole library. What it actually marks,
case by case, is in ``missing``.

**An authored description and a derived one are kept apart.** The harvester
synthesises a description from the identifier when a unit has no docstring,
and deliberately leaves ``description`` in ``missing`` to say so. Those entries
belong in the library -- they are retrievable, and they make good distractors
-- but a training query generated from one is the function's own name reworded,
which teaches a ranker to match identifiers rather than intent. So
:class:`Dataset` carries the two id sets separately and lets the caller decide:
the library is every entry, the query sources are the authored ones.
"""

from __future__ import annotations

import json
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from pathlib import Path

from ml.schemas import Entry, EntryType, Visibility

DEFAULT_DATASET = Path("seed/entries.jsonl")
DEFAULT_QUERIES = Path("seed/queries.jsonl")

DESCRIPTION_AUTHORED = "docstring"
"""``description_source`` value meaning the description is the author's own."""


# Input: record (dict)
# Output: Entry | None
# Processing: Convert one stored harvest record into an Entry
# Changed by: Claude Code
# Date: 2026-10-12
def entry_from_record(record: dict) -> Entry | None:
    """Build an :class:`Entry` from one harvested record, or ``None`` to skip.

    Returns ``None`` rather than raising for a record with no description at
    all. Such a record cannot be matched by the vector arm and cannot generate
    a training query, so it is not a borderline case -- but it is also not an
    error in the file, just a unit the describer could not say anything about.
    """
    description = (record.get("description") or "").strip()
    if not description:
        return None

    metrics = record.get("code_metrics") or {}
    return Entry(
        id=record["id"],
        type=EntryType(record.get("type", "helper")),
        name=record["name"],
        description=description,
        category=record.get("category", "uncategorised"),
        language=record["language"],
        signature=record.get("signature") or None,
        docstring=record.get("docstring", ""),
        code=record.get("code", ""),
        tags=tuple(record.get("tags") or ()),
        framework=record.get("framework"),
        license=record.get("license_spdx", "MIT"),
        owner_id=record.get("author"),
        visibility=Visibility.PUBLIC,
        dependencies=tuple(record.get("dependencies") or ()),
        members=metrics.get("members"),
        # Usage signals stay at zero -- see the module docstring. The library
        # has served nobody, so there is nothing to put here that is true.
        usage_count=0,
        impressions=0,
        accepts=0,
        mutation_score=0.0,
        last_retest_at=None,
    )


@dataclass(frozen=True)
class Dataset:
    """The library, plus which of its entries have an authored description."""

    entries: tuple[Entry, ...]
    authored_ids: frozenset[str]

    # Input: none
    # Output: int
    # Processing: Entry count
    # Changed by: Claude Code
    # Date: 2026-10-12
    def __len__(self) -> int:
        return len(self.entries)

    # Input: none
    # Output: list[Entry]
    # Processing: The entries whose description is the author's own words
    # Changed by: Claude Code
    # Date: 2026-10-12
    @property
    def authored(self) -> list[Entry]:
        """Entries fit to generate a training query from.

        Everything else in the library is still a candidate and still a
        negative; it just cannot be a query source. See the module docstring.
        """
        return [e for e in self.entries if e.id in self.authored_ids]

    # Input: none
    # Output: list[Entry]
    # Processing: The entries whose description was derived from the identifier
    # Changed by: Claude Code
    # Date: 2026-10-12
    @property
    def derived(self) -> list[Entry]:
        return [e for e in self.entries if e.id not in self.authored_ids]

    # Input: none
    # Output: dict[str, int]
    # Processing: Entry counts per language
    # Changed by: Claude Code
    # Date: 2026-10-12
    def by_language(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for entry in self.entries:
            counts[entry.language] = counts.get(entry.language, 0) + 1
        return counts


# Input: path (Path | str)
# Output: Iterator[tuple[Entry, bool]]
# Processing: Stream (entry, description_is_authored) pairs from a JSONL file
# Changed by: Claude Code
# Date: 2026-10-12
def iter_entries(path: Path | str = DEFAULT_DATASET) -> Iterator[tuple[Entry, bool]]:
    """Yield ``(entry, authored)`` for every usable record, in file order."""
    with Path(path).open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"{path}:{line_number} is not valid JSON: {exc}") from exc
            entry = entry_from_record(record)
            if entry is not None:
                yield entry, record.get("description_source") == DESCRIPTION_AUTHORED


# Input: path (Path | str), languages (Sequence[str] | None)
# Output: Dataset
# Processing: Load the harvested library, optionally restricted to languages
# Changed by: Claude Code
# Date: 2026-10-12
def load_dataset(
    path: Path | str = DEFAULT_DATASET, *, languages: Sequence[str] | None = None
) -> Dataset:
    """Load the dataset. Duplicate ids are a hard error.

    Two entries sharing an id would silently shadow each other in the entry
    store, and the one that lost would be unretrievable while still appearing
    in every count -- a library that reports 515 entries and can only ever
    return 514.
    """
    wanted = {lang.lower() for lang in languages} if languages else None
    entries: list[Entry] = []
    authored: set[str] = set()
    seen: set[str] = set()
    for entry, is_authored in iter_entries(path):
        if wanted is not None and entry.language.lower() not in wanted:
            continue
        if entry.id in seen:
            raise ValueError(f"duplicate entry id in {path}: {entry.id!r}")
        seen.add(entry.id)
        entries.append(entry)
        if is_authored:
            authored.add(entry.id)
    return Dataset(entries=tuple(entries), authored_ids=frozenset(authored))


# Input: entries (Sequence[Entry])
# Output: dict[str, bool]
# Processing: Report which usage-derived fields vary on this dataset
# Changed by: Claude Code
# Date: 2026-10-12
def feature_support(entries: Sequence[Entry]) -> dict[str, bool]:
    """Which usage-derived fields actually vary across these entries.

    A feature that is constant cannot be split on, so a model trained here has
    learned nothing about it -- whatever its importance table says. Reported so
    a training run states that up front.
    """

    # Input: values (list)
    # Output: bool
    # Processing: Whether a column holds more than one distinct value
    # Changed by: Claude Code
    # Date: 2026-10-12
    def varies(values: list) -> bool:
        return len(set(values)) > 1

    return {
        "usage_count": varies([e.usage_count for e in entries]),
        "impressions": varies([e.impressions for e in entries]),
        "accepts": varies([e.accepts for e in entries]),
        "mutation_score": varies([e.mutation_score for e in entries]),
        "last_retest_at": varies([e.last_retest_at for e in entries]),
        "owner_id": varies([e.owner_id for e in entries]),
        "dependencies": varies([e.dependencies for e in entries]),
    }


__all__ = [
    "DEFAULT_DATASET",
    "DEFAULT_QUERIES",
    "DESCRIPTION_AUTHORED",
    "Dataset",
    "entry_from_record",
    "feature_support",
    "iter_entries",
    "load_dataset",
    "load_queries",
]


# Input: path (Path | str), entry_ids (Collection[str] | None)
# Output: list[SyntheticQuery]
# Processing: Load hand-written training queries from a JSONL file
# Changed by: Claude Code
# Date: 2026-10-12
def load_queries(path: Path | str = DEFAULT_QUERIES, entry_ids=None):
    """Load training queries written by hand rather than generated.

    Every row keeps the ``source`` recorded in the file, and the writer puts
    ``synthetic`` in that string on purpose: ``GoldQuery`` refuses any source
    containing it, so a query loaded here cannot be promoted into an
    evaluation set no matter what later code does with it. Queries written by
    someone who has read the entry make that entry correct by construction --
    the same defect as a generated query, however natural the wording.

    A query naming an entry that is not in ``entry_ids`` is dropped: the
    dataset and the query file are regenerated independently, so a stale query
    file would otherwise contribute groups whose target cannot be retrieved.
    """
    from ml.evaluation.synthetic import SyntheticQuery

    queries = []
    with Path(path).open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"{path}:{line_number} is not valid JSON: {exc}") from exc
            if entry_ids is not None and row["entry_id"] not in entry_ids:
                continue
            queries.append(
                SyntheticQuery(
                    query=row["query"],
                    entry_id=row["entry_id"],
                    language=row["language"],
                    source=row.get("source", "synthetic"),
                    generator=row.get("generator", "hand"),
                )
            )
    return queries
