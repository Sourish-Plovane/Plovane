# scripts

Command-line tools that are run, not imported.

| Script | What it does |
|---|---|
| `train_cross_encoder.py` | Trains and evaluates the Stage 2 cross-encoder (`--final` for the shipped model) |
| `vast_run.py` | Rents a vast.ai GPU, runs training on it and destroys it afterwards |
| `gpu_box_setup.sh` | What the rented GPU machine runs: code, dependencies, dataset, training |
| `setup_cross_encoder.py` | Downloads and verifies the trained cross-encoder on a new machine |
| `fetch_weights.py` | Downloads model weights and checks their SHA-256 |
| `check_comments.py` | Checks every Python function has the five-field comment block |
