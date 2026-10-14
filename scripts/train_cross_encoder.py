"""Train and evaluate the Stage 2 cross-encoder on a GPU box.

This runs where the GPU is, not where this repository is edited. The session
that wrote it has no route to a rented GPU -- outbound traffic is forced
through an HTTPS proxy, there is no ssh client and no raw TCP -- so the job
travels in the repository and the GPU pulls it.

What it does, in order:

1.  Rebuilds retrieval exactly as the pipeline runs it, UniXcoder included,
    so the candidates the cross-encoder trains on are the candidates it will
    be asked to rerank. Training on a different candidate distribution than
    serving is what cost Stage 1 a factor of 50 in NDCG, and that mistake is
    not worth repeating at GPU prices.
2.  Splits by **entry**, not by query. A fold's test entries are never a
    positive during its training, so a fold's score is what a newly added
    library entry would get rather than what a memorised one gets.
3.  Mines hard negatives from real retrieval output, which is the only place
    close look-alikes appear.
4.  Reranks the held-out queries at several depths and reports @1/@3/@10
    against the retrieval baseline it started from.
5.  Writes weights and a metrics file, and prints the SHA-256 of the weights
    so ``scripts/fetch_weights.py`` can verify them on the way back.

Nothing here decides whether the result ships. The release gate in
``ml.evaluation`` does, and it will refuse these queries, because they were
written by the same assistant that wrote this file -- see issue 19. Treat the
numbers as a measurement of headroom, not as a certification.

Usage on the GPU box::

    python scripts/train_cross_encoder.py --epochs 3 --negatives 15

Add ``--smoke`` first: it runs two folds at one epoch over a handful of
queries, which catches a broken environment in a couple of minutes instead of
after an hour of rented time.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import platform
import time
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from pathlib import Path

import numpy as np

from ml.config import TrainingConfig
from ml.ingest.dataset import load_dataset, load_queries
from ml.ranking.cross_encoder import (
    CrossEncoder,
    CrossEncoderGroup,
    mine_hard_negatives,
    train_cross_encoder,
)
from ml.retrieval.bi_encoder import CodeBiEncoder
from ml.retrieval.keyword import InMemoryBM25Index
from ml.retrieval.query import build_query
from ml.retrieval.service import RetrievalService
from ml.retrieval.vector import InMemoryVectorIndex
from ml.schemas import ProjectContext, Request

CUTOFFS = (1, 3, 10)


@dataclass
class FoldResult:
    """One fold's before and after, on its own held-out queries."""

    fold: int
    test_queries: int
    train_groups: int
    baseline: dict[str, float]
    reranked: dict[str, float]
    final_loss: float
    seconds: float


# Input: ranked_ids (list[str]), target (str)
# Output: dict[str, float]
# Processing: Hit rate at each cutoff for one query, as 0.0 or 1.0 per cutoff.
# Changed by: Claude Code
# Date: 2026-10-14
def hits_at(ranked_ids: list[str], target: str) -> dict[str, float]:
    """Hit rate at each cutoff for one query, as 0.0 or 1.0 per cutoff."""
    return {f"@{k}": float(target in ranked_ids[:k]) for k in CUTOFFS}


# Input: rows (list[dict[str, float]])
# Output: dict[str, float]
# Processing: Mean of per-query hit rates, which is the @k over a query set.
# Changed by: Claude Code
# Date: 2026-10-14
def mean_hits(rows: list[dict[str, float]]) -> dict[str, float]:
    """Mean of per-query hit rates, which is the @k over a query set."""
    if not rows:
        return {f"@{k}": 0.0 for k in CUTOFFS}
    return {f"@{k}": float(np.mean([r[f"@{k}"] for r in rows])) for k in CUTOFFS}


# Input: candidate_ids (Sequence[str]), head_scores (Sequence[float]), depth (int)
# Output: list[str]
# Processing: Reorder the first `depth` candidates by score, keep the tail as-is.
# Changed by: Claude Code
# Date: 2026-10-14
def reranked_order(
    candidate_ids: Sequence[str], head_scores: Sequence[float], *, depth: int
) -> list[str]:
    """Reorder the first ``depth`` candidates by score, highest first.

    The tail beyond ``depth`` keeps its retrieval order, so a cutoff wider
    than the rerank depth stays comparable: reranking the top 10 must not be
    able to change @100.

    This is a separate function so the ordering convention can be pinned by a
    test. "Higher score ranks first" matches Stage 3, which gives
    ``cross_encoder_score`` a positive weight, and a sign error here would be
    invisible in a smoke test -- random scores and inverted scores both leave
    @10 untouched while wrecking @1.
    """
    head = list(candidate_ids[:depth])
    if len(head_scores) != len(head):
        raise ValueError(f"got {len(head_scores)} scores for {len(head)} candidates")
    order = sorted(range(len(head)), key=lambda i: -head_scores[i])
    return [head[i] for i in order] + list(candidate_ids[depth:])


# Input: path (Path)
# Output: str
# Processing: SHA-256 of a file, so the weights can be verified after transfer.
# Changed by: Claude Code
# Date: 2026-10-14
def sha256_of(path: Path) -> str:
    """SHA-256 of a file, so the weights can be verified after transfer."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


# Input: args (argparse.Namespace)
# Output: int
# Processing: Build retrieval, train per fold, measure, write artefacts.
# Changed by: Claude Code
# Date: 2026-10-14
def main(args: argparse.Namespace) -> int:
    import torch

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    print(f"torch {torch.__version__}, cuda available: {torch.cuda.is_available()}")
    if torch.cuda.is_available():
        print(f"  device: {torch.cuda.get_device_name(0)}")
    elif not args.allow_cpu:
        print(
            "No CUDA device. This job is hours on a GPU and weeks on CPU, so it "
            "refuses to start rather than quietly burn rented time at 1/100 speed. "
            "Pass --allow-cpu to override (use with --smoke)."
        )
        return 2

    # -- Retrieval, exactly as the pipeline builds it -----------------------
    dataset = load_dataset()
    entries = list(dataset.entries)
    by_id = {e.id: e for e in entries}
    queries = load_queries(entry_ids=set(by_id))
    if args.smoke:
        queries = queries[: args.smoke_queries]

    print(f"library {len(entries)} entries, {len(queries)} queries")
    encoder = CodeBiEncoder(args.encoder, dimension=768)
    for entry, vector in zip(entries, encoder.encode_entries(entries)):
        entry.embedding = vector
    retrieval = RetrievalService(
        keyword_index=InMemoryBM25Index(entries),
        vector_index=InMemoryVectorIndex(entries),
        embedding_model=encoder,
        entry_store=by_id,
    )
    print(f"  rrf arm weights {retrieval.rrf_arm_weights}")

    # Retrieve once per query and reuse. The candidates are what both the
    # training groups and the reranking evaluation are built from, so they
    # have to be the same list in both places.
    retrieved: dict[str, list] = {}
    for query in queries:
        request = Request(
            query=query.query, project_context=ProjectContext(languages=(query.language,))
        )
        retrieved[query.query] = list(retrieval.retrieve(request).candidates)

    # -- Fold assignment, by entry ------------------------------------------
    folds = args.folds if not args.smoke else 2
    entry_fold = np.random.default_rng(args.seed).integers(0, folds, size=len(entries))
    index_of = {e.id: i for i, e in enumerate(entries)}
    query_fold = np.array([entry_fold[index_of[q.entry_id]] for q in queries])

    results: list[FoldResult] = []
    for fold in range(folds):
        started = time.time()
        train_queries = [q for q, f in zip(queries, query_fold) if f != fold]
        test_queries = [q for q, f in zip(queries, query_fold) if f == fold]
        if not test_queries or not train_queries:
            print(f"fold {fold}: empty split, skipped")
            continue

        groups = [
            CrossEncoderGroup(
                query_text=build_query(
                    Request(query=q.query, project_context=ProjectContext(languages=(q.language,)))
                ).cross_encoder_text,
                positive=by_id[q.entry_id],
                negatives=tuple(
                    mine_hard_negatives(
                        retrieved[q.query], [q.entry_id], count=args.negatives
                    )
                ),
            )
            for q in train_queries
        ]
        # A group with no negatives teaches nothing: the softmax over a single
        # logit is constant, so its gradient is zero.
        groups = [g for g in groups if g.negatives]

        model = CrossEncoder(args.encoder)
        config = TrainingConfig(
            epochs=1 if args.smoke else args.epochs,
            batch_groups=args.batch_groups,
            learning_rate=args.learning_rate,
        )
        history = train_cross_encoder(model, groups, config=config)

        baseline_rows, reranked_rows = [], []
        for query in test_queries:
            candidates = retrieved[query.query]
            if not candidates:
                continue
            baseline_rows.append(hits_at([c.id for c in candidates], query.entry_id))
            head = candidates[: args.rerank_depth]
            query_text = build_query(
                Request(
                    query=query.query,
                    project_context=ProjectContext(languages=(query.language,)),
                )
            ).cross_encoder_text
            # score_candidates takes Candidate objects, not entries: it reads
            # c.entry itself and writes the score back onto each candidate.
            scored = model.score_candidates(query_text, head)
            reranked_rows.append(
                hits_at(
                    reranked_order(
                        [c.id for c in candidates], scored, depth=args.rerank_depth
                    ),
                    query.entry_id,
                )
            )

        result = FoldResult(
            fold=fold,
            test_queries=len(reranked_rows),
            train_groups=len(groups),
            baseline=mean_hits(baseline_rows),
            reranked=mean_hits(reranked_rows),
            final_loss=float(history[-1]) if history else float("nan"),
            seconds=time.time() - started,
        )
        results.append(result)
        print(
            f"fold {fold}: {result.train_groups} groups -> {result.test_queries} test queries, "
            f"loss {result.final_loss:.4f}, {result.seconds:.0f}s"
        )
        for k in CUTOFFS:
            before, after = result.baseline[f"@{k}"], result.reranked[f"@{k}"]
            print(f"    @{k:<3} {100*before:5.1f}%  ->  {100*after:5.1f}%   {100*(after-before):+5.1f}")

        if fold == 0:
            weights = out / "cross_encoder_fold0.pt"
            torch.save(model.state_dict(), weights)
            print(f"    weights {weights} sha256 {sha256_of(weights)}")

    if not results:
        print("no fold produced a result")
        return 1

    # -- Aggregate, weighted by how many queries each fold tested -----------
    counts = np.array([r.test_queries for r in results], dtype=float)
    share = counts / counts.sum()
    aggregate = {
        "baseline": {
            f"@{k}": float(sum(s * r.baseline[f"@{k}"] for s, r in zip(share, results)))
            for k in CUTOFFS
        },
        "reranked": {
            f"@{k}": float(sum(s * r.reranked[f"@{k}"] for s, r in zip(share, results)))
            for k in CUTOFFS
        },
    }
    print("\nheld out by entry, all folds pooled:")
    for k in CUTOFFS:
        before, after = aggregate["baseline"][f"@{k}"], aggregate["reranked"][f"@{k}"]
        print(f"  @{k:<3} retrieval {100*before:5.1f}%  ->  reranked {100*after:5.1f}%   {100*(after-before):+5.1f}")

    report = {
        "aggregate": aggregate,
        "folds": [asdict(r) for r in results],
        "settings": {
            "encoder": args.encoder,
            "epochs": 1 if args.smoke else args.epochs,
            "negatives": args.negatives,
            "rerank_depth": args.rerank_depth,
            "batch_groups": args.batch_groups,
            "learning_rate": args.learning_rate,
            "folds": folds,
            "seed": args.seed,
            "smoke": args.smoke,
        },
        "environment": {
            "torch": torch.__version__,
            "cuda": torch.cuda.is_available(),
            "device": torch.cuda.get_device_name(0) if torch.cuda.is_available() else None,
            "python": platform.python_version(),
        },
        "certified": False,
        "why_not_certified": (
            "Queries are assistant-authored. Holding out by entry controls for "
            "entry memorisation, not for query style, so this measures headroom "
            "and not release readiness. See issue 19."
        ),
    }
    metrics = out / "cross_encoder_metrics.json"
    metrics.write_text(json.dumps(report, indent=2))
    print(f"\nwrote {metrics}")
    print("commit the metrics file; keep the .pt out of git and host it for fetch_weights.py")
    return 0


# Input: none
# Output: argparse.Namespace
# Processing: Parse arguments
# Changed by: Claude Code
# Date: 2026-10-14
def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--encoder", default="weights/unixcoder-base")
    parser.add_argument("--epochs", type=int, default=3)
    parser.add_argument("--negatives", type=int, default=15)
    parser.add_argument("--rerank-depth", type=int, default=100)
    parser.add_argument("--batch-groups", type=int, default=8)
    parser.add_argument("--learning-rate", type=float, default=2e-5)
    parser.add_argument("--folds", type=int, default=5)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--out", default="artifacts")
    parser.add_argument(
        "--smoke", action="store_true", help="two folds, one epoch, few queries"
    )
    parser.add_argument("--smoke-queries", type=int, default=20)
    parser.add_argument(
        "--allow-cpu", action="store_true", help="run without CUDA (only sane with --smoke)"
    )
    return parser.parse_args()


if __name__ == "__main__":
    raise SystemExit(main(parse_args()))
