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
| `train_whiteboard_lora.py` | Fine-tunes a small open model (LoRA) to draw a project's architecture as `/sketch` lines, then scores it on held-out projects |
| `whiteboard_box_setup.sh` | What the rented GPU machine runs for the whiteboard job: dependencies, data from the manifest, training |

```sh
python ml/training/vast_run.py run --dry-run      # what would be rented, and the cost
python ml/training/setup_cross_encoder.py         # weights for a new machine
```

## The whiteboard model

The whiteboard AI's first step draws a project's architecture in one call
(`board_ai.draw`). The training data are public, permissively licensed
projects whose README draws their own architecture in Mermaid
(`ml/whiteboard_data/harvest.py`; manifest in `ml/data/whiteboard_sources.json`).
About one project in five is held out for evaluation.

```sh
python -m ml.whiteboard_data.harvest --from-manifest            # rebuild the examples
python -m ml.whiteboard_data.prepare                            # chat-format train / eval files
python -m ml.whiteboard_data.evaluate --mode sketch --use-saved-key   # one-shot score of a hosted model
python -m ml.whiteboard_data.evaluate --use-saved-key           # full builds, scored against the authors' diagrams

python ml/training/vast_run.py search --job whiteboard          # cheapest 24 GB cards (read-only)
python ml/training/vast_run.py run --job whiteboard --dry-run   # what would be rented
VAST_API_KEY=... GH_RESULTS_TOKEN=... \
  python ml/training/vast_run.py run --job whiteboard --max-price 0.45 --max-hours 3 --yes
```

The run pushes `metrics.json` and `predictions.jsonl` to a `gpu-results/<run>`
branch and the LoRA adapter as a release asset, then destroys the box. Its
held-out scores use the same scorer as `--mode sketch`, so they compare
directly with the hosted model. To use the adapter, serve the base model with
it behind an OpenAI-compatible server and point the draw step at it:
`PLOVANE_BOARD_AI_DRAW_PROVIDER=openai PLOVANE_OPENAI_BASE_URL=http://127.0.0.1:8000/v1 PLOVANE_BOARD_AI_DRAW_MODEL=<name>`.
