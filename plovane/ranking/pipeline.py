"""The serving pipeline: request in, slim cards out.

Retrieval narrows the library to 1,000, Stage 1 cuts to 100, the cross-encoder
scores those precisely, Stage 3 combines everything and calibrates, the cutoff
decides what is good enough, and MMR removes the near-duplicates. What comes
back is a short ordered list of slim cards and nothing else.

Two rules from the spec are enforced here rather than left to callers. Every
recommendation logs the model version that produced it, so a regression can be
traced and rolled back -- that is :class:`plovane.schemas.RecommendationLog`,
returned alongside every response. And the version never reaches the agent.
"""

from __future__ import annotations

import secrets
from collections.abc import Sequence
from dataclasses import dataclass, field

import numpy as np

from plovane.config import MMR_LAMBDA, RELEVANCE_CUTOFF, STAGE1_TOP_K
from plovane.ranking.features import FeatureContext, UsageIndex
from plovane.ranking.mmr import maximal_marginal_relevance
from plovane.ranking.stage1 import FeatureRanker, rank_stage1
from plovane.ranking.stage3 import FinalRanker
from plovane.retrieval.filters import FilterSpec
from plovane.retrieval.service import RetrievalService
from plovane.schemas import Candidate, RecommendationLog, Request, Response, SlimCard

NO_MATCH_REASON = "no entry above the relevance cutoff"
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
    """A :class:`plovane.ranking.cross_encoder.CrossEncoder`, or None.

    Optional so the stack is runnable without torch -- and because Stage 2 is
    the one stage that can be dropped under load. Stage 1 and Stage 3 still
    produce a usable ordering; it is simply less precise, which the logged
    version records.
    """

    usage_index: UsageIndex = field(default_factory=UsageIndex)
    global_acceptance_rate: float = 0.0
    cutoff: float = RELEVANCE_CUTOFF
    mmr_lambda: float = MMR_LAMBDA
    stage1_top_k: int = STAGE1_TOP_K
    versions: ModelVersions = field(default_factory=ModelVersions)

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
        above_cutoff = [c for c in ordered if (c.calibrated_score or 0.0) >= self.cutoff]

        # MMR runs after the cutoff, not before: diversifying first would let a
        # distinct-but-irrelevant entry into the list, and the cutoff is the
        # promise that nothing weak is returned.
        selected = maximal_marginal_relevance(above_cutoff, k=request.k, lambda_=self.mmr_lambda)

        cards = tuple(SlimCard.from_candidate(c, c.calibrated_score or 0.0) for c in selected)
        response = Response(
            query_id=query_id,
            results=cards,
            reason=None if cards else NO_MATCH_REASON,
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
            from plovane.retrieval.query import build_query

            self.cross_encoder.score_candidates(build_query(request).cross_encoder_text, shortlist)
        return self.final_ranker.rank(shortlist, ctx)
