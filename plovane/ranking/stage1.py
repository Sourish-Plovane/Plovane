"""Stage 1: the feature ranker. 1,000 candidates down to 100.

A LightGBM model with the ``lambdarank`` objective, optimised for NDCG@10.
Stage 1 is a filter, not a final order -- it only has to keep the right entry
inside the 100 the cross-encoder can afford to read -- so it is scored on
recall at 100 as much as on NDCG.

Before there is usage data there is nothing to train on, so
:class:`LinearFeatureRanker` scores the same features with hand-set weights.
It is a real ranker on day one and a baseline afterwards: if the trained model
cannot beat it on the gold set, the trained model does not ship.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

import numpy as np

from plovane.config import STAGE1_TOP_K, LightGBMConfig
from plovane.ranking.features import FEATURE_NAMES, FeatureContext, build_feature_matrix
from plovane.schemas import Candidate

HAND_WEIGHTS: dict[str, float] = {
    # Retrieval agreement is the strongest day-one signal; the fused rank
    # carries the most weight because it already reflects both arms.
    "bm25_score": 0.8,
    "cosine_score": 1.0,
    "rrf_rank": -1.5,
    # Usage signals are weak until the logs are deep, so they stay small --
    # they must not outvote relevance before they mean anything.
    "co_usage_lift": 0.5,
    "usage_log": 0.3,
    "acceptance_smoothed": 0.6,
    # Trust signals: a well-tested, recently retested entry is safer to insert.
    "mutation_score": 0.4,
    "days_since_retest": -0.2,
    # Friction and ownership.
    "dependency_compat": -0.7,
    "owner_match": 0.5,
}
"""Hand-set weights for the untrained ranker. Negative where a lower value is
better: ``rrf_rank`` 1 beats 500, ``dependency_compat`` 0 (compatible) beats 2
(conflicts), and a stale entry is worse than a freshly retested one."""


@dataclass
class TrainingGroup:
    """One query's candidates with relevance labels.

    LightGBM's lambdarank optimises *within* a group, so the group boundaries
    are what make it a ranker rather than a regressor. One group per query.
    """

    features: np.ndarray  # (n_candidates, n_features)
    labels: np.ndarray  # graded relevance, higher is better
    query_id: str | None = None

    # Input: none
    # Output: None
    # Processing: Post init
    # Changed by: Claude Code
    # Date: 2026-10-12
    def __post_init__(self) -> None:
        self.features = np.asarray(self.features, dtype=np.float32)
        self.labels = np.asarray(self.labels, dtype=np.float32)
        if self.features.ndim != 2:
            raise ValueError(f"features must be 2-D, got shape {self.features.shape}")
        if self.features.shape[0] != self.labels.shape[0]:
            raise ValueError(
                f"group {self.query_id or '?'}: {self.features.shape[0]} rows but {self.labels.shape[0]} labels"
            )


# Input: groups (Sequence[TrainingGroup])
# Output: tuple[np.ndarray, np.ndarray, list[int]]
# Processing: Flatten
# Changed by: Claude Code
# Date: 2026-10-12
def _flatten(groups: Sequence[TrainingGroup]) -> tuple[np.ndarray, np.ndarray, list[int]]:
    if not groups:
        raise ValueError("no training groups given")
    features = np.concatenate([g.features for g in groups], axis=0)
    labels = np.concatenate([g.labels for g in groups], axis=0)
    return features, labels, [int(g.features.shape[0]) for g in groups]


class FeatureRanker(Protocol):
    """Scores a feature matrix. Higher is better."""

    # Input: features (np.ndarray)
    # Output: np.ndarray
    # Processing: Score
    # Changed by: Claude Code
    # Date: 2026-10-12
    def score(self, features: np.ndarray) -> np.ndarray: ...


# Input: features (np.ndarray)
# Output: np.ndarray
# Processing: Min-max each column across this query's candidates.
# Changed by: Claude Code
# Date: 2026-10-12
def normalize_within_query(features: np.ndarray) -> np.ndarray:
    """Min-max each column across this query's candidates.

    A hand-weighted linear score needs comparable scales, and these features
    have none: BM25 is unbounded, cosine sits in [-1, 1], ``days_since_retest``
    runs to 365. Normalising *within the query* rather than against global
    statistics also means the weights do not drift as the library grows.

    A constant column maps to 0.5 -- no information either way, rather than an
    arbitrary 0 or 1.
    """
    features = np.asarray(features, dtype=np.float32)
    if features.size == 0:
        return features
    lo = features.min(axis=0)
    hi = features.max(axis=0)
    span = hi - lo
    flat = span <= 0
    out = np.where(flat, 0.5, (features - lo) / np.where(flat, 1.0, span))
    return out.astype(np.float32)


class LinearFeatureRanker:
    """Hand-weighted linear score over the normalised features."""

    # Input: weights (dict[str, float] | None)
    # Output: None
    # Processing: Init
    # Changed by: Claude Code
    # Date: 2026-10-12
    def __init__(self, weights: dict[str, float] | None = None) -> None:
        weights = weights or HAND_WEIGHTS
        unknown = set(weights) - set(FEATURE_NAMES)
        if unknown:
            raise ValueError(f"unknown feature names in weights: {sorted(unknown)}")
        self.weights = weights
        self._vector = np.asarray([weights.get(name, 0.0) for name in FEATURE_NAMES], dtype=np.float32)

    # Input: features (np.ndarray)
    # Output: np.ndarray
    # Processing: Score
    # Changed by: Claude Code
    # Date: 2026-10-12
    def score(self, features: np.ndarray) -> np.ndarray:
        features = np.asarray(features, dtype=np.float32)
        if features.size == 0:
            return np.zeros(features.shape[0], dtype=np.float32)
        if features.shape[1] != len(FEATURE_NAMES):
            raise ValueError(f"expected {len(FEATURE_NAMES)} features, got {features.shape[1]}")
        return normalize_within_query(features) @ self._vector


class LightGBMFeatureRanker:
    """LightGBM lambdarank over the same features. Requires lightgbm."""

    # Input: config (LightGBMConfig | None)
    # Output: None
    # Processing: Init
    # Changed by: Claude Code
    # Date: 2026-10-12
    def __init__(self, config: LightGBMConfig | None = None) -> None:
        self.config = config or LightGBMConfig()
        self.booster = None

    # Input: groups (Sequence[TrainingGroup]), validation (Sequence[TrainingGroup] | None)
    # Output: LightGBMFeatureRanker
    # Processing: Fit
    # Changed by: Claude Code
    # Date: 2026-10-12
    def fit(
        self,
        groups: Sequence[TrainingGroup],
        *,
        validation: Sequence[TrainingGroup] | None = None,
    ) -> LightGBMFeatureRanker:
        import lightgbm as lgb

        features, labels, group_sizes = _flatten(groups)
        train_set = lgb.Dataset(
            features, label=labels, group=group_sizes, feature_name=list(FEATURE_NAMES), free_raw_data=False
        )

        valid_sets = []
        callbacks = []
        if validation:
            v_features, v_labels, v_groups = _flatten(validation)
            valid_sets.append(
                lgb.Dataset(
                    v_features,
                    label=v_labels,
                    group=v_groups,
                    feature_name=list(FEATURE_NAMES),
                    reference=train_set,
                )
            )
            callbacks.append(lgb.early_stopping(self.config.early_stopping_rounds, verbose=False))

        self.booster = lgb.train(
            self.config.as_params(),
            train_set,
            num_boost_round=self.config.num_boost_round,
            valid_sets=valid_sets or None,
            callbacks=callbacks or None,
        )
        return self

    # Input: features (np.ndarray)
    # Output: np.ndarray
    # Processing: Score
    # Changed by: Claude Code
    # Date: 2026-10-12
    def score(self, features: np.ndarray) -> np.ndarray:
        if self.booster is None:
            raise RuntimeError("LightGBMFeatureRanker is not fitted; call fit() or load()")
        features = np.asarray(features, dtype=np.float32)
        if features.size == 0:
            return np.zeros(features.shape[0], dtype=np.float32)
        return np.asarray(self.booster.predict(features), dtype=np.float32)

    # Input: none
    # Output: dict[str, float]
    # Processing: Gain per feature. Worth reading before adding another one.
    # Changed by: Claude Code
    # Date: 2026-10-12
    def feature_importance(self) -> dict[str, float]:
        """Gain per feature. Worth reading before adding another one."""
        if self.booster is None:
            raise RuntimeError("LightGBMFeatureRanker is not fitted")
        gains = self.booster.feature_importance(importance_type="gain")
        return dict(zip(FEATURE_NAMES, (float(g) for g in gains), strict=True))

    # Input: path (str | Path)
    # Output: None
    # Processing: Save
    # Changed by: Claude Code
    # Date: 2026-10-12
    def save(self, path: str | Path) -> None:
        if self.booster is None:
            raise RuntimeError("LightGBMFeatureRanker is not fitted")
        self.booster.save_model(str(path))

    # Input: path (str | Path)
    # Output: LightGBMFeatureRanker
    # Processing: Load
    # Changed by: Claude Code
    # Date: 2026-10-12
    def load(self, path: str | Path) -> LightGBMFeatureRanker:
        import lightgbm as lgb

        self.booster = lgb.Booster(model_file=str(path))
        return self


# Input: candidates (Sequence[Candidate]), ctx (FeatureContext), ranker (FeatureRanker), top_k (int)
# Output: list[Candidate]
# Processing: Score every candidate, keep the best ``top_k``.
# Changed by: Claude Code
# Date: 2026-10-12
def rank_stage1(
    candidates: Sequence[Candidate],
    ctx: FeatureContext,
    ranker: FeatureRanker,
    *,
    top_k: int = STAGE1_TOP_K,
) -> list[Candidate]:
    """Score every candidate, keep the best ``top_k``.

    The score is written back onto each candidate because Stage 3 takes these
    same features plus the cross-encoder score -- the work is not repeated.
    """
    if not candidates:
        return []

    features = build_feature_matrix(candidates, ctx)
    scores = ranker.score(features)
    for candidate, score in zip(candidates, scores, strict=True):
        candidate.stage1_score = float(score)

    order = np.argsort(-scores, kind="stable")[:top_k]
    return [candidates[i] for i in order]
