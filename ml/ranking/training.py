"""Assemble Stage 1 training data, fit the ranker, and refuse to oversell it.

The pipeline here is the one the spec describes: generate queries, retrieve
candidates for each, label the entry the query came from as relevant, mine the
highest-ranked wrong candidates as hard negatives, group one query per
lambdarank group, fit, and compare against the untrained hand-weighted ranker
on a held-out split.

What this module adds to that is a refusal. A training run on generated
queries produces excellent numbers for a reason that has nothing to do with
ranking quality: the query is built from the entry's description, the keyword
index holds that same description, so BM25 answers the query by string overlap
and a trained model's best available strategy is to copy BM25. On this library
that scores about 0.99 NDCG@10. The run succeeds, the metric is computed
correctly, and the model has learned nothing transferable.

So :class:`TrainingReport` separates *ran* from *certified*. The fit always
runs -- it is how the pipeline gets exercised, and the artefact is real. But
the report is only certified when the query set is not lexically degenerate,
when enough features actually vary to be learned from, and when the trained
model beats the hand-weighted baseline. Anything short of that is reported as
a diagnostic, and ``certified`` is False, so no release gate can read a
paraphrase-matching score as evidence the ranker works.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

import numpy as np

from ml.config import HARD_NEGATIVES_PER_GROUP, STAGE1_TOP_K, LightGBMConfig
from ml.evaluation.metrics import ndcg_at_k, recall_at_k, reciprocal_rank
from ml.evaluation.synthetic import (
    OverlapReport,
    SyntheticQuery,
    measure_overlap,
)
from ml.ingest.dataset import feature_support
from ml.ranking.features import FEATURE_NAMES, FeatureContext, build_feature_matrix
from ml.ranking.stage1 import (
    FeatureRanker,
    LightGBMFeatureRanker,
    LinearFeatureRanker,
    TrainingGroup,
)
from ml.schemas import Entry, ProjectContext, Request

MIN_INFORMATIVE_FEATURES = 6
"""How many of the ten features must vary before a fit is certifiable.

Stage 1 is specified as a judgement over retrieval agreement, usage, trust and
friction. A model fitted where only the retrieval columns vary has been given
no chance to learn the other three groups, so whatever it scores says nothing
about the ranker the spec describes. Six is the majority of ten -- a deliberate
floor rather than a measured one, since the number of varying columns is a
property of the data and not something to tune."""


@dataclass(frozen=True)
class LabelledGroup:
    """One query's candidates, with labels and the ids behind them.

    :class:`TrainingGroup` carries only features and labels, which is all
    LightGBM needs. The ids are kept alongside here so a failure can be read
    back -- "which entry did the ranker put above the right one" is the first
    question after a bad metric, and it is unanswerable from a feature matrix.
    """

    query: str
    query_id: str
    target_id: str
    language: str
    candidate_ids: tuple[str, ...]
    features: np.ndarray
    labels: np.ndarray

    # Input: none
    # Output: TrainingGroup
    # Processing: Narrow to what LightGBM consumes
    # Changed by: Claude Code
    # Date: 2026-10-12
    def as_training_group(self) -> TrainingGroup:
        return TrainingGroup(features=self.features, labels=self.labels, query_id=self.query_id)


# Input: query_id (str), fractions (tuple[float, float, float])
# Output: str
# Processing: Deterministically assign a key to train, valid or test
# Changed by: Claude Code
# Date: 2026-10-12
def _assign_split(key: str, fractions: tuple[float, float, float]) -> str:
    """Hash a key into one of three splits. Stable across runs and machines.

    Hashing rather than shuffling means a given entry lands in the same split
    every run without a seed having to be threaded through, so two training
    runs are comparable.
    """
    digest = hashlib.blake2b(key.encode(), digest_size=8).hexdigest()
    position = int(digest, 16) / float(1 << 64)
    train, valid, _ = fractions
    if position < train:
        return "train"
    if position < train + valid:
        return "valid"
    return "test"


# Input: queries (Sequence[SyntheticQuery]), fractions (tuple[float, float, float])
# Output: dict[str, list[SyntheticQuery]]
# Processing: Split queries into train/valid/test by source entry
# Changed by: Claude Code
# Date: 2026-10-12
def split_by_entry(
    queries: Sequence[SyntheticQuery],
    *,
    fractions: tuple[float, float, float] = (0.7, 0.15, 0.15),
) -> dict[str, list[SyntheticQuery]]:
    """Split on the *source entry*, never on the query.

    Three queries generated from one entry are near-paraphrases of each other.
    Splitting per query would put two of them in train and the third in test,
    and the test score would then measure whether the model remembers an entry
    it was trained on -- which it does, perfectly. Splitting on the entry is
    what makes the held-out split held out.
    """
    if abs(sum(fractions) - 1.0) > 1e-6:
        raise ValueError(f"split fractions must sum to 1, got {fractions}")
    out: dict[str, list[SyntheticQuery]] = {"train": [], "valid": [], "test": []}
    for query in queries:
        out[_assign_split(query.entry_id, fractions)].append(query)
    return out


# Input: service (object), query (SyntheticQuery), ctx (FeatureContext), negatives (int), query_id (str)
# Output: LabelledGroup | None
# Processing: Retrieve for one query and label the candidates
# Changed by: Claude Code
# Date: 2026-10-12
def assemble_group(
    service,
    query: SyntheticQuery,
    ctx: FeatureContext,
    *,
    negatives: int = HARD_NEGATIVES_PER_GROUP,
    query_id: str | None = None,
) -> LabelledGroup | None:
    """Build one lambdarank group: the target plus its hardest rivals.

    Returns ``None`` when retrieval misses the target entirely. Such a query
    cannot teach a ranker anything -- the right answer is not among the rows
    it would score -- and keeping it as an all-negative group would push the
    model to drive every score down.

    The negatives are the highest-ranked wrong candidates rather than a random
    sample, because a random negative out of 515 is almost always obviously
    wrong and separating it from the target is free. The rivals retrieval
    already ranks highly are the ones the model has to learn to beat.
    """
    request = Request(
        query=query.query,
        project_context=ProjectContext(languages=(query.language,)),
    )
    candidates = service.retrieve(request).candidates
    target = next((c for c in candidates if c.id == query.entry_id), None)
    if target is None:
        return None

    rivals = [c for c in candidates if c.id != query.entry_id][:negatives]
    chosen = [target, *rivals]
    labels = np.array([1.0] + [0.0] * len(rivals), dtype=np.float32)
    return LabelledGroup(
        query=query.query,
        query_id=query_id or f"{query.entry_id}::{abs(hash(query.query)) % 10**8}",
        target_id=query.entry_id,
        language=query.language,
        candidate_ids=tuple(c.id for c in chosen),
        features=build_feature_matrix(chosen, ctx),
        labels=labels,
    )


# Input: service (object), queries (Sequence[SyntheticQuery]), ctx (FeatureContext), negatives (int)
# Output: list[LabelledGroup]
# Processing: Assemble a labelled group per query, dropping retrieval misses
# Changed by: Claude Code
# Date: 2026-10-12
def assemble_groups(
    service,
    queries: Sequence[SyntheticQuery],
    ctx: FeatureContext,
    *,
    negatives: int = HARD_NEGATIVES_PER_GROUP,
) -> list[LabelledGroup]:
    """Assemble every query that retrieval can actually serve."""
    groups: list[LabelledGroup] = []
    for index, query in enumerate(queries):
        group = assemble_group(
            service, query, ctx, negatives=negatives, query_id=f"q{index:06d}"
        )
        if group is not None:
            groups.append(group)
    return groups


# Input: ranker (FeatureRanker), groups (Sequence[LabelledGroup]), ndcg_k (int), recall_k (int)
# Output: dict[str, float]
# Processing: Score each group and average the ranking metrics
# Changed by: Claude Code
# Date: 2026-10-12
def evaluate_ranker(
    ranker: FeatureRanker,
    groups: Sequence[LabelledGroup],
    *,
    ndcg_k: int = 10,
    recall_k: int = 3,
) -> dict[str, float]:
    """NDCG@k, MRR and recall@k averaged over groups.

    Positions stand in for ids, so these reuse the same metric functions the
    evaluation harness uses rather than a second implementation that could
    drift from it.
    """
    if not groups:
        return {"ndcg": 0.0, "mrr": 0.0, "recall": 0.0, "groups": 0.0}

    ndcgs: list[float] = []
    rrs: list[float] = []
    recalls: list[float] = []
    for group in groups:
        scores = ranker.score(group.features)
        order = np.argsort(-np.asarray(scores), kind="stable")
        ranked = [str(int(i)) for i in order]
        relevance = {str(i): float(group.labels[i]) for i in range(len(group.labels))}
        relevant = {key for key, value in relevance.items() if value > 0}
        ndcgs.append(ndcg_at_k(ranked, relevance, k=ndcg_k))
        rrs.append(reciprocal_rank(ranked, relevant))
        recalls.append(recall_at_k(ranked, relevant, k=recall_k))

    return {
        "ndcg": float(np.mean(ndcgs)),
        "mrr": float(np.mean(rrs)),
        "recall": float(np.mean(recalls)),
        "groups": float(len(groups)),
    }


@dataclass(frozen=True)
class TrainingReport:
    """What a training run produced, and whether it may be believed.

    ``certified`` is the field that matters. A run can complete, fit a real
    model, and report a near-perfect metric while establishing nothing; see
    the module docstring. Every reason a run is not certifiable is listed in
    ``blockers`` so the summary says *why* rather than only that.
    """

    overlap: OverlapReport
    varying_features: Mapping[str, bool]
    informative_features: tuple[str, ...]
    split_sizes: Mapping[str, int]
    baseline: Mapping[str, float]
    trained: Mapping[str, float]
    importance: Mapping[str, float] = field(default_factory=dict)
    model_path: str | None = None

    # Input: none
    # Output: list[str]
    # Processing: Every reason this run cannot be treated as validation
    # Changed by: Claude Code
    # Date: 2026-10-12
    @property
    def blockers(self) -> list[str]:
        """Reasons the run is a diagnostic rather than a validation."""
        reasons: list[str] = []
        if self.overlap.degenerate:
            reasons.append(
                f"query set is lexically degenerate (mean overlap "
                f"{self.overlap.mean:.2f} >= {self.overlap.threshold:.2f}): the "
                f"queries restate the descriptions BM25 indexes, so a high score "
                f"measures paraphrase matching"
            )
        if len(self.informative_features) < MIN_INFORMATIVE_FEATURES:
            missing = [n for n in FEATURE_NAMES if n not in self.informative_features]
            reasons.append(
                f"only {len(self.informative_features)} of {len(FEATURE_NAMES)} "
                f"features vary, below the floor of {MIN_INFORMATIVE_FEATURES}; "
                f"constant here: {', '.join(missing)}"
            )
        if self.trained.get("ndcg", 0.0) <= self.baseline.get("ndcg", 0.0):
            reasons.append(
                f"trained NDCG@10 {self.trained.get('ndcg', 0.0):.4f} does not beat "
                f"the hand-weighted baseline {self.baseline.get('ndcg', 0.0):.4f}"
            )
        return reasons

    # Input: none
    # Output: bool
    # Processing: Whether this run validates the ranker
    # Changed by: Claude Code
    # Date: 2026-10-12
    @property
    def certified(self) -> bool:
        """True only when nothing blocks reading this run as validation."""
        return not self.blockers

    # Input: none
    # Output: str
    # Processing: Human-readable training report
    # Changed by: Claude Code
    # Date: 2026-10-12
    def summary(self) -> str:
        lines = [
            "Stage 1 training run",
            "=" * 60,
            f"queries        {self.overlap.summary()}",
            f"features       {len(self.informative_features)}/{len(FEATURE_NAMES)} vary: "
            f"{', '.join(self.informative_features)}",
            f"split          " + ", ".join(f"{k}={v}" for k, v in self.split_sizes.items()),
            "",
            f"{'metric':<12} {'baseline':>10} {'trained':>10} {'delta':>10}",
        ]
        for name in ("ndcg", "mrr", "recall"):
            base = self.baseline.get(name, 0.0)
            trained = self.trained.get(name, 0.0)
            lines.append(f"{name:<12} {base:>10.4f} {trained:>10.4f} {trained - base:>+10.4f}")
        if self.importance:
            ranked = sorted(self.importance.items(), key=lambda kv: -kv[1])
            top = ", ".join(f"{k} {v:.0f}" for k, v in ranked if v > 0) or "none"
            lines += ["", f"gain           {top}"]
        lines += ["", f"CERTIFIED      {self.certified}"]
        for reason in self.blockers:
            lines.append(f"  blocker      {reason}")
        if self.model_path:
            lines.append(f"artefact       {self.model_path}")
        return "\n".join(lines)


# Input: service (object), entries (Sequence[Entry]), queries (Sequence[SyntheticQuery]), ctx (FeatureContext), config (LightGBMConfig | None), model_path (str | None)
# Output: TrainingReport
# Processing: Run the full Stage 1 training pipeline and report honestly
# Changed by: Claude Code
# Date: 2026-10-12
def run_stage1_training(
    service,
    entries: Sequence[Entry],
    queries: Sequence[SyntheticQuery],
    ctx: FeatureContext | None = None,
    *,
    config: LightGBMConfig | None = None,
    model_path: str | None = None,
) -> TrainingReport:
    """Train Stage 1 end to end and return a report that states its own limits.

    The fit runs whatever the diagnostics say. An uncertifiable run is still
    worth completing: it exercises the pipeline, and a report that says "ran,
    not certified, here is why" is more useful than a refusal that leaves the
    machinery untested.
    """
    from ml.ranking.features import UsageIndex

    ctx = ctx or FeatureContext.build(usage_index=UsageIndex())
    overlap = measure_overlap(queries, {e.id: e.description for e in entries})
    varying = feature_support(entries)

    splits = split_by_entry(queries)
    assembled = {name: assemble_groups(service, qs, ctx) for name, qs in splits.items()}

    # Which columns actually move once the features are built. This is the
    # honest version of the question -- a field can vary across entries and
    # still be constant within every group, which is just as unlearnable.
    stacked = np.concatenate(
        [g.features for groups in assembled.values() for g in groups], axis=0
    )
    informative = tuple(
        name
        for index, name in enumerate(FEATURE_NAMES)
        if stacked.size and len(np.unique(stacked[:, index])) > 1
    )

    baseline_ranker = LinearFeatureRanker()
    ranker = LightGBMFeatureRanker(config)
    ranker.fit(
        [g.as_training_group() for g in assembled["train"]],
        validation=[g.as_training_group() for g in assembled["valid"]] or None,
    )
    if model_path:
        ranker.save(model_path)

    return TrainingReport(
        overlap=overlap,
        varying_features=varying,
        informative_features=informative,
        split_sizes={name: len(groups) for name, groups in assembled.items()},
        baseline=evaluate_ranker(baseline_ranker, assembled["test"]),
        trained=evaluate_ranker(ranker, assembled["test"]),
        importance=ranker.feature_importance(),
        model_path=model_path,
    )


__all__ = [
    "MIN_INFORMATIVE_FEATURES",
    "LabelledGroup",
    "TrainingReport",
    "assemble_group",
    "assemble_groups",
    "evaluate_ranker",
    "run_stage1_training",
    "split_by_entry",
]


# Input: argv (Sequence[str] | None)
# Output: int
# Processing: CLI -- build the library, generate queries, train, print the report
# Changed by: Claude Code
# Date: 2026-10-12
def main(argv: Sequence[str] | None = None) -> int:
    """Run a Stage 1 training pass over a harvested dataset.

    Exits non-zero when the run is not certified, so this can gate a pipeline
    without the caller having to parse the report.
    """
    import argparse

    from ml.evaluation.synthetic import generate_training_queries
    from ml.ingest.dataset import DEFAULT_DATASET, load_dataset
    from ml.retrieval.embedding import HashingEmbeddingModel, embed_entries_inplace
    from ml.retrieval.keyword import InMemoryBM25Index
    from ml.retrieval.service import RetrievalService
    from ml.retrieval.vector import InMemoryVectorIndex

    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dataset", default=str(DEFAULT_DATASET), help="harvested entries.jsonl")
    parser.add_argument("--per-entry", type=int, default=3, help="queries generated per entry")
    parser.add_argument("--negatives", type=int, default=HARD_NEGATIVES_PER_GROUP)
    parser.add_argument("--out", default=None, help="where to save the fitted booster")
    parser.add_argument(
        "--languages", nargs="*", default=None, help="restrict the library to these languages"
    )
    args = parser.parse_args(argv)

    dataset = load_dataset(args.dataset, languages=args.languages)
    entries = list(dataset.entries)
    if not entries:
        parser.error(f"no usable entries in {args.dataset}")

    model = HashingEmbeddingModel()
    embed_entries_inplace(model, entries)
    service = RetrievalService(
        keyword_index=InMemoryBM25Index(entries),
        vector_index=InMemoryVectorIndex(entries),
        embedding_model=model,
        entry_store={entry.id: entry for entry in entries},
    )

    # Only entries with an author's own description generate queries; see
    # ml.ingest.dataset for why a derived description cannot be a query source.
    queries = generate_training_queries(dataset.authored, per_entry=args.per_entry)
    report = run_stage1_training(service, entries, queries, model_path=args.out)
    print(report.summary())
    return 0 if report.certified else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
