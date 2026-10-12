"""Plovane's recommendation models.

The stack, end to end:

1. **Retrieval** narrows the library to at most 1,000 candidates with keyword
   and vector search fused by RRF. Measured by recall@1000 -- and nothing
   downstream can repair a miss here.
2. **Stage 1** scores those 1,000 on cheap features and keeps 100.
3. **Stage 2**, the cross-encoder, reads query and candidate together and
   scores the 100 precisely.
4. **Stage 3** combines the features with the cross-encoder score and
   calibrates the result, so the relevance cutoff means something.
5. **MMR** drops the near-duplicates, and what the agent receives is a short
   ordered list of slim cards.

Two rules keep it accurate, and both are enforced in code rather than by
convention: every model change must beat the current version on the frozen
held-out gold set before release (:mod:`ml.evaluation.gates`), and every
recommendation logs the model version that produced it
(:class:`ml.schemas.RecommendationLog`).
"""

from ml.config import MVP_LANGUAGES, LightGBMConfig, TrainingConfig
from ml.schemas import (
    Candidate,
    DependencyCompat,
    Entry,
    EntryType,
    ProjectContext,
    RecommendationLog,
    Request,
    Response,
    SlimCard,
    Visibility,
)

__version__ = "0.1.0"

__all__ = [
    "MVP_LANGUAGES",
    "Candidate",
    "DependencyCompat",
    "Entry",
    "EntryType",
    "LightGBMConfig",
    "ProjectContext",
    "RecommendationLog",
    "Request",
    "Response",
    "SlimCard",
    "TrainingConfig",
    "Visibility",
    "__version__",
]
