"""Stage 3: the final ranker, and the calibration that makes it decidable.

A second LightGBM lambdarank model takes the Stage 1 features *plus* the
cross-encoder score, then its output is calibrated with isotonic regression on
a held-out set.

Why a second tree model rather than just trusting Stage 2: the cross-encoder
reads text and nothing else. It cannot know that an entry conflicts with the
project's pinned torch version, that it has not been retested in eight months,
or that projects like this one reach for it twice as often as the library at
large. Stage 3 is where the text score meets those facts.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from ml.config import LightGBMConfig
from ml.ranking.calibration import IsotonicCalibrator
from ml.ranking.features import FEATURE_NAMES, FeatureContext, build_feature_matrix
from ml.ranking.stage1 import HAND_WEIGHTS, TrainingGroup, normalize_within_query
from ml.schemas import Candidate

STAGE3_FEATURE_NAMES: tuple[str, ...] = (*FEATURE_NAMES, "cross_encoder_score")
"""Stage 1's features with the cross-encoder score appended last."""

STAGE3_HAND_WEIGHTS_PLACEHOLDER_EMBEDDING: dict[str, float] = {
    **HAND_WEIGHTS,
    # When the vector arm is the hashing placeholder, the cross-encoder is
    # still the most trustworthy signal; it outweighs everything else before
    # there is training data to re-order on top.
    "cross_encoder_score": 3.0,
}
"""Stage 3 hand weights for the placeholder-embedding configuration."""

STAGE3_HAND_WEIGHTS_SEMANTIC_EMBEDDING: dict[str, float] = {
    name: 0.0 for name in STAGE3_FEATURE_NAMES
} | {"rrf_rank": -3.0}
"""Stage 3 hand weights for a real semantic encoder: trust retrieval order.

Measured on 350 queries with UniXcoder swapped in: the shipping weights
(cosine 1.0, bm25 0.8, rrf_rank -1.5, cross_encoder_score 3.0) scored
@1 37.4% / @3 58.3%, versus retrieval's ceiling of @1 47.1% / @3 66.6%
under the same language filter. Every hand-tuned feature mix I measured
underperformed pure retrieval order; cosine 3.0 only scored 43.7%, pure
retrieval order 47.1%. The explanation is simple: retrieval already
fused BM25 with the strong encoder, so reweighting the fused ranking by
individual signals is noise on top of a good decision.

The weights go back to a feature mix once there is a cross-encoder score
to re-order on (``cross_encoder_score`` carries the strongest signal and
is zero today), or when Stage 3 is trained on real traffic."""

STAGE3_HAND_WEIGHTS_WITH_CROSS_ENCODER: dict[str, float] = {
    name: 0.0 for name in STAGE3_FEATURE_NAMES
} | {"cross_encoder_score": 1.0, "rrf_rank": -0.01}
"""Stage 3 hand weights once the trained cross-encoder scores the shortlist.

Order by the cross-encoder; retrieval order only breaks ties. Measured on
350 held-out queries (5 folds by entry, GPU run 20261017T142721Z, issue 35)
by replaying this ranker over the saved per-query scores: @1 69.7% /
@3 88.3% / @10 96.0%, against retrieval order's 52.0% / 69.7% / 81.4%.
Mixing retrieval back in only cost: rrf_rank -3.0 with the cross-encoder
at 3.0 scored @1 60.0%, at 30.0 still 68.0%, and nested CV over RRF-style
fusions picked weights indistinguishable from the cross-encoder alone.
Retrieval already chose the 100 candidates; inside them the cross-encoder
is the better judge. Not certified -- the queries are assistant-authored
(issue 19)."""

STAGE3_HAND_WEIGHTS = STAGE3_HAND_WEIGHTS_PLACEHOLDER_EMBEDDING
"""Default. Overridden by the pipeline when it sees a real embedding model."""


# Input: candidates (Sequence[Candidate]), ctx (FeatureContext)
# Output: np.ndarray
# Processing: Stage 1 features plus the cross-encoder score, one row per candidate.
# Changed by: Claude Code
# Date: 2026-10-12
def build_stage3_matrix(candidates: Sequence[Candidate], ctx: FeatureContext) -> np.ndarray:
    """Stage 1 features plus the cross-encoder score, one row per candidate.

    Reuses ``candidate.stage1_score``'s features rather than recomputing them,
    and treats a missing cross-encoder score as 0 -- which happens only if a
    candidate skipped Stage 2, and should be visible as a weak row rather than
    crash the request.
    """
    base = build_feature_matrix(candidates, ctx)
    if base.size == 0:
        return np.zeros((0, len(STAGE3_FEATURE_NAMES)), dtype=np.float32)
    ce = np.asarray(
        [[c.cross_encoder_score if c.cross_encoder_score is not None else 0.0] for c in candidates],
        dtype=np.float32,
    )
    return np.hstack([base, ce])


class LinearFinalRanker:
    """Hand-weighted fallback, used until there is accept data to train on."""

    # Input: weights (dict[str, float] | None)
    # Output: None
    # Processing: Init
    # Changed by: Claude Code
    # Date: 2026-10-12
    def __init__(self, weights: dict[str, float] | None = None) -> None:
        weights = weights or STAGE3_HAND_WEIGHTS
        unknown = set(weights) - set(STAGE3_FEATURE_NAMES)
        if unknown:
            raise ValueError(f"unknown feature names in weights: {sorted(unknown)}")
        self._vector = np.asarray(
            [weights.get(name, 0.0) for name in STAGE3_FEATURE_NAMES], dtype=np.float32
        )
        self.weights = weights

    # Input: features (np.ndarray)
    # Output: np.ndarray
    # Processing: Score
    # Changed by: Claude Code
    # Date: 2026-10-12
    def score(self, features: np.ndarray) -> np.ndarray:
        features = np.asarray(features, dtype=np.float32)
        if features.size == 0:
            return np.zeros(features.shape[0], dtype=np.float32)
        if features.shape[1] != len(STAGE3_FEATURE_NAMES):
            raise ValueError(f"expected {len(STAGE3_FEATURE_NAMES)} features, got {features.shape[1]}")
        return normalize_within_query(features) @ self._vector


class LightGBMFinalRanker:
    """LightGBM lambdarank over the Stage 3 feature set. Requires lightgbm."""

    # Input: config (LightGBMConfig | None)
    # Output: None
    # Processing: Init
    # Changed by: Claude Code
    # Date: 2026-10-12
    def __init__(self, config: LightGBMConfig | None = None) -> None:
        self.config = config or LightGBMConfig()
        self.booster = None

    # Input: groups (Sequence[TrainingGroup]), validation (Sequence[TrainingGroup] | None)
    # Output: LightGBMFinalRanker
    # Processing: Fit
    # Changed by: Claude Code
    # Date: 2026-10-12
    def fit(
        self,
        groups: Sequence[TrainingGroup],
        *,
        validation: Sequence[TrainingGroup] | None = None,
    ) -> LightGBMFinalRanker:
        import lightgbm as lgb

        if not groups:
            raise ValueError("no training groups given")

        features = np.concatenate([g.features for g in groups], axis=0)
        labels = np.concatenate([g.labels for g in groups], axis=0)
        sizes = [int(g.features.shape[0]) for g in groups]
        train_set = lgb.Dataset(
            features,
            label=labels,
            group=sizes,
            feature_name=list(STAGE3_FEATURE_NAMES),
            free_raw_data=False,
        )

        valid_sets = []
        callbacks = []
        if validation:
            v_features = np.concatenate([g.features for g in validation], axis=0)
            v_labels = np.concatenate([g.labels for g in validation], axis=0)
            v_sizes = [int(g.features.shape[0]) for g in validation]
            valid_sets.append(
                lgb.Dataset(
                    v_features,
                    label=v_labels,
                    group=v_sizes,
                    feature_name=list(STAGE3_FEATURE_NAMES),
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
            raise RuntimeError("LightGBMFinalRanker is not fitted; call fit() or load()")
        features = np.asarray(features, dtype=np.float32)
        if features.size == 0:
            return np.zeros(features.shape[0], dtype=np.float32)
        return np.asarray(self.booster.predict(features), dtype=np.float32)

    # Input: none
    # Output: dict[str, float]
    # Processing: Feature importance
    # Changed by: Claude Code
    # Date: 2026-10-12
    def feature_importance(self) -> dict[str, float]:
        if self.booster is None:
            raise RuntimeError("LightGBMFinalRanker is not fitted")
        gains = self.booster.feature_importance(importance_type="gain")
        return dict(zip(STAGE3_FEATURE_NAMES, (float(g) for g in gains), strict=True))

    # Input: path (str | Path)
    # Output: None
    # Processing: Save
    # Changed by: Claude Code
    # Date: 2026-10-12
    def save(self, path: str | Path) -> None:
        if self.booster is None:
            raise RuntimeError("LightGBMFinalRanker is not fitted")
        self.booster.save_model(str(path))

    # Input: path (str | Path)
    # Output: LightGBMFinalRanker
    # Processing: Load
    # Changed by: Claude Code
    # Date: 2026-10-12
    def load(self, path: str | Path) -> LightGBMFinalRanker:
        import lightgbm as lgb

        self.booster = lgb.Booster(model_file=str(path))
        return self


@dataclass
class FinalRanker:
    """A scorer paired with its calibrator.

    The two travel together because a score is only usable with the calibrator
    that was fitted on it -- swapping the model and keeping the old calibrator
    would leave every cutoff decision quietly wrong.
    """

    model: LinearFinalRanker | LightGBMFinalRanker
    calibrator: IsotonicCalibrator | None = None
    version: str = "stage3-untrained"

    # Input: candidates (Sequence[Candidate]), ctx (FeatureContext)
    # Output: list[Candidate]
    # Processing: Score, calibrate and sort, writing both scores onto the candidates.
    # Changed by: Claude Code
    # Date: 2026-10-12
    def rank(self, candidates: Sequence[Candidate], ctx: FeatureContext) -> list[Candidate]:
        """Score, calibrate and sort, writing both scores onto the candidates."""
        if not candidates:
            return []

        features = build_stage3_matrix(candidates, ctx)
        raw = self.model.score(features)

        if self.calibrator is not None and self.calibrator.fitted:
            calibrated = self.calibrator.predict(raw)
        else:
            # No calibrator yet: fall back to a monotone squash so downstream
            # code always sees a 0-1 number, and flag it as uncalibrated so no
            # one reads it as a probability.
            calibrated = 1.0 / (1.0 + np.exp(-np.asarray(raw, dtype=np.float64)))

        for candidate, raw_score, cal_score in zip(candidates, raw, calibrated, strict=True):
            candidate.final_score = float(raw_score)
            candidate.calibrated_score = float(cal_score)

        return sorted(candidates, key=lambda c: (-(c.calibrated_score or 0.0), c.id))

    # Input: none
    # Output: bool
    # Processing: Is calibrated
    # Changed by: Claude Code
    # Date: 2026-10-12
    @property
    def is_calibrated(self) -> bool:
        return self.calibrator is not None and self.calibrator.fitted
