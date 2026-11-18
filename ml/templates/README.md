# Template recommender

The template arm of the ranking stack. The spec keeps separate rankers for
templates and helpers — "or one model with a type token prepended; pick
whichever scores higher on the evaluation set" — and this is the separate-model
arm, with the type token included so the same code trains the single-model arm
too. Which wins is an experiment to run, not a decision made here.

| File | Holds |
| --- | --- |
| `features.py` | The three template features appended to the shared ten, and why two shared ones mean something different here |
| `ranking.py` | Stage 1 and Stage 3 rankers, hand weights for the untrained path, LightGBM for the trained one |
| `cross_encoder.py` | Stage 2: the spec's layer table on a template-shaped input (requires torch) |
| `recommender.py` | The whole thing wired together: request in, slim cards out |

## What differs from the helper path, and why

**Dependency compatibility inverts.** For a helper, needing a new package is
friction. For a template it is the product — a FastAPI scaffold is supposed to
bring FastAPI. Carrying the helper weighting over would rank the emptiest
scaffold first, so `dependency_compat` is weighted near zero and replaced by a
plain count of what is new. The same reasoning drops `drop_in_only` from the
filters: applied to templates it removes every scaffold worth having.

**Framework match gets its own feature, and the largest hand weight.** A
Next.js template in a Vue project is not a worse answer, it is an unusable one.
A template with no framework of its own scores 0.5 rather than 0 — it fits
anywhere, so it is not a mismatch, but it is not the targeted answer either.

**No signature.** The spec leaves `signature` off a template's card because a
template is not called. It is absent from the model input too, so the model
never sees an empty slot.

**The code is the description.** A helper's docstring carries the meaning and
the body is detail; a template's blurb is thin and the scaffold itself says
what it is. So the candidate side reserves a floor for code, and truncation
keeps the file's *head* — imports, top-level declarations, structure — because
a template's tail is boilerplate and the right thing to lose.

**MMR runs at λ = 0.6, not 0.7.** Near-duplicates are the normal case here:
ten React starters differ in lint config and little else, and three slots spent
on one starter is a list with one real option in it. A starting value to tune,
like the spec's own.

## Running it

```python
from ml.templates import TemplateRecommender

recommender = TemplateRecommender(retrieval=retrieval_service)   # untrained path
response, log = recommender.recommend(request)
```

Works on day one with no trained model and no torch: the hand-weighted rankers
score the same features, and Stage 2 is skipped. `log` carries the model
versions; `response` deliberately does not.

## Before this is trained

Nothing here is trained, and two things are needed first — a labelled gold set
with template queries in every MVP language, and enough accepted
recommendations to fit the rankers and the calibrator. Until the calibrator is
fitted, `is_calibrated` is `False` and the scores are **not** probabilities, so
a relevance cutoff on them is a guess. That is why `TemplateRecommender`
reports the flag rather than hiding it.

The release gate in `ml.evaluation` decides whether a trained model ships,
not a training run's own loss curve.
