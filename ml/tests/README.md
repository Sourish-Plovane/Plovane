# tests

The pytest suite for `ml/`. Run it from the repository root with
`python -m pytest`. Each file is named after what it tests, for example
`test_ranking.py` for `ml/ranking/` and `test_model_service.py` for
`ml/serving/service/`. No test needs the model weights or a network
connection: the model tests use a small placeholder encoder.

`check_comments.py` checks the five-field comment standard, and
`test_comment_standard.py` runs it over every function outside this folder.
