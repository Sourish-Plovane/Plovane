# tests

The pytest suite. Run all of it from the repository root with `python -m pytest`.
Each file is named after what it tests, e.g. `test_ranking.py` for
`ml/ranking/`, `test_model_service.py` for `ml_models/service/`, and
`test_whiteboard_*.py` for `whiteboard/`. No test needs the model weights or a
network connection: model tests use a small placeholder encoder.
