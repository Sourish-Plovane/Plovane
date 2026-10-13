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
    source_url: str | None = None
    """Repository URL. Required for the serving-time attribution contract:
    the response hands the user code they will paste, and the handbook's
    §5.2 shape must carry the credit line with it."""
    source_permalink: str | None = None
    """SHA-pinned link to the exact lines, as recorded at harvest. Logged on
    every served candidate so a disputed attribution is traceable."""
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
    """One result returned by B06 across the §5.2 recommendation contract.

    Carries the handbook's §5.2 fields (``block_id``, ``name``, ``language``,
    ``code``, ``comments``, ``dependencies``) *and* the fields the ML side
    produces that the handbook draft is missing but a UI will need:

    - ``score`` -- calibrated acceptance probability. Lets the UI distinguish
      "paste this" from "worth a look"; the handbook's draft response has no
      way to express that. Rounded to two places so a logistic 0.8137 and a
      logistic 0.8142 do not become distinct cache keys.
    - ``score_is_calibrated`` -- whether the number above is a probability or
      just an ordering. Flipped by the pipeline when no calibrator is
      fitted (see ``Response.cutoff_enforced``), so a client treating 0.9
      as "90% likely accepted" is corrected before it does.
    - ``license`` and ``source_url`` -- the attribution contract. The spec has
      the server hand code the user pastes into their project; dropping the
      SPDX string and the credit URL at the §5.2 boundary means the pasted
      code arrives without the required notice. Required at this schema, not
      bolted on at the UI.
    - ``signature`` -- helper calling signature, so the UI can render it
      before the user commits to pasting.
    """

    id: str
    type: EntryType
    name: str
    description: str
    category: str
    language: str
    score: float
    code: str = ""
    dependencies: tuple[str, ...] = ()
    license: str = ""
    source_url: str | None = None
    source_permalink: str | None = None
    signature: str | None = None
    members: int | None = None
    score_is_calibrated: bool = False

    # Input: candidate (Candidate), score (float), score_is_calibrated (bool)
    # Output: SlimCard
    # Processing: Build a card from a ranked candidate
    # Changed by: Claude Code
    # Date: 2026-10-13
    @classmethod
    def from_candidate(
        cls, candidate: Candidate, score: float, *, score_is_calibrated: bool = False
    ) -> SlimCard:
        entry = candidate.entry
        is_helper = entry.type is EntryType.HELPER
        return cls(
            id=entry.id,
            type=entry.type,
            name=entry.name,
            description=entry.description,
            category=entry.category,
            language=entry.language,
            score=round(float(score), 4),
            code=entry.code,
            dependencies=entry.dependencies,
            license=entry.license,
            source_url=entry.source_url,
            source_permalink=entry.source_permalink,
            signature=entry.signature if is_helper else None,
            members=entry.members if entry.type is EntryType.BUNDLE else None,
            score_is_calibrated=score_is_calibrated,
        )

    # Input: none
    # Output: dict[str, Any]
    # Processing: Full card as the ML pipeline knows it, for internal use
    # Changed by: Claude Code
    # Date: 2026-10-13
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

    # Input: none
    # Output: dict[str, Any]
    # Processing: Serialise to the handbook §5.2 block shape, with the proposed additions
    # Changed by: Claude Code
    # Date: 2026-10-13
    def to_handbook_block(self) -> dict[str, Any]:
        """Serialise to the handbook §5.2 ``code_blocks[]`` element.

        Field names match the draft (``block_id``, not ``id``) so a client
        reading the handbook can consume the response directly. The proposed
        additional fields (``score``, ``score_is_calibrated``, ``license``,
        ``source_url``, ``signature``) ride alongside; the review draft I am
        proposing adds them to §5.2 and the next revision will formalise it.
        A conservative client that does not know the new fields ignores them,
        which is the whole point of ``3.5`` schema-compatibility rule 1
        ("adding an optional field should not break existing consumers").
        """
        block: dict[str, Any] = {
            "block_id": self.id,
            "name": self.name,
            "language": self.language,
            "code": self.code,
            "comments": self.description,
            "dependencies": list(self.dependencies),
            # Proposed §5.2 additions:
            "score": self.score,
            "score_is_calibrated": self.score_is_calibrated,
            "license": self.license,
        }
        if self.signature is not None:
            block["signature"] = self.signature
        if self.source_url is not None:
            block["source_url"] = self.source_url
        if self.source_permalink is not None:
            block["source_permalink"] = self.source_permalink
        return block


@dataclass(frozen=True)
class Response:
    """The payload sent to the agent. No model version: that is logged.

    ``reason`` and ``cutoff_enforced`` together make an empty ``results``
    unambiguous. The handbook §5.2 says the array ``may contain zero, one or
    multiple blocks`` without naming *why* zero -- so a client cannot tell
    "nothing matched well enough" from "the cutoff is not applied yet, these
    were the best we had". The reason strings cover the first case and
    ``cutoff_enforced`` covers the second.
    """

    query_id: str
    results: tuple[SlimCard, ...] = ()
    reason: str | None = None
    cutoff_enforced: bool = True

    # Input: none
    # Output: dict[str, Any]
    # Processing: Internal shape; used by pipeline consumers inside the ML package
    # Changed by: Claude Code
    # Date: 2026-10-13
    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "query_id": self.query_id,
            "results": [card.to_dict() for card in self.results],
        }
        if self.reason is not None:
            out["reason"] = self.reason
        return out

    # Input: none
    # Output: dict[str, Any]
    # Processing: Serialise into the §4.1 envelope with the §5.2 data block
    # Changed by: Claude Code
    # Date: 2026-10-13
    def to_handbook_response(self) -> dict[str, Any]:
        """The exact wire shape B06 should emit across §5.2.

        The §4.1 envelope wraps the §5.2 data: ``ok: true`` with a
        ``data.recommendations`` array of §5.2 blocks on success,
        ``ok: false`` with an ``error`` object on transport failure. A
        recommendation that returned nothing-above-cutoff is still a
        *successful* operation (``ok: true``) with an empty array and a
        reason string -- not an error. That matches the handbook §16.3
        rule that "domain outcomes must be represented as operation results
        when the operation itself completed successfully".
        """
        data: dict[str, Any] = {
            "query_id": self.query_id,
            "recommendations": [card.to_handbook_block() for card in self.results],
            "cutoff_enforced": self.cutoff_enforced,
        }
        if self.reason is not None:
            data["reason"] = self.reason
        return {"ok": True, "data": data, "error": None}


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
