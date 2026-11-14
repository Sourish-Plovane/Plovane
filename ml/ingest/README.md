# Seeding the library from GitHub

Harvests reusable code from GitHub into Plovane's own library, licence-first.
Entries live in Plovane's store and *cite* their source repository; GitHub is
the source, not the database.

| File | Holds |
| --- | --- |
| `licensing.py` | The eligibility gate: an allowlist of permissive SPDX ids |
| `provenance.py` | Attribution — licence text, copyright notice, SHA-pinned source |
| `github_client.py` | Rate-limit-aware REST client, stdlib only |
| `quota.py` | How 1,200 entries divide across categories and languages |
| `extract.py` | Pulling functions and templates out of files |
| `harvest.py` | The orchestrator and its CLI |

## Run it

```bash
python -m ml.ingest --target 1200 --plan-only          # no API calls
GITHUB_TOKEN=... python -m ml.ingest --target 1200 --out ml/data/

# Choose the template/helper split. One or neither, never both.
python -m ml.ingest --target 1200 --templates 900 --plan-only
python -m ml.ingest --target 1200 --template-share 0.75 --plan-only
```

Without either flag a quarter of the target is harvested as templates. The
count is approximate: the split is applied per (category, language) cell and
the rounding accumulates, so `--templates 900` yields about 902.

Output is `ml/data/entries.jsonl`, written and flushed per entry so a run that
dies at 900 leaves 900 usable entries, plus a report counting what was
rejected and why.

## Attribution is not a credit line

The spec has the server copy the exact code into a user's project. That is
redistribution, and what it requires is set by the source licence, not by us:

- **MIT, BSD, ISC** require the licence text *and* the copyright notice travel
  with the code. Naming the repo in a comment does not satisfy them, which is
  why `Attribution` carries the full text and `render_third_party_licenses()`
  emits a `THIRD_PARTY_LICENSES` file.
- **Apache-2.0** adds the upstream `NOTICE`, where one exists.
- **GPL, AGPL, LGPL, MPL** are copyleft. Pasted into a user's proprietary
  project they impose terms on *their* work, so they are rejected at the gate
  rather than flagged downstream.
- **No licence is the dangerous case**, and it is a large share of GitHub. No
  licence means all rights reserved: readable, not copyable. Treating silence
  as permission is the most likely way this pipeline causes real harm, so
  `other`, `NOASSERTION` and a 404 from the licence endpoint are all hard
  rejections.

The gate is therefore an **allowlist**, not a denylist: anything unrecognised
is ineligible until a person reviews it.

Two further safeguards worth knowing about:

- **The commit SHA is pinned.** A repository can relicense or delete a file
  after harvest. Recording the SHA with the licence as it was *at that SHA*
  states precisely which bytes were taken under which terms.
- **Vendored and generated trees are skipped.** `node_modules/`, `vendor/`,
  `third_party/`, `dist/`, minified and generated files. Their licence is not
  the host repository's, so mining them would attach the wrong attribution.
  The path matcher is anchored, because GitHub tree paths have no leading
  slash and an unanchored marker misses `node_modules/` at the repo root.

## What the plan buys you

1,200 across 16 categories and 6 languages, with a floor of 60 per language
before anything is distributed by weight. The floor is the point: the spec
requires every metric reported per language, and a language with a handful of
entries cannot be measured — its NDCG@10 moves by whole points on one query.
The floor is what buys a trustworthy CUDA number, and CUDA is exactly where
the spec warns data is scarcest.

The 16 categories are a **proposal, not from the spec**, which only names
`data-processing`, `machine-learning` and `scaffolding` in passing. Correct
`CATEGORIES` and `CATEGORY_LANGUAGES` in `quota.py` first if they do not match
how the library is actually organised — everything downstream follows them.

## What a harvested entry is not

A candidate, not a library entry. The spec expects entries to carry
mutation-tested tests, a generated description and an embedding; none of that
exists at this stage. Non-Python extraction is a brace-matching heuristic and
every unit it produces is marked `needs_review`, as is every entry whose
copyright holder could not be parsed. Nothing here judges whether the code is
any *good* — that still needs a person, and the gold set still needs labelling
before the models can be measured on any of it.
