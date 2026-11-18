"""The template recommender: the spec's template arm of the ranking stack.

``cross_encoder`` is not imported here because it requires torch; import
:class:`ml.templates.cross_encoder.TemplateCrossEncoder` directly when
you need Stage 2.
"""

from ml.templates.features import (
    TEMPLATE_FEATURE_NAMES,
    TEMPLATE_STAGE3_FEATURE_NAMES,
    build_template_feature_matrix,
    build_template_stage3_matrix,
    framework_match,
    is_template_like,
    member_count,
    new_dependency_count,
)
from ml.templates.ranking import (
    TEMPLATE_HAND_WEIGHTS,
    TEMPLATE_STAGE3_HAND_WEIGHTS,
    LightGBMTemplateRanker,
    LinearRanker,
    default_stage1_ranker,
    default_stage3_ranker,
)
from ml.templates.recommender import (
    TEMPLATE_ENTRY_TYPES,
    TEMPLATE_MMR_LAMBDA,
    TemplateRecommender,
)

__all__ = [
    "TEMPLATE_ENTRY_TYPES",
    "TEMPLATE_FEATURE_NAMES",
    "TEMPLATE_HAND_WEIGHTS",
    "TEMPLATE_MMR_LAMBDA",
    "TEMPLATE_STAGE3_FEATURE_NAMES",
    "TEMPLATE_STAGE3_HAND_WEIGHTS",
    "LightGBMTemplateRanker",
    "LinearRanker",
    "TemplateRecommender",
    "build_template_feature_matrix",
    "build_template_stage3_matrix",
    "default_stage1_ranker",
    "default_stage3_ranker",
    "framework_match",
    "is_template_like",
    "member_count",
    "new_dependency_count",
]
