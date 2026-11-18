# artifacts

What training produced. Metrics and the small ranker files are committed as
the record of what was measured. The cross-encoder weights (505 MB) are too
large for git: they're in a GitHub release, listed in
`ml/weights/manifest.json`.

| File | What it is |
|---|---|
| `cross_encoder_metrics.json` | 5-fold held-out accuracy of the Stage 2 cross-encoder (issue 35) |
| `cross_encoder_final.json` | Hash, size and training settings of the shipped cross-encoder |
| `stage1_retrieval_fusion.txt` | Trained Stage 1 booster. **Uncertified, not served.** |
| `stage3_final.txt` | Trained Stage 3 booster. **Uncertified, not served.** |
| `MODELS.md` | Why neither booster is certified, and how to reproduce every file here |

Training writes `.pt`, `.log` and per-query score files here too. Those are
gitignored.
