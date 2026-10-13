"""Tunable constants, all traceable to the technical build specification.

Values the spec gives as starting points are marked as such: they are meant to
be tuned on the validation set, not treated as settled.
"""

from __future__ import annotations

from dataclasses import dataclass, field

# --- Retrieval -------------------------------------------------------------

RETRIEVAL_TOP_K = 1000
"""Candidates handed to ranking. Retrieval's only job is recall@1000."""

RRF_K = 60
"""Reciprocal Rank Fusion constant: each item scores sum(1 / (RRF_K + rank))."""

RRF_ARM_WEIGHTS_PLACEHOLDER_EMBEDDING = (1.0, 0.25)
"""RRF arm weights -- (bm25, cosine) -- used while the vector arm runs on
``HashingEmbeddingModel``.

Measured on all 350 hand-written queries, top-1 hit rate:

===========================  =====  =====
``(w_bm25, w_cos)``             @1     @3
===========================  =====  =====
``(1.0, 1.0)`` (standard)     36.6%  54.6%
``(1.0, 0.50)``               38.6%  54.6%
**``(1.0, 0.25)`` (default)** 40.9%  55.1%
``(1.0, 0.00)`` (bm25 only)   40.0%  55.4%
``(0.0, 1.0)`` (cosine only)  25.7%  44.9%
===========================  =====  =====

The hashing embedding gives 25% @1 on its own, so equal fusion drags the
stronger arm down. The chosen weight keeps a little of the vector arm --
it still rescues cases where BM25 misses -- without letting it dominate.

A workaround, not a tuning knob. ``RetrievalService`` reads this only when
the embedding model is the placeholder, so a real bi-encoder restores
equal weighting without a config change."""

RRF_ARM_WEIGHTS_EQUAL = (1.0, 1.0)
"""RRF arm weights used with a real bi-encoder: standard RRF."""

BM25_K1 = 1.2
BM25_B = 0.75

EMBEDDING_DIM = 768

HNSW_EF_SEARCH = 100
"""Raise this when filtered vector search returns fewer rows than requested.

HNSW finds nearest neighbours *before* the SQL filter removes some of them, so a
narrow filter can starve the result set. The other fix is iterative index scans
(pgvector >= 0.8.0), which keep scanning until enough rows pass the filter.
"""

PGVECTOR_ITERATIVE_SCAN = "relaxed_order"
"""``off``, ``strict_order`` or ``relaxed_order``; needs pgvector >= 0.8.0."""

# --- Ranking ---------------------------------------------------------------

STAGE1_TOP_K = 100
"""Stage 1 cuts 1,000 candidates to the 100 the cross-encoder can afford."""

CROSS_ENCODER_MAX_TOKENS = 512
HARD_NEGATIVES_PER_GROUP = 15
"""One correct entry plus 15 hard negatives mined from retrieval results."""

MMR_LAMBDA = 0.7
"""Maximal Marginal Relevance trade-off; the spec's starting value."""

RELEVANCE_CUTOFF = 0.5
"""Calibrated-probability floor. Meaningful only because Stage 3 is calibrated."""

ACCEPTANCE_SMOOTHING_IMPRESSIONS = 50.0
"""Pseudo-count pulling a low-impression entry's acceptance rate toward the
global average, so three lucky accepts do not outrank a proven entry."""

CO_USAGE_NEIGHBOURS = 50
"""Nearest projects (by project embedding) used for the co-usage feature."""

# --- MVP languages ---------------------------------------------------------

MVP_LANGUAGES = ("javascript", "html", "css", "python", "cpp", "cuda")
"""Every metric is reported per language as well as overall: a strong average
hides the languages where training data is scarcest, such as CUDA."""


@dataclass(frozen=True)
class TrainingConfig:
    """Cross-encoder fine-tuning. Starting values from the spec; tune these."""

    learning_rate: float = 2e-5
    weight_decay: float = 0.01
    warmup_fraction: float = 0.10
    bf16: bool = True
    batch_groups: int = 8
    epochs: int = 3
    max_grad_norm: float = 1.0


@dataclass(frozen=True)
class LightGBMConfig:
    """Shared Stage 1 / Stage 3 ranker config: lambdarank tuned for NDCG@10."""

    objective: str = "lambdarank"
    metric: str = "ndcg"
    eval_at: tuple[int, ...] = (10,)
    learning_rate: float = 0.05
    num_leaves: int = 31
    min_data_in_leaf: int = 20
    feature_fraction: float = 0.9
    bagging_fraction: float = 0.8
    bagging_freq: int = 1
    num_boost_round: int = 500
    early_stopping_rounds: int = 50
    extra: dict[str, object] = field(default_factory=dict)

    # Input: none
    # Output: dict[str, object]
    # Processing: As params
    # Changed by: Claude Code
    # Date: 2026-10-12
    def as_params(self) -> dict[str, object]:
        params: dict[str, object] = {
            "objective": self.objective,
            "metric": self.metric,
            "ndcg_eval_at": list(self.eval_at),
            "learning_rate": self.learning_rate,
            "num_leaves": self.num_leaves,
            "min_data_in_leaf": self.min_data_in_leaf,
            "feature_fraction": self.feature_fraction,
            "bagging_fraction": self.bagging_fraction,
            "bagging_freq": self.bagging_freq,
            "verbosity": -1,
        }
        params.update(self.extra)
        return params
