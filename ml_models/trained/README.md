# Model artefacts

Each file here is a trained model. **A file existing in this folder does not
mean it may be served.** The status column is the only thing that says so, and
it is set by what the training harness certified, not by how the metric looked.

| Artefact | Stage | Status | May it serve? |
| --- | --- | --- | --- |
| `stage1_retrieval_fusion.txt` | 1 | **uncertified** | **No** |
| `stage3_final.txt` | 3 | **uncertified** | **No** |
| `cross-encoder-stage2` (release asset, see below) | 2 | **uncertified** | Only when passed to the pipeline explicitly |

What ships today is the hand-weighted `LinearFeatureRanker` and
`LinearFinalRanker`. They are real rankers, they were always the day-one plan,
and on the measurement that matters they are still competitive with the trained
alternatives.

## Why neither is certified

**Stage 1** (`stage1_retrieval_fusion.txt`) — trained on the full retrieved
candidate set, 5-fold cross-validated by entry. NDCG@10 0.5066 ± 0.0515
against 0.4358 ± 0.0582 for the hand weights: **+0.0708 ± 0.0473, winning 4 of
5 folds.** The gain is roughly the size of its own spread and it loses a fold,
so `CrossValidationResult.consistent` refuses it. A mean improvement containing
a losing fold means the model found something split-specific.

**Stage 3** (`stage3_final.txt`) — fits and produces a real booster, but
`cross_encoder_score` never varied, because there is no cross-encoder: its
weights are unreachable from this environment. Stage 3 without Stage 2 is
Stage 1's feature set with a constant column, which is not the model the spec
describes. No calibrator is fitted either, so the relevance cutoff has no
meaning.

## The mistake this folder exists to prevent

An earlier `stage1_retrieval_fusion.txt` was committed and reported as a 46%
improvement, cross-validated, 5 folds out of 5. It was trained on groups of one
positive and the 15 hardest negatives, and scored on that same 16-row shape.

Asked to rank a real retrieved set of ~200 candidates, it scored **NDCG@10
0.0066 against the baseline's 0.3428** — about fifty times worse than the
ranker it was meant to replace. The model had never seen the long tail of weak
candidates that makes up most of a real request, so it assigned them
indistinguishable scores and effectively randomised.

Nothing about the run looked wrong. The data checks passed, the folds were
consistent, the metric was computed correctly. The evaluation was simply of a
task nobody performs. `assemble_group` now keeps the whole retrieved set by
default, and truncating it is a certification blocker.

## Reproducing

```sh
# Stage 1
python -m ml.ranking.training --out ml_models/trained/stage1_retrieval_fusion.txt
```

The library is rebuilt from `seed/sources.json`: clone each repository at the
commit recorded there and run `ml.ingest.local_source.harvest_clones` over the
clones. `seed/entries.jsonl` is gitignored as regenerable output.

Queries come from `seed/queries.jsonl`, which is tracked. They are
hand-written, and `GoldQuery` refuses them for evaluation on purpose: whoever
wrote them had read the entries, so each target is correct by construction.
**No artefact here has been measured against real demand.** That needs the
queries tracked in issue #19.

## Stage 2 cross-encoder

The trained cross-encoder is too large for git (505 MB), so it lives in the
private release `cross-encoder-20261017T142721Z` and `weights/manifest.json`
records its URL and SHA-256 as `cross-encoder-stage2`. From a fresh clone:

```sh
GITHUB_TOKEN=... python scripts/setup_cross_encoder.py
```

That fetches the base encoder and the trained weights, checks both hashes,
loads the model and confirms it ranks a right entry above a wrong one. Then:

```python
from ml.ranking.cross_encoder import load_cross_encoder

pipeline = RecommendationPipeline(
    ...,
    cross_encoder=load_cross_encoder("weights/cross-encoder-stage2/cross_encoder_final.pt"),
)
```

With a cross-encoder passed in, Stage 3 orders by its score
(`STAGE3_HAND_WEIGHTS_WITH_CROSS_ENCODER`). Held-out, 350 queries, 5 folds:
@1 69.7% / @3 88.3% / @10 96.0%, against 52.0% / 69.7% / 81.4% for
retrieval order. Not certified, for the reason above. Scoring 100 candidates
takes 10-30 s on a CPU and well under a second on a GPU; reranking only the
top 20 keeps @1 at 67.1% (issue 35).
