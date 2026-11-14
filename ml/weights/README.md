# Encoder weights

The embedding half of retrieval ships with `HashingEmbeddingModel`, a
dependency-free placeholder that captures lexical overlap and nothing else.
At 25% @1 on 350 real queries it loses to BM25 (44%) and to standard RRF (37%);
`RetrievalService` auto-selects asymmetric fusion weights `(1.0, 0.25)` to
compensate.

That placeholder is a floor, not a serving choice. Replacing it is the
largest remaining win in the stack, and it needs one of two things:

1. **Allowlist `huggingface.co` and `cdn-lfs.huggingface.co`** in the cloud
   environment's network settings. Weights load through `transformers`
   `AutoModel.from_pretrained(encoder_name)` as the spec describes.

2. **Publish the weights as a GitHub release asset** in this org. GitHub
   releases are reachable from this container today — the spaCy transformer
   model (497 MB) installed through that path to prove it.

For route 2, `ml/training/fetch_weights.py` downloads from any URL and verifies
SHA-256:

```
python ml/training/fetch_weights.py code-encoder \
    https://github.com/<org>/<repo>/releases/download/<tag>/<asset>.tar.gz \
    <expected-sha256> --extract
```

The archive is unpacked at `ml/weights/code-encoder/model/`, which is the
directory the bi-encoder loads from.

## Manifest

`manifest.json` records which artefacts the project uses by name. The fetcher
reads it so a fresh container catches up with one command:

```
python ml/training/fetch_weights.py --from-manifest
```

Currently empty, because no weights have been published to a reachable URL
yet.
