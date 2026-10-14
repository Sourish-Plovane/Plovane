"""The serving pipeline: request in, slim cards out.

Retrieval narrows the library to 1,000, Stage 1 cuts to 100, the cross-encoder
scores those precisely, Stage 3 combines everything and calibrates, the cutoff
decides what is good enough, and MMR removes the near-duplicates. What comes
back is a short ordered list of slim cards and nothing else.

Two rules from the spec are enforced here rather than left to callers. Every
recommendation logs the model version that produced it, so a regression can be
traced and rolled back -- that is :class:`ml.schemas.RecommendationLog`,
returned alongside every response. And the version never reaches the agent.
"""

from __future__ import annotations

import secrets
from collections.abc import Sequence
from dataclasses import dataclass, field

import numpy as np

from ml.config import (
    MMR_LAMBDA,
    MMR_LAMBDA_PLACEHOLDER_EMBEDDING,
    MMR_LAMBDA_SEMANTIC_EMBEDDING,
    RELEVANCE_CUTOFF,
    STAGE1_TOP_K,
)
from ml.ranking.features import FeatureContext, UsageIndex
from ml.ranking.mmr import maximal_marginal_relevance
from ml.ranking.stage1 import FeatureRanker, rank_stage1
from ml.ranking.stage3 import FinalRanker
from ml.retrieval.filters import FilterSpec
from ml.retrieval.service import RetrievalService
from ml.schemas import Candidate, RecommendationLog, Request, Response, SlimCard

NO_MATCH_REASON = "no entry above the relevance cutoff"

UNCALIBRATED_REASON = (
    "scores are uncalibrated: no relevance cutoff was applied, so these are the "
    "best available matches rather than matches known to be good"
)
"""Said out loud when the cutoff could not be enforced.

Without a fitted calibrator a score is an ordering, not a probability, and a
fixed cutoff on it rejects whatever it happens to reject. Applying 0.5 to the
uncalibrated sigmoid on this library passed every one of 100 candidates and
returned three confident results for a query about warp cores. Serving the
list and saying it is unfiltered is honest; serving it while reporting a
cutoff that never fired is not."""
"""Returned instead of a weak match, so the agent writes new code rather than
pasting something it will have to undo."""


# Input: none
# Output: str
# Processing: A unique id tying a response to the agent's later accept or reject.
# Changed by: Claude Code
# Date: 2026-10-12
def new_query_id() -> str:
    """A unique id tying a response to the agent's later accept or reject.

    Deliberately wider than the spec's illustrative ``q_7f3a``: four hex
    characters is 65,536 values, and at the spec's projected volume -- roughly
    50 queries per user per day -- ids would start colliding within days.
    A collision does not just confuse a log line; it misattributes an accept
    to the wrong recommendation and poisons the rankers' training data.
    """
    return f"q_{secrets.token_hex(8)}"


@dataclass(frozen=True)
class ModelVersions:
    """Which model produced a recommendation. Logged, never sent to the agent."""

    retrieval_embedding: str = "unversioned"
    stage1: str = "unversioned"
    cross_encoder: str = "none"
    stage3: str = "unversioned"
    calibrator: str = "none"

    # Input: none
    # Output: dict[str, str]
    # Processing: As dict
    # Changed by: Claude Code
    # Date: 2026-10-12
    def as_dict(self) -> dict[str, str]:
        return {
            "retrieval_embedding": self.retrieval_embedding,
            "stage1": self.stage1,
            "cross_encoder": self.cross_encoder,
            "stage3": self.stage3,
            "calibrator": self.calibrator,
        }


@dataclass
class RecommendationPipeline:
    retrieval: RetrievalService
    stage1_ranker: FeatureRanker
    final_ranker: FinalRanker
    cross_encoder: object | None = None
    """A :class:`ml.ranking.cross_encoder.CrossEncoder`, or None.

    Optional so the stack is runnable without torch -- and because Stage 2 is
    the one stage that can be dropped under load. Stage 1 and Stage 3 still
    produce a usable ordering; it is simply less precise, which the logged
    version records.
    """

    usage_index: UsageIndex = field(default_factory=UsageIndex)
    global_acceptance_rate: float = 0.0
    cutoff: float = RELEVANCE_CUTOFF
    mmr_lambda: float | None = None
    """MMR relevance/diversity trade-off.

    None means auto-pick by embedding model: 0.7 when the embedding model is
    the hashing placeholder, 1.0 when it is a real encoder. Measured on 350
    queries after swapping UniXcoder in: 0.7 scored @3 39.7% / @10 49.1%, 1.0
    scored @3 58.3% / @10 78.9%. MMR's job is to clear near-duplicates and a
    real encoder does not produce them in the top 10."""
    stage1_top_k: int = STAGE1_TOP_K
    versions: ModelVersions = field(default_factory=ModelVersions)

    # Input: none
    # Output: None
    # Processing: Resolve mmr_lambda from the embedding model when None
    # Changed by: Claude Code
    # Date: 2026-10-14
    def __post_init__(self) -> None:
        from ml.retrieval.embedding import HashingEmbeddingModel
        from ml.ranking.stage3 import (
            LinearFinalRanker,
            STAGE3_HAND_WEIGHTS_PLACEHOLDER_EMBEDDING,
            STAGE3_HAND_WEIGHTS_SEMANTIC_EMBEDDING,
        )

        placeholder = isinstance(self.retrieval.embedding_model, HashingEmbeddingModel)

        if self.mmr_lambda is None:
            object.__setattr__(
                self,
                "mmr_lambda",
                MMR_LAMBDA_PLACEHOLDER_EMBEDDING if placeholder else MMR_LAMBDA_SEMANTIC_EMBEDDING,
            )

        # Swap Stage 3's hand weights to the semantic-mode variant when the
        # encoder is strong. Only touches a LinearFinalRanker that is still
        # using the placeholder-mode weights; a caller who supplied their own
        # weights or a trained booster is left alone.
        if not placeholder:
            model = self.final_ranker.model
            if (
                isinstance(model, LinearFinalRanker)
                and model.weights is STAGE3_HAND_WEIGHTS_PLACEHOLDER_EMBEDDING
            ):
                self.final_ranker.model = LinearFinalRanker(
                    weights=STAGE3_HAND_WEIGHTS_SEMANTIC_EMBEDDING
                )

    # Input: request (Request), project_embedding (np.ndarray | None)
    # Output: FeatureContext
    # Processing: Feature context
    # Changed by: Claude Code
    # Date: 2026-10-12
    def _feature_context(self, request: Request, project_embedding: np.ndarray | None) -> FeatureContext:
        return FeatureContext.build(
            usage_index=self.usage_index,
            project_embedding=project_embedding,
            global_acceptance_rate=self.global_acceptance_rate,
            installed_dependencies=request.project_context.dependencies,
            requester_id=request.requester_id,
        )

    # Input: request (Request), filters (FilterSpec | None), project_embedding (np.ndarray | None)
    # Output: tuple[Response, RecommendationLog]
    # Processing: Recommend
    # Changed by: Claude Code
    # Date: 2026-10-12
    def recommend(
        self,
        request: Request,
        *,
        filters: FilterSpec | None = None,
        project_embedding: np.ndarray | None = None,
    ) -> tuple[Response, RecommendationLog]:
        query_id = new_query_id()

        retrieved = self.retrieval.retrieve(request, filters=filters)
        ctx = self._feature_context(request, project_embedding)

        shortlist = rank_stage1(retrieved.candidates, ctx, self.stage1_ranker, top_k=self.stage1_top_k)

        if self.cross_encoder is not None and shortlist:
            self.cross_encoder.score_candidates(retrieved.query.cross_encoder_text, shortlist)

        ordered = self.final_ranker.rank(shortlist, ctx)

        # The cutoff is only meaningful once the scores are calibrated. Before
        # then it is applied to a sigmoid whose range never reaches it, so it
        # filters nothing while appearing to -- the worst of both. Skip it and
        # say so instead.
        cutoff_enforced = self.final_ranker.is_calibrated
        if cutoff_enforced:
            above_cutoff = [c for c in ordered if (c.calibrated_score or 0.0) >= self.cutoff]
        else:
            above_cutoff = list(ordered)

        # MMR runs after the cutoff, not before: diversifying first would let a
        # distinct-but-irrelevant entry into the list, and the cutoff is the
        # promise that nothing weak is returned.
        selected = maximal_marginal_relevance(above_cutoff, k=request.k, lambda_=self.mmr_lambda)

        cards = tuple(
            SlimCard.from_candidate(c, c.calibrated_score or 0.0, score_is_calibrated=cutoff_enforced)
            for c in selected
        )
        if not cards:
            reason = NO_MATCH_REASON if cutoff_enforced else UNCALIBRATED_REASON
        else:
            reason = None if cutoff_enforced else UNCALIBRATED_REASON
        response = Response(
            query_id=query_id,
            results=cards,
            reason=reason,
            cutoff_enforced=cutoff_enforced,
        )
        log = RecommendationLog(
            query_id=query_id,
            model_versions=self.versions.as_dict(),
            query=request.query,
            project_context=request.project_context,
            candidates_retrieved=len(retrieved.candidates),
            returned=tuple(card.id for card in cards),
            scores=tuple(card.score for card in cards),
            cutoff=self.cutoff,
            cutoff_enforced=cutoff_enforced,
        )
        return response, log

    # Input: request (Request), candidates (Sequence[Candidate]), project_embedding (np.ndarray | None)
    # Output: list[Candidate]
    # Processing: Run the ranking stages over a fixed candidate set.
    # Changed by: Claude Code
    # Date: 2026-10-12
    def rank_only(
        self,
        request: Request,
        candidates: Sequence[Candidate],
        *,
        project_embedding: np.ndarray | None = None,
    ) -> list[Candidate]:
        """Run the ranking stages over a fixed candidate set.

        For evaluation: it measures ranking against a frozen retrieval result,
        so a ranking change cannot be credited for a retrieval improvement or
        blamed for a retrieval regression.
        """
        ctx = self._feature_context(request, project_embedding)
        shortlist = rank_stage1(list(candidates), ctx, self.stage1_ranker, top_k=self.stage1_top_k)
        if self.cross_encoder is not None and shortlist:
            from ml.retrieval.query import build_query

            self.cross_encoder.score_candidates(build_query(request).cross_encoder_text, shortlist)
        return self.final_ranker.rank(shortlist, ctx)
