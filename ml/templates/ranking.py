"""Template rankers: Stage 1 and Stage 3 over the template feature set.

Same two-model shape as the shared stack -- a cheap ranker that shortlists, a
final ranker that folds in the cross-encoder score and is calibrated -- with
weights set for templates rather than helpers. Each weight below carries the
reason it differs; a weight without a reason is a number nobody can argue
with, which is how a hand-tuned ranker ossifies.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

import numpy as np

from ml.config import LightGBMConfig
from ml.ranking.stage1 import TrainingGroup, normalize_within_query
from ml.templates.features import TEMPLATE_FEATURE_NAMES, TEMPLATE_STAGE3_FEATURE_NAMES

TEMPLATE_HAND_WEIGHTS: dict[str, float] = {
    # Template queries describe an intent ("set up a service with auth"), not
    # an identifier, so meaning carries more than lexical overlap. The reverse
    # of the helper weighting, where a query often names the function.
    "bm25_score": 0.5,
    "cosine_score": 1.3,
    "rrf_rank": -1.5,
    # A framework mismatch does not make a template worse, it makes it
    # unusable, so this is the largest single weight in the row.
    "framework_match": 1.8,
    # Templates are more interchangeable than helpers, so popularity says more
    # about quality here than it does there.
    "co_usage_lift": 0.6,
    "usage_log": 0.4,
    "acceptance_smoothed": 0.6,
    # A template is edited after insertion, so its own test strength matters
    # less than a helper's -- but it rots faster, because the framework
    # versions it pins go stale.
    "mutation_score": 0.3,
    "days_since_retest": -0.3,
    # Near zero on purpose: a template is meant to bring its own dependencies.
    # See ml.templates.features for why penalising that inverts the rank.
    "dependency_compat": -0.05,
    "new_dependency_count": -0.15,
    # Unsigned: scope is query-dependent, so no hand weight can be right.
    "member_count": 0.0,
    "owner_match": 0.5,
}

TEMPLATE_STAGE3_HAND_WEIGHTS: dict[str, float] = {
    **TEMPLATE_HAND_WEIGHTS,
    # Until there is accept data the cross-encoder is the most trustworthy
    # signal available, so it outweighs everything else.
    "cross_encoder_score": 3.0,
}


class LinearRanker:
    """Hand-weighted linear score over any named feature set.

    Generic over the feature names so Stage 1 and Stage 3 share one
    implementation; the features are min-max normalised within the query
    before weighting, because they have no common scale.
    """

    # Input: feature_names (Sequence[str]), weights (dict[str, float])
    # Output: None
    # Processing: Init
    # Changed by: Sourish
    # Date: 2026-10-12
    def __init__(self, feature_names: Sequence[str], weights: dict[str, float]) -> None:
        unknown = set(weights) - set(feature_names)
        if unknown:
            raise ValueError(f"unknown feature names in weights: {sorted(unknown)}")
        self.feature_names = tuple(feature_names)
        self.weights = weights
        self._vector = np.asarray([weights.get(name, 0.0) for name in feature_names], dtype=np.float32)

    # Input: features (np.ndarray)
    # Output: np.ndarray
    # Processing: Score
    # Changed by: Sourish
    # Date: 2026-10-12
    def score(self, features: np.ndarray) -> np.ndarray:
        features = np.asarray(features, dtype=np.float32)
        if features.size == 0:
            return np.zeros(features.shape[0], dtype=np.float32)
        if features.shape[1] != len(self.feature_names):
            raise ValueError(f"expected {len(self.feature_names)} features, got {features.shape[1]}")
        return normalize_within_query(features) @ self._vector


class LightGBMTemplateRanker:
    """LightGBM lambdarank over a named feature set. Requires lightgbm."""

    # Input: feature_names (Sequence[str]), config (LightGBMConfig | None)
    # Output: None
    # Processing: Init
    # Changed by: Sourish
    # Date: 2026-10-12
    def __init__(self, feature_names: Sequence[str], config: LightGBMConfig | None = None) -> None:
        self.feature_names = tuple(feature_names)
        self.config = config or LightGBMConfig()
        self.booster = None

    # Input: groups (Sequence[TrainingGroup]), validation (Sequence[TrainingGroup] | None)
    # Output: LightGBMTemplateRanker
    # Processing: Fit
    # Changed by: Sourish
    # Date: 2026-10-12
    def fit(
        self, groups: Sequence[TrainingGroup], *, validation: Sequence[TrainingGroup] | None = None
    ) -> LightGBMTemplateRanker:
        import lightgbm as lgb

        if not groups:
            raise ValueError("no training groups given")
        for group in groups:
            if group.features.shape[1] != len(self.feature_names):
                raise ValueError(
                    f"group {group.query_id or '?'} has {group.features.shape[1]} features, "
                    f"expected {len(self.feature_names)}"
                )

        features = np.concatenate([g.features for g in groups], axis=0)
        labels = np.concatenate([g.labels for g in groups], axis=0)
        sizes = [int(g.features.shape[0]) for g in groups]
        train_set = lgb.Dataset(
            features, label=labels, group=sizes, feature_name=list(self.feature_names), free_raw_data=False
        )

        valid_sets, callbacks = [], []
        if validation:
            v_features = np.concatenate([g.features for g in validation], axis=0)
            v_labels = np.concatenate([g.labels for g in validation], axis=0)
            v_sizes = [int(g.features.shape[0]) for g in validation]
            valid_sets.append(
                lgb.Dataset(
                    v_features,
                    label=v_labels,
                    group=v_sizes,
                    feature_name=list(self.feature_names),
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
    # Changed by: Sourish
    # Date: 2026-10-12
    def score(self, features: np.ndarray) -> np.ndarray:
        if self.booster is None:
            raise RuntimeError("LightGBMTemplateRanker is not fitted; call fit() or load()")
        features = np.asarray(features, dtype=np.float32)
        if features.size == 0:
            return np.zeros(features.shape[0], dtype=np.float32)
        return np.asarray(self.booster.predict(features), dtype=np.float32)

    # Input: none
    # Output: dict[str, float]
    # Processing: Feature importance
    # Changed by: Sourish
    # Date: 2026-10-12
    def feature_importance(self) -> dict[str, float]:
        if self.booster is None:
            raise RuntimeError("LightGBMTemplateRanker is not fitted")
        gains = self.booster.feature_importance(importance_type="gain")
        return dict(zip(self.feature_names, (float(g) for g in gains), strict=True))

    # Input: path (str | Path)
    # Output: None
    # Processing: Save
    # Changed by: Sourish
    # Date: 2026-10-12
    def save(self, path: str | Path) -> None:
        if self.booster is None:
            raise RuntimeError("LightGBMTemplateRanker is not fitted")
        self.booster.save_model(str(path))

    # Input: path (str | Path)
    # Output: LightGBMTemplateRanker
    # Processing: Load
    # Changed by: Sourish
    # Date: 2026-10-12
    def load(self, path: str | Path) -> LightGBMTemplateRanker:
        import lightgbm as lgb

        self.booster = lgb.Booster(model_file=str(path))
        return self


# Input: none
# Output: LinearRanker
# Processing: The untrained Stage 1 template ranker. Works on day one.
# Changed by: Sourish
# Date: 2026-10-12
def default_stage1_ranker() -> LinearRanker:
    """The untrained Stage 1 template ranker. Works on day one."""
    return LinearRanker(TEMPLATE_FEATURE_NAMES, TEMPLATE_HAND_WEIGHTS)


# Input: none
# Output: LinearRanker
# Processing: The untrained Stage 3 template ranker.
# Changed by: Sourish
# Date: 2026-10-12
def default_stage3_ranker() -> LinearRanker:
    """The untrained Stage 3 template ranker."""
    return LinearRanker(TEMPLATE_STAGE3_FEATURE_NAMES, TEMPLATE_STAGE3_HAND_WEIGHTS)
