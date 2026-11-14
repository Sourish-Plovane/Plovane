# training

Scripts that make and fetch the models. They run from the repository root and
are not imported.

| Script | What it does |
|---|---|
| `train_cross_encoder.py` | Trains and evaluates the Stage 2 cross-encoder (`--final` for the shipped model). Writes to `ml/artifacts/`. |
| `vast_run.py` | Rents a vast.ai GPU, runs the training on it, brings the results back and destroys the machine |
| `gpu_box_setup.sh` | What the rented GPU machine runs: code, dependencies, dataset, training |
| `setup_cross_encoder.py` | Downloads and verifies the base encoder and the trained cross-encoder |
| `fetch_weights.py` | Downloads model weights into `ml/weights/` and checks their SHA-256 |

```sh
python ml/training/vast_run.py run --dry-run      # what would be rented, and the cost
python ml/training/setup_cross_encoder.py         # weights for a new machine
```
