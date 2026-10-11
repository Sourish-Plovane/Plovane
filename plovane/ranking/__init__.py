"""The three-stage ranking stack, calibration and diversification.

``cross_encoder`` is not imported here because it requires torch; import it
directly when you need Stage 2.
"""

from plovane.ranking.calibration import IsotonicCalibrator, pool_adjacent_violators
from plovane.ranking.features import (
    FEATURE_NAMES,
    FeatureContext,
    UsageIndex,
    build_feature_matrix,
    co_usage_lift,
    smoothed_acceptance,
)
from plovane.ranking.mmr import maximal_marginal_relevance
from plovane.ranking.pipeline import (
    NO_MATCH_REASON,
    ModelVersions,
    RecommendationPipeline,
    new_query_id,
)
from plovane.ranking.stage1 import (
    HAND_WEIGHTS,
    LightGBMFeatureRanker,
    LinearFeatureRanker,
    TrainingGroup,
    rank_stage1,
)
from plovane.ranking.stage3 import (
    STAGE3_FEATURE_NAMES,
    FinalRanker,
    LightGBMFinalRanker,
    LinearFinalRanker,
    build_stage3_matrix,
)

__all__ = [
    "FEATURE_NAMES",
    "HAND_WEIGHTS",
    "NO_MATCH_REASON",
    "STAGE3_FEATURE_NAMES",
    "FeatureContext",
    "FinalRanker",
    "IsotonicCalibrator",
    "LightGBMFeatureRanker",
    "LightGBMFinalRanker",
    "LinearFeatureRanker",
    "LinearFinalRanker",
    "ModelVersions",
    "RecommendationPipeline",
    "TrainingGroup",
    "UsageIndex",
    "build_feature_matrix",
    "build_stage3_matrix",
    "co_usage_lift",
    "maximal_marginal_relevance",
    "new_query_id",
    "pool_adjacent_violators",
    "rank_stage1",
    "smoothed_acceptance",
]
