"""Retrieval: keyword search, vector search, fusion and filters.

``bi_encoder`` is not imported here because it requires torch; import it
directly when you need the trainable embedding model.
"""

from ml.retrieval.embedding import (
    EmbeddingModel,
    HashingEmbeddingModel,
    embed_entries_inplace,
    entry_embedding_text,
)
from ml.retrieval.filters import FilterSpec, classify_dependency_compat
from ml.retrieval.fusion import reciprocal_rank_fusion
from ml.retrieval.keyword import InMemoryBM25Index, KeywordIndex, tokenize
from ml.retrieval.query import RetrievalQuery, build_query
from ml.retrieval.service import RetrievalResult, RetrievalService
from ml.retrieval.vector import InMemoryVectorIndex, VectorIndex, l2_normalize

__all__ = [
    "EmbeddingModel",
    "FilterSpec",
    "HashingEmbeddingModel",
    "InMemoryBM25Index",
    "InMemoryVectorIndex",
    "KeywordIndex",
    "RetrievalQuery",
    "RetrievalResult",
    "RetrievalService",
    "VectorIndex",
    "build_query",
    "classify_dependency_compat",
    "embed_entries_inplace",
    "entry_embedding_text",
    "l2_normalize",
    "reciprocal_rank_fusion",
    "tokenize",
]
