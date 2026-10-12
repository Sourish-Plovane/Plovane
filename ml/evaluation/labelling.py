"""Tooling that makes gold-set labelling cheap enough to actually happen.

The models have no accuracy figure because there is no gold set, and labelling
is the step that cannot be automated away: a query generated from an entry's
own description makes its label true by construction. So this module does not
try to avoid the human work. It tries to reduce it to picking from a shortlist
and checking a sample.

Three pieces, and the third is the one people skip. A sampler that measures
*the labeller* is what separates an LLM-labelled gold set from an unvalidated
instrument being used to validate models.
"""

from __future__ import annotations

import hashlib
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

from ml.config import MVP_LANGUAGES
from ml.evaluation.gold_set import GoldQuery, GoldSet, assign_split
from ml.schemas import Candidate, ProjectContext, Request

SHORTLIST_SIZE = 20
"""Candidates shown per query.

Twenty is a compromise. Too few and the correct entry is often absent, which
turns a labelling task into a retrieval complaint; too many and the labeller
is searching again, which is the thing the shortlist exists to prevent.
"""

GRADE_EXACT = 3.0
GRADE_PARTIAL = 1.0
"""The two grades a labeller assigns. NDCG reads them; recall and MRR read
anything above zero as relevant."""


@dataclass
class Shortlist:
    """One query with the candidates a labeller chooses between."""

    query: str
    language: str
    candidates: tuple[Candidate, ...]
    project_context: ProjectContext = field(default_factory=ProjectContext)
    category: str | None = None
    source: str = "hand-written"

    # Input: none
    # Output: tuple[str, ...]
    # Processing: Candidate ids in shortlist order
    # Changed by: Claude Code
    # Date: 2026-10-12
    @property
    def candidate_ids(self) -> tuple[str, ...]:
        return tuple(c.id for c in self.candidates)

    # Input: entry_id (str)
    # Output: int | None
    # Processing: Position of an entry in the shortlist, 1-indexed
    # Changed by: Claude Code
    # Date: 2026-10-12
    def rank_of(self, entry_id: str) -> int | None:
        for rank, candidate in enumerate(self.candidates, start=1):
            if candidate.id == entry_id:
                return rank
        return None


@dataclass(frozen=True)
class Label:
    """One labelling decision about one query."""

    query: str
    language: str
    relevance: dict[str, float]
    labeller: str
    confidence: float = 1.0
    chosen_rank: int | None = None
    """Where in the shortlist the chosen entry sat.

    Recorded so a labeller that only ever picks rank 1 shows up as a pattern
    rather than being mistaken for agreement with retrieval.
    """

    not_in_shortlist: bool = False
    """The correct entry was absent.

    The most informative outcome in the set: a retrieval failure caught before
    any ranking was measured. Kept rather than dropped, because discarding
    these silently removes exactly the queries retrieval is worst at.
    """

    note: str = ""


@runtime_checkable
class Labeller(Protocol):
    """Picks the relevant entries from a shortlist."""

    name: str

    def label(self, shortlist: Shortlist) -> Label: ...


class LLMLabeller:
    """Labels a shortlist with a language model.

    Importable without a credential, so the module is testable and a harvest
    is never blocked on a key; raises on use instead of degrading, because a
    silent fallback would mix machine labels with something else and leave
    nothing in the record saying which.
    """

    def __init__(self, client=None, *, name: str = "llm") -> None:
        self.client = client
        self.name = name

    # Input: shortlist (Shortlist)
    # Output: str
    # Processing: Render the labelling prompt for one shortlist
    # Changed by: Claude Code
    # Date: 2026-10-12
    def build_prompt(self, shortlist: Shortlist) -> str:
        """Ask for an exact match, a partial one, or neither.

        "Neither" is offered explicitly and first, because a model asked to
        choose from a list will choose from the list. Without a stated way out,
        every query gets a label and the absent-entry case disappears -- which
        is the case worth most.
        """
        lines = [
            "A developer asked for code. Below are candidate library entries.",
            "Reply with the id of the entry that does what was asked, or NONE if",
            "no entry does. Optionally add a second id that partly does.",
            "",
            f"Request: {shortlist.query}",
            f"Language: {shortlist.language}",
            "",
            "Candidates:",
        ]
        for rank, candidate in enumerate(shortlist.candidates, start=1):
            entry = candidate.entry
            lines.append(
                f"{rank}. {entry.id} | {entry.name}{entry.signature or ''} | {entry.description}"
            )
        lines += ["", "Answer with: EXACT=<id|NONE> PARTIAL=<id|NONE>"]
        return "\n".join(lines)

    # Input: shortlist (Shortlist)
    # Output: Label
    # Processing: Ask the model which shortlist entries are relevant
    # Changed by: Claude Code
    # Date: 2026-10-12
    def label(self, shortlist: Shortlist) -> Label:
        if self.client is None:
            raise RuntimeError(
                "LLMLabeller has no client; pass one. There is no offline fallback, "
                "because a guessed label is worse than a missing one"
            )
        reply = str(self.client.complete(self.build_prompt(shortlist)))
        return self.parse_reply(shortlist, reply)

    # Input: shortlist (Shortlist), reply (str)
    # Output: Label
    # Processing: Turn a model reply into a Label, ignoring ids not on offer
    # Changed by: Claude Code
    # Date: 2026-10-12
    def parse_reply(self, shortlist: Shortlist, reply: str) -> Label:
        """Parse ``EXACT=<id> PARTIAL=<id>``.

        An id the model invented is discarded rather than trusted. A labeller
        that hallucinates an entry id would otherwise write a gold label
        pointing at nothing, and every metric computed against it would be
        quietly wrong.
        """
        offered = set(shortlist.candidate_ids)
        relevance: dict[str, float] = {}
        chosen_rank: int | None = None

        for token in reply.replace("\n", " ").split():
            if "=" not in token:
                continue
            key, _, value = token.partition("=")
            key = key.strip().upper()
            value = value.strip().strip(",.;")
            if value.upper() == "NONE" or value not in offered:
                continue
            if key == "EXACT":
                relevance[value] = GRADE_EXACT
                chosen_rank = shortlist.rank_of(value)
            elif key == "PARTIAL":
                relevance.setdefault(value, GRADE_PARTIAL)

        return Label(
            query=shortlist.query,
            language=shortlist.language,
            relevance=relevance,
            labeller=self.name,
            chosen_rank=chosen_rank,
            not_in_shortlist=not relevance,
        )


# Input: requests (Sequence[Request]), retrieval (RetrievalService), size (int)
# Output: list[Shortlist]
# Processing: Retrieve a candidate shortlist for each query
# Changed by: Claude Code
# Date: 2026-10-12
def build_shortlists(
    requests: Sequence[Request],
    retrieval,
    *,
    size: int = SHORTLIST_SIZE,
    sources: Sequence[str] | None = None,
) -> list[Shortlist]:
    """Retrieve candidates per query so a labeller picks instead of searching."""
    shortlists: list[Shortlist] = []
    for index, request in enumerate(requests):
        result = retrieval.retrieve(request)
        language = (
            request.project_context.languages[0]
            if request.project_context.languages
            else "unknown"
        )
        shortlists.append(
            Shortlist(
                query=request.query,
                language=language,
                candidates=tuple(result.candidates[:size]),
                project_context=request.project_context,
                category=request.project_context.category,
                source=sources[index] if sources and index < len(sources) else "hand-written",
            )
        )
    return shortlists


# Input: labels (Sequence[Label]), holdout_fraction (float), validation_fraction (float)
# Output: GoldSet
# Processing: Turn labels into a GoldSet, dropping the unlabelled ones
# Changed by: Claude Code
# Date: 2026-10-12
def gold_set_from_labels(
    labels: Sequence[Label],
    *,
    holdout_fraction: float = 0.3,
    validation_fraction: float = 0.2,
) -> GoldSet:
    """Assemble a :class:`GoldSet`, assigning splits deterministically.

    A label with no relevant entry cannot become a gold query -- ``GoldQuery``
    rejects an empty relevance map, and rightly: a query with no correct answer
    cannot contribute to NDCG. Those rows are still worth keeping as a
    retrieval-failure record, which is what
    :func:`shortlist_recall` reports, so they are counted there rather than
    discarded unnoticed.
    """
    gold = GoldSet()
    for label in labels:
        if not label.relevance:
            continue
        query_id = "g_" + hashlib.blake2b(
            f"{label.language}:{label.query}".encode(), digest_size=6
        ).hexdigest()
        gold.add(
            GoldQuery(
                query_id=query_id,
                query=label.query,
                language=label.language,
                relevance=dict(label.relevance),
                split=assign_split(
                    query_id,
                    holdout_fraction=holdout_fraction,
                    validation_fraction=validation_fraction,
                ),
                source=f"labelled:{label.labeller}",
            )
        )
    return gold


# Input: labels (Sequence[Label])
# Output: dict[str, object]
# Processing: Share of queries whose correct entry reached the shortlist
# Changed by: Claude Code
# Date: 2026-10-12
def shortlist_recall(labels: Sequence[Label]) -> dict[str, object]:
    """How often retrieval put the right entry in front of the labeller.

    A measurement of retrieval taken for free during labelling, and an early
    read on recall before any ranking model is involved. A low number here
    caps every downstream metric, since ranking only reorders what retrieval
    returned.
    """
    if not labels:
        return {"queries": 0, "recall": 0.0, "misses": 0, "by_language": {}}

    misses = sum(1 for label in labels if label.not_in_shortlist)
    by_language: dict[str, dict[str, int]] = {}
    for label in labels:
        bucket = by_language.setdefault(label.language, {"queries": 0, "misses": 0})
        bucket["queries"] += 1
        if label.not_in_shortlist:
            bucket["misses"] += 1

    return {
        "queries": len(labels),
        "recall": round((len(labels) - misses) / len(labels), 4),
        "misses": misses,
        "by_language": {
            lang: {
                **counts,
                "recall": round((counts["queries"] - counts["misses"]) / counts["queries"], 4),
            }
            for lang, counts in sorted(by_language.items())
        },
    }


# Input: labels (Sequence[Label]), per_language (int), languages (Sequence[str])
# Output: list[Label]
# Processing: Stratified sample for a person to check, across every MVP language
# Changed by: Claude Code
# Date: 2026-10-12
def verification_sample(
    labels: Sequence[Label],
    *,
    per_language: int = 8,
    languages: Sequence[str] = MVP_LANGUAGES,
) -> list[Label]:
    """Pick labels for a person to check, stratified by language.

    Stratified rather than random because a random sample of a real label set
    is mostly Python and JavaScript and would say nothing about CUDA, which is
    where the concept warns data is scarcest -- and where a labeller is most
    likely to be wrong.

    Selection is deterministic, by hash of the query, so the same sample comes
    back on every run and two people checking "the sample" check the same one.
    """
    wanted = {lang.lower() for lang in languages}
    grouped: dict[str, list[Label]] = {}
    for label in labels:
        key = label.language.lower()
        if key in wanted:
            grouped.setdefault(key, []).append(label)

    sample: list[Label] = []
    for language in sorted(grouped):
        ordered = sorted(
            grouped[language],
            key=lambda lab: hashlib.blake2b(lab.query.encode(), digest_size=8).digest(),
        )
        sample.extend(ordered[:per_language])
    return sample


# Input: machine (Sequence[Label]), human (Sequence[Label])
# Output: dict[str, object]
# Processing: Agreement between a labeller and the human-checked sample
# Changed by: Claude Code
# Date: 2026-10-12
def agreement(machine: Sequence[Label], human: Sequence[Label]) -> dict[str, object]:
    """Compare a labeller against the verified sample.

    This number decides whether the unverified labels can be used. High
    agreement means the rest of the set is probably sound; low agreement means
    an LLM-labelled gold set would be an unvalidated instrument used to
    validate models, which is worse than having no gold set, because the
    resulting numbers look real.

    Compares top choices only. Partial grades are genuinely debatable between
    two careful people, so counting them as disagreement would understate a
    labeller that is right about what matters.
    """
    by_query = {(lab.language.lower(), lab.query): lab for lab in machine}
    compared = agreed = 0
    disputes: list[dict[str, object]] = []

    for truth in human:
        key = (truth.language.lower(), truth.query)
        candidate = by_query.get(key)
        if candidate is None:
            continue
        compared += 1
        machine_top = _top_choice(candidate)
        human_top = _top_choice(truth)
        if machine_top == human_top:
            agreed += 1
        else:
            disputes.append(
                {"query": truth.query, "labeller": machine_top, "human": human_top}
            )

    return {
        "compared": compared,
        "agreed": agreed,
        "agreement": round(agreed / compared, 4) if compared else 0.0,
        "disputes": disputes,
    }


# Input: label (Label)
# Output: str | None
# Processing: The highest-graded entry id in a label, or None
# Changed by: Claude Code
# Date: 2026-10-12
def _top_choice(label: Label) -> str | None:
    if not label.relevance:
        return None
    return max(sorted(label.relevance), key=lambda entry_id: label.relevance[entry_id])
