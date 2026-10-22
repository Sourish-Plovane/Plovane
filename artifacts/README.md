# artifacts

Small results from training runs, kept in git as the record of what was
measured. The trained weights are too large for git; they are in GitHub
Releases and listed in `weights/manifest.json`.

| File | What it is |
|---|---|
| `cross_encoder_metrics.json` | 5-fold held-out accuracy of the Stage 2 cross-encoder (issue 35) |
| `cross_encoder_final.json` | Hash, size and training settings of the shipped cross-encoder |
