"""Retrieval: narrow the whole library to at most 1,000 candidates.

Keyword and vector search run against the same filters, their two ranked lists
are fused with RRF, and the top 1,000 go to ranking. Retrieval's only job is
recall -- the right entry must be somewhere in the 1,000, even if it isn't
first -- so nothing here tries to order results precisely. That is ranking's
job, and it can only work with what retrieval hands it.
"""

from __future__ import annotations

from dataclasses import dataclass

from plovane.config import RETRIEVAL_TOP_K, RRF_K
from plovane.retrieval.embedding import EmbeddingModel
from plovane.retrieval.filters import FilterSpec
from plovane.retrieval.fusion import reciprocal_rank_fusion
from plovane.retrieval.keyword import KeywordIndex
from plovane.retrieval.query import RetrievalQuery, build_query
from plovane.retrieval.vector import VectorIndex
from plovane.schemas import Candidate, Entry, Request


@dataclass
class RetrievalResult:
    query: RetrievalQuery
    candidates: list[Candidate]
    keyword_hits: int
    vector_hits: int

    # Input: none
    # Output: bool
    # Processing: True when both arms came back thin.
    # Changed by: Claude Code
    # Date: 2026-10-12
    @property
    def shortfall(self) -> bool:
        """True when both arms came back thin.

        Worth logging. On a filtered request it usually means HNSW found its
        neighbours before the filter removed them -- see
        :func:`plovane.retrieval.vector.pgvector_session_sql`.
        """
        return len(self.candidates) < min(RETRIEVAL_TOP_K, self.keyword_hits + self.vector_hits)


class RetrievalService:
    """Keyword + vector retrieval with RRF fusion."""

    # Input: keyword_index (KeywordIndex), vector_index (VectorIndex), embedding_model (EmbeddingModel), entry_store (dict[str, Entry]), top_k (int), rrf_k (int)
    # Output: None
    # Processing: Init
    # Changed by: Claude Code
    # Date: 2026-10-12
    def __init__(
        self,
        *,
        keyword_index: KeywordIndex,
        vector_index: VectorIndex,
        embedding_model: EmbeddingModel,
        entry_store: dict[str, Entry],
        top_k: int = RETRIEVAL_TOP_K,
        rrf_k: int = RRF_K,
    ) -> None:
        self.keyword_index = keyword_index
        self.vector_index = vector_index
        self.embedding_model = embedding_model
        self.entry_store = entry_store
        self.top_k = top_k
        self.rrf_k = rrf_k

    # Input: request (Request), filters (FilterSpec | None)
    # Output: RetrievalResult
    # Processing: Retrieve
    # Changed by: Claude Code
    # Date: 2026-10-12
    def retrieve(self, request: Request, *, filters: FilterSpec | None = None) -> RetrievalResult:
        query = build_query(request)
        filters = filters or FilterSpec.from_request(request)

        # Each arm is asked for the full top_k rather than a share of it: the
        # two lists overlap heavily, and splitting the budget costs recall.
        keyword_hits = self.keyword_index.search(query.keyword_text, limit=self.top_k, filters=filters)
        query_vector = self.embedding_model.encode_queries([query.vector_text])[0]
        vector_hits = self.vector_index.search(query_vector, limit=self.top_k, filters=filters)

        bm25 = {entry_id: score for entry_id, score in keyword_hits}
        bm25_rank = {entry_id: rank for rank, (entry_id, _) in enumerate(keyword_hits, start=1)}
        cosine = {entry_id: score for entry_id, score in vector_hits}
        vector_rank = {entry_id: rank for rank, (entry_id, _) in enumerate(vector_hits, start=1)}

        fused = reciprocal_rank_fusion(
            [[entry_id for entry_id, _ in keyword_hits], [entry_id for entry_id, _ in vector_hits]],
            k=self.rrf_k,
        )

        candidates: list[Candidate] = []
        for rrf_rank, (entry_id, rrf_score) in enumerate(fused[: self.top_k], start=1):
            entry = self.entry_store.get(entry_id)
            if entry is None:
                # Index and store disagree -- the entry was deleted between
                # indexing and this query. Skip it rather than serving a card
                # that points at nothing.
                continue
            candidates.append(
                Candidate(
                    entry=entry,
                    bm25_score=bm25.get(entry_id, 0.0),
                    bm25_rank=bm25_rank.get(entry_id),
                    cosine_score=cosine.get(entry_id, 0.0),
                    vector_rank=vector_rank.get(entry_id),
                    rrf_score=rrf_score,
                    rrf_rank=rrf_rank,
                )
            )

        return RetrievalResult(
            query=query,
            candidates=candidates,
            keyword_hits=len(keyword_hits),
            vector_hits=len(vector_hits),
        )
