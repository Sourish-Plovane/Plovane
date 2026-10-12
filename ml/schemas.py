"""Request, entry, candidate and response types.

The response shape is fixed by the spec: a short ordered list of slim cards,
best match first, each with a calibrated score, and nothing else. The agent
spends as few tokens as possible deciding what to use, so anything the agent
cannot act on -- the model version above all -- is logged on the server and
left out of the payload.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any

import numpy as np


class EntryType(str, Enum):
    HELPER = "helper"
    TEMPLATE = "template"
    BUNDLE = "bundle"


class Visibility(str, Enum):
    PUBLIC = "public"
    PRIVATE = "private"


class DependencyCompat(str, Enum):
    """Ordered best to worst; Stage 1 consumes the ordinal."""

    COMPATIBLE = "compatible"
    NEEDS_INSTALL = "needs_install"
    CONFLICTS = "conflicts"

    # Input: none
    # Output: int
    # Processing: Ordinal
    # Changed by: Claude Code
    # Date: 2026-10-12
    @property
    def ordinal(self) -> int:
        return {"compatible": 0, "needs_install": 1, "conflicts": 2}[self.value]


@dataclass(frozen=True)
class ProjectContext:
    """What the agent knows about the project it is editing.

    Everything here feeds the retrieval query and the cross-encoder's query
    side, so a field left empty costs accuracy rather than breaking anything.
    """

    languages: tuple[str, ...] = ()
    framework: str | None = None
    category: str | None = None
    dependencies: tuple[str, ...] = ()
    file_summary: str | None = None

    # Input: raw (dict[str, Any])
    # Output: ProjectContext
    # Processing: From dict
    # Changed by: Claude Code
    # Date: 2026-10-12
    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> ProjectContext:
        return cls(
            languages=tuple(raw.get("languages") or ()),
            framework=raw.get("framework"),
            category=raw.get("category"),
            dependencies=tuple(raw.get("dependencies") or ()),
            file_summary=raw.get("file_summary"),
        )


@dataclass(frozen=True)
class Request:
    """One recommendation request from an agent."""

    query: str
    project_context: ProjectContext = field(default_factory=ProjectContext)
    k: int = 3
    requester_id: str | None = None
    drop_in_only: bool = False
    """When set, the agent wants code it can paste without installing anything,
    so dependency compatibility becomes a filter rather than a feature."""

    # Input: raw (dict[str, Any])
    # Output: Request
    # Processing: From dict
    # Changed by: Claude Code
    # Date: 2026-10-12
    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> Request:
        return cls(
            query=raw["query"],
            project_context=ProjectContext.from_dict(raw.get("project_context") or {}),
            k=int(raw.get("k", 3)),
            requester_id=raw.get("requester_id"),
            drop_in_only=bool(raw.get("drop_in_only", False)),
        )


@dataclass
class Entry:
    """A library entry: a helper, a template or a bundle of both."""

    id: str
    type: EntryType
    name: str
    description: str
    category: str
    language: str
    signature: str | None = None
    docstring: str = ""
    code: str = ""
    tags: tuple[str, ...] = ()
    framework: str | None = None
    license: str = "MIT"
    owner_id: str | None = None
    visibility: Visibility = Visibility.PUBLIC
    dependencies: tuple[str, ...] = ()
    members: int | None = None

    # Usage signals, maintained by the serving layer and read as features.
    usage_count: int = 0
    impressions: int = 0
    accepts: int = 0
    mutation_score: float = 0.0
    last_retest_at: datetime | None = None

    embedding: np.ndarray | None = None

    # Input: none
    # Output: float
    # Processing: Raw rate. Stage 1 uses the smoothed version, not this one.
    # Changed by: Claude Code
    # Date: 2026-10-12
    @property
    def acceptance_rate(self) -> float:
        """Raw rate. Stage 1 uses the smoothed version, not this one."""
        return self.accepts / self.impressions if self.impressions else 0.0

    # Input: none
    # Output: str
    # Processing: The keyword-index document: name, signature, docstring, tags and
    # Changed by: Claude Code
    # Date: 2026-10-12
    def indexed_text(self) -> str:
        """The keyword-index document: name, signature, docstring, tags and
        generated description. Code is deliberately left out -- it is the
        cross-encoder's input, and indexing it floods BM25 with boilerplate."""
        parts = [self.name, self.signature or "", self.docstring, " ".join(self.tags), self.description]
        return "\n".join(p for p in parts if p)

    # Input: now (datetime | None)
    # Output: float
    # Processing: Large value when never retested, so staleness is never rewarded.
    # Changed by: Claude Code
    # Date: 2026-10-12
    def days_since_retest(self, now: datetime | None = None) -> float:
        """Large value when never retested, so staleness is never rewarded."""
        if self.last_retest_at is None:
            return 365.0
        now = now or datetime.now(timezone.utc)
        return max(0.0, (now - self.last_retest_at).total_seconds() / 86400.0)


@dataclass
class Candidate:
    """An entry carrying the scores accumulated as it moves down the stack."""

    entry: Entry

    # Retrieval
    bm25_score: float = 0.0
    bm25_rank: int | None = None
    cosine_score: float = 0.0
    vector_rank: int | None = None
    rrf_score: float = 0.0
    rrf_rank: int | None = None

    # Ranking
    stage1_score: float | None = None
    cross_encoder_score: float | None = None
    final_score: float | None = None
    calibrated_score: float | None = None

    # Input: none
    # Output: str
    # Processing: Id
    # Changed by: Claude Code
    # Date: 2026-10-12
    @property
    def id(self) -> str:
        return self.entry.id


@dataclass(frozen=True)
class SlimCard:
    """What the agent actually receives per result."""

    id: str
    type: EntryType
    name: str
    description: str
    category: str
    language: str
    score: float
    signature: str | None = None
    members: int | None = None

    # Input: candidate (Candidate), score (float)
    # Output: SlimCard
    # Processing: From candidate
    # Changed by: Claude Code
    # Date: 2026-10-12
    @classmethod
    def from_candidate(cls, candidate: Candidate, score: float) -> SlimCard:
        entry = candidate.entry
        is_helper = entry.type is EntryType.HELPER
        return cls(
            id=entry.id,
            type=entry.type,
            name=entry.name,
            description=entry.description,
            category=entry.category,
            language=entry.language,
            score=round(float(score), 2),
            # Templates and bundles are not called, so they carry no signature.
            signature=entry.signature if is_helper else None,
            members=entry.members if entry.type is EntryType.BUNDLE else None,
        )

    # Input: none
    # Output: dict[str, Any]
    # Processing: To dict
    # Changed by: Claude Code
    # Date: 2026-10-12
    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "id": self.id,
            "type": self.type.value,
            "name": self.name,
        }
        if self.signature is not None:
            out["signature"] = self.signature
        out["description"] = self.description
        out["category"] = self.category
        out["language"] = self.language
        if self.members is not None:
            out["members"] = self.members
        out["score"] = self.score
        return out


@dataclass(frozen=True)
class Response:
    """The payload sent to the agent. No model version: that is logged."""

    query_id: str
    results: tuple[SlimCard, ...] = ()
    reason: str | None = None

    # Input: none
    # Output: dict[str, Any]
    # Processing: To dict
    # Changed by: Claude Code
    # Date: 2026-10-12
    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "query_id": self.query_id,
            "results": [card.to_dict() for card in self.results],
        }
        if self.reason is not None:
            out["reason"] = self.reason
        return out


@dataclass(frozen=True)
class RecommendationLog:
    """Server-side record of one response.

    Every recommendation logs the model version that produced it, so any
    regression can be traced to a release and rolled back. ``query_id`` ties
    the record to the agent's later accept or reject, which is what turns
    serving traffic into ranker training data.
    """

    query_id: str
    model_versions: dict[str, str]
    query: str
    project_context: ProjectContext
    candidates_retrieved: int
    returned: tuple[str, ...]
    scores: tuple[float, ...]
    cutoff: float
    cutoff_enforced: bool = True
    """Whether the relevance cutoff could actually be applied.

    False when no calibrator was fitted. The spec's promise to return nothing
    rather than a poor match rests on the score being a probability, and an
    uncalibrated score is not one -- on this library the uncalibrated sigmoid
    spans 0.73 to 0.98, so a 0.5 cutoff passed 100 candidates out of 100 and a
    nonsense query came back with three confident results. Logging the flag
    keeps a response that skipped the cutoff distinguishable from one that
    passed it, which matters when these records become training data.
    """

    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
