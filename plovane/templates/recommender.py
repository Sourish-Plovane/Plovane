"""The template recommender, end to end.

Request in, slim cards out, over the same five steps as the shared stack:
retrieval narrows the library to 1,000 templates, Stage 1 cuts to 100, the
template cross-encoder scores those precisely, Stage 3 folds the features back
in and calibrates, and MMR removes the near-duplicates.

This orchestrates the pieces directly rather than reusing
:class:`plovane.ranking.pipeline.RecommendationPipeline`, because the feature
sets genuinely differ -- 13 and 14 columns here against 10 and 11 there -- and
a pipeline that switched feature builders on a flag would be harder to follow
than the forty lines below. If a third entry type ever needs its own stack,
that is the point to generalise instead.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field

import numpy as np

from plovane.config import RELEVANCE_CUTOFF, STAGE1_TOP_K
from plovane.ranking.calibration import IsotonicCalibrator
from plovane.ranking.features import FeatureContext, UsageIndex
from plovane.ranking.mmr import maximal_marginal_relevance
from plovane.ranking.pipeline import NO_MATCH_REASON, ModelVersions, new_query_id
from plovane.retrieval.filters import FilterSpec
from plovane.retrieval.service import RetrievalService
from plovane.schemas import Candidate, RecommendationLog, Request, Response, SlimCard
from plovane.templates.features import (
    build_template_feature_matrix,
    build_template_stage3_matrix,
    is_template_like,
)
from plovane.templates.ranking import default_stage1_ranker, default_stage3_ranker

TEMPLATE_MMR_LAMBDA = 0.6
"""More diversity than the shared stack's 0.7.

Near-duplicates are the normal case among templates in a way they are not
among helpers: ten React starters differ in lint config and little else, and a
list of three that are all the same starter wastes the agent's only three
slots. Lowering lambda costs a little relevance to buy a list with genuinely
different options in it. A starting value to tune, like the spec's own.
"""

TEMPLATE_ENTRY_TYPES: tuple[str, ...] = ("template", "bundle")


@dataclass
class TemplateRecommender:
    """Recommends templates for an agent's request."""

    retrieval: RetrievalService
    stage1_ranker: object = field(default_factory=default_stage1_ranker)
    stage3_ranker: object = field(default_factory=default_stage3_ranker)
    cross_encoder: object | None = None
    """A :class:`plovane.templates.cross_encoder.TemplateCrossEncoder`, or None.

    Optional so the recommender runs without torch, and because Stage 2 is the
    stage that can be shed under load: Stage 1 and Stage 3 still order the
    list, just less precisely. The logged version records which path ran.
    """

    calibrator: IsotonicCalibrator | None = None
    usage_index: UsageIndex = field(default_factory=UsageIndex)
    global_acceptance_rate: float = 0.0
    cutoff: float = RELEVANCE_CUTOFF
    mmr_lambda: float = TEMPLATE_MMR_LAMBDA
    stage1_top_k: int = STAGE1_TOP_K
    include_bundles: bool = True
    versions: ModelVersions = field(default_factory=ModelVersions)

    # Input: none
    # Output: bool
    # Processing: Is calibrated
    # Changed by: Claude Code
    # Date: 2026-10-12
    @property
    def is_calibrated(self) -> bool:
        return self.calibrator is not None and self.calibrator.fitted

    # Input: none
    # Output: tuple[str, ...]
    # Processing: Entry types
    # Changed by: Claude Code
    # Date: 2026-10-12
    def _entry_types(self) -> tuple[str, ...]:
        return TEMPLATE_ENTRY_TYPES if self.include_bundles else ("template",)

    # Input: request (Request)
    # Output: FilterSpec
    # Processing: The request's filters, restricted to template-like entries.
    # Changed by: Claude Code
    # Date: 2026-10-12
    def filters_for(self, request: Request) -> FilterSpec:
        """The request's filters, restricted to template-like entries.

        ``drop_in_only`` is dropped deliberately. For a helper it means "no
        installs"; applied to templates it would filter out every scaffold
        that brings its own framework, which is all of the useful ones.
        """
        base = FilterSpec.from_request(request)
        return FilterSpec(
            requester_id=base.requester_id,
            languages=base.languages,
            framework=base.framework,
            category=base.category,
            allowed_licenses=base.allowed_licenses,
            installed_dependencies=base.installed_dependencies,
            drop_in_only=False,
            entry_types=self._entry_types(),
        )

    # Input: request (Request), project_embedding (np.ndarray | None)
    # Output: FeatureContext
    # Processing: Context
    # Changed by: Claude Code
    # Date: 2026-10-12
    def _context(self, request: Request, project_embedding: np.ndarray | None) -> FeatureContext:
        return FeatureContext.build(
            usage_index=self.usage_index,
            project_embedding=project_embedding,
            global_acceptance_rate=self.global_acceptance_rate,
            installed_dependencies=request.project_context.dependencies,
            requester_id=request.requester_id,
        )

    # Input: request (Request), project_embedding (np.ndarray | None)
    # Output: tuple[Response, RecommendationLog]
    # Processing: Recommend
    # Changed by: Claude Code
    # Date: 2026-10-12
    def recommend(
        self,
        request: Request,
        *,
        project_embedding: np.ndarray | None = None,
    ) -> tuple[Response, RecommendationLog]:
        query_id = new_query_id()
        framework = request.project_context.framework

        retrieved = self.retrieval.retrieve(request, filters=self.filters_for(request))
        # Belt and braces: the filter already restricted the type in SQL, so
        # anything else here means the index and the store disagree.
        candidates = [c for c in retrieved.candidates if is_template_like(c, include_bundles=self.include_bundles)]
        ctx = self._context(request, project_embedding)

        shortlist = self._stage1(candidates, ctx, framework)
        if self.cross_encoder is not None and shortlist:
            self.cross_encoder.score_candidates(retrieved.query.cross_encoder_text, shortlist)
        ordered = self._stage3(shortlist, ctx, framework)

        above_cutoff = [c for c in ordered if (c.calibrated_score or 0.0) >= self.cutoff]
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
            candidates_retrieved=len(candidates),
            returned=tuple(card.id for card in cards),
            scores=tuple(card.score for card in cards),
            cutoff=self.cutoff,
        )
        return response, log

    # -- Stages ------------------------------------------------------------

    # Input: candidates (Sequence[Candidate]), ctx (FeatureContext), framework (str | None)
    # Output: list[Candidate]
    # Processing: Stage1
    # Changed by: Claude Code
    # Date: 2026-10-12
    def _stage1(
        self, candidates: Sequence[Candidate], ctx: FeatureContext, framework: str | None
    ) -> list[Candidate]:
        if not candidates:
            return []
        features = build_template_feature_matrix(candidates, ctx, framework=framework)
        scores = self.stage1_ranker.score(features)
        for candidate, score in zip(candidates, scores, strict=True):
            candidate.stage1_score = float(score)
        order = np.argsort(-np.asarray(scores), kind="stable")[: self.stage1_top_k]
        return [candidates[i] for i in order]

    # Input: candidates (Sequence[Candidate]), ctx (FeatureContext), framework (str | None)
    # Output: list[Candidate]
    # Processing: Stage3
    # Changed by: Claude Code
    # Date: 2026-10-12
    def _stage3(
        self, candidates: Sequence[Candidate], ctx: FeatureContext, framework: str | None
    ) -> list[Candidate]:
        if not candidates:
            return []
        features = build_template_stage3_matrix(candidates, ctx, framework=framework)
        raw = self.stage3_ranker.score(features)

        if self.is_calibrated:
            calibrated = self.calibrator.predict(raw)
        else:
            # No calibrator yet: a monotone squash so downstream code always
            # sees a 0-1 number. Not a probability -- is_calibrated says so,
            # and a cutoff on it is a guess until the calibrator is fitted.
            calibrated = 1.0 / (1.0 + np.exp(-np.asarray(raw, dtype=np.float64)))

        for candidate, raw_score, cal in zip(candidates, raw, calibrated, strict=True):
            candidate.final_score = float(raw_score)
            candidate.calibrated_score = float(cal)
        return sorted(candidates, key=lambda c: (-(c.calibrated_score or 0.0), c.id))
