"""Schemas for connection 6: BM12 (B06) ↔ Verified code-library access (B07).

The handbook flags this one in G3 as "connections 6 and 7 only reuse the
code-block shape", which is not enough: BM12 cannot rank without usage,
trust and friction signals the §5.2 block shape omits. These schemas are
the ones the ML code already assumes exist, written down so the handbook
can adopt them.

Design: thin B07, thick B06. B07 is a verified-library *access* service --
a keyword-and-vector search over B08 that returns candidate entries with
every ranking signal present. The ranking itself lives in B06; moving it
into B07 would put the ML models inside the library service and conflate
"what the library knows" with "how B06 ranks it".

Two operations are enough for serving:

1. ``search`` -- what the recommendation flow calls once per request.
2. ``get_blocks`` -- bulk lookup by id, for a second-pass re-serve, an
   audit trail resolve, or a cross-encoder that re-reads only the top 100.

Both return the same block shape (``LibraryBlock``), so a consumer only
has to learn one record.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from ml.schemas import DependencyCompat, EntryType


@dataclass(frozen=True)
class LibrarySearchRequest:
    """What BM12 sends B07 to retrieve candidates for one recommendation.

    Carries both arms' query material because B07 runs both searches and
    fuses -- B06 does not round-trip once per arm. Filters are explicit, so
    a request for "python only, no copyleft, no entries conflicting with the
    user's torch pin" is one hop, not a client-side intersection over three.
    """

    keyword_text: str
    query_vector: tuple[float, ...]
    # Filters, each optional. An omitted filter does not restrict.
    languages: tuple[str, ...] = ()
    entry_types: tuple[EntryType, ...] = ()
    categories: tuple[str, ...] = ()
    frameworks: tuple[str, ...] = ()
    installed_dependencies: tuple[str, ...] = ()
    """``name`` or ``name==version``. Used to compute ``dependency_compat`` on
    each candidate server-side, so the ranker reads a classified field
    instead of inferring compatibility from the raw list."""
    exclude_block_ids: tuple[str, ...] = ()
    """Blocks the client already saw and does not want again -- e.g. a
    re-request after a user rejected the first list."""
    drop_in_only: bool = False
    """When True, blocks requiring an install the project does not already
    have are excluded at retrieval time, not downranked."""
    limit: int = 1000
    """How many candidates B07 should return. The pipeline's default top_k
    for retrieval."""

    # Input: none
    # Output: dict[str, Any]
    # Processing: Serialise to the §6 wire shape
    # Changed by: Sourish
    # Date: 2026-10-13
    def to_handbook_payload(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "keyword_text": self.keyword_text,
            "query_vector": list(self.query_vector),
            "limit": self.limit,
            "drop_in_only": self.drop_in_only,
        }
        if self.languages:
            payload["languages"] = list(self.languages)
        if self.entry_types:
            payload["entry_types"] = [t.value for t in self.entry_types]
        if self.categories:
            payload["categories"] = list(self.categories)
        if self.frameworks:
            payload["frameworks"] = list(self.frameworks)
        if self.installed_dependencies:
            payload["installed_dependencies"] = list(self.installed_dependencies)
        if self.exclude_block_ids:
            payload["exclude_block_ids"] = list(self.exclude_block_ids)
        return payload


@dataclass(frozen=True)
class LibraryBlock:
    """One library entry projected for ranking. The §6 response element.

    Thicker than the §5.2 ``code_blocks[]`` shape because BM12 reads signals
    §5.2 omits: usage counts, acceptance rates, mutation score, last
    retest. Those fields never leave B06 -- the §5.2 projection trims them
    off before serving -- but B07 has to supply them, or B06 cannot rank.
    """

    block_id: str
    name: str
    type: EntryType
    language: str
    category: str
    description: str
    code: str
    license: str
    source_url: str | None = None
    source_permalink: str | None = None
    signature: str | None = None
    dependencies: tuple[str, ...] = ()
    framework: str | None = None
    members: int | None = None
    # Ranking signals.
    bm25_score: float = 0.0
    bm25_rank: int | None = None
    cosine_score: float = 0.0
    vector_rank: int | None = None
    rrf_score: float = 0.0
    rrf_rank: int | None = None
    # Trust and usage, read as features.
    usage_count: int = 0
    impressions: int = 0
    accepts: int = 0
    mutation_score: float = 0.0
    last_retest_at: datetime | None = None
    owner_id: str | None = None
    # Server-computed friction, so BM12 does not re-derive it.
    dependency_compat: DependencyCompat = DependencyCompat.COMPATIBLE

    # Input: none
    # Output: dict[str, Any]
    # Processing: Wire shape for one block in the §6 response
    # Changed by: Sourish
    # Date: 2026-10-13
    def to_handbook_payload(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "block_id": self.block_id,
            "name": self.name,
            "type": self.type.value,
            "language": self.language,
            "category": self.category,
            "description": self.description,
            "code": self.code,
            "license": self.license,
            "dependencies": list(self.dependencies),
            "ranking_signals": {
                "bm25_score": self.bm25_score,
                "bm25_rank": self.bm25_rank,
                "cosine_score": self.cosine_score,
                "vector_rank": self.vector_rank,
                "rrf_score": self.rrf_score,
                "rrf_rank": self.rrf_rank,
                "dependency_compat": self.dependency_compat.value,
            },
            "trust_signals": {
                "usage_count": self.usage_count,
                "impressions": self.impressions,
                "accepts": self.accepts,
                "mutation_score": self.mutation_score,
                "last_retest_at": self.last_retest_at.isoformat() if self.last_retest_at else None,
            },
        }
        if self.signature is not None:
            payload["signature"] = self.signature
        if self.source_url is not None:
            payload["source_url"] = self.source_url
        if self.source_permalink is not None:
            payload["source_permalink"] = self.source_permalink
        if self.framework is not None:
            payload["framework"] = self.framework
        if self.members is not None:
            payload["members"] = self.members
        if self.owner_id is not None:
            payload["owner_id"] = self.owner_id
        return payload


@dataclass(frozen=True)
class LibrarySearchResponse:
    """§6 search response. One ranked list, with how B07 arrived at it."""

    blocks: tuple[LibraryBlock, ...] = ()
    keyword_hits: int = 0
    """How many candidates the keyword arm returned before fusion."""
    vector_hits: int = 0
    """How many candidates the vector arm returned before fusion."""
    shortfall: bool = False
    """True when both arms came back thin against the filter. Logged by B06
    because it almost always means HNSW found its neighbours before a
    filter removed them -- a known issue the spec warns about."""

    # Input: none
    # Output: dict[str, Any]
    # Processing: Serialise to the §6 wire shape
    # Changed by: Sourish
    # Date: 2026-10-13
    def to_handbook_payload(self) -> dict[str, Any]:
        return {
            "blocks": [b.to_handbook_payload() for b in self.blocks],
            "keyword_hits": self.keyword_hits,
            "vector_hits": self.vector_hits,
            "shortfall": self.shortfall,
        }


@dataclass(frozen=True)
class LibraryGetBlocksRequest:
    """Bulk lookup by id. Used for the second pass that re-reads top-100 blocks.

    Projection chooses what the response carries so the second pass is cheap
    when the first already pulled every signal: a cross-encoder re-reading
    the top 100 needs only the text, not another 100 copies of the usage
    counts it already has.
    """

    block_ids: tuple[str, ...] = ()
    projection: str = "ranking"
    """One of ``ranking`` (every signal), ``text`` (code + description only)
    or ``serving`` (what the §5.2 projection needs). See
    ``LibraryBlock.to_handbook_payload`` for the ``ranking`` shape; ``text``
    returns only ``block_id``, ``code``, ``description``, ``signature`` and
    ``license``; ``serving`` returns the §5.2 block fields plus
    ``source_url``."""

    # Input: none
    # Output: dict[str, Any]
    # Processing: Wire shape for one §6 get_blocks request
    # Changed by: Sourish
    # Date: 2026-10-13
    def to_handbook_payload(self) -> dict[str, Any]:
        return {
            "block_ids": list(self.block_ids),
            "projection": self.projection,
        }


__all__ = [
    "LibraryBlock",
    "LibraryGetBlocksRequest",
    "LibrarySearchRequest",
    "LibrarySearchResponse",
]
