#!/usr/bin/env bash
# Set up a rented GPU box and fine-tune the whiteboard model (LoRA).
#
# Run by vast_run.py (run --job whiteboard) from the repository root on the box.
# Needs GH_TOKEN (read access to this repository and public GitHub) to rebuild
# the examples from the committed manifest. Results land in
# ml/artifacts/whiteboard_lora/ for vast_run.py to collect.
#
#   WB_MODEL     base model (default Qwen/Qwen2.5-Coder-1.5B-Instruct)
#   WB_EPOCHS    training epochs (default 3)
#   WB_FLAGS     extra flags for train_whiteboard_lora.py (e.g. --four-bit for a 7B model)

set -euo pipefail

echo "== box =="
nvidia-smi --query-gpu=name,memory.total,driver_version --format=csv,noheader || {
  echo "no nvidia-smi: this is not a GPU box, stopping" >&2
  exit 2
}
python3 --version

echo
echo "== python packages =="
python3 -m pip install -q --upgrade pip
# Pinned: the training script is written and smoke-tested against exactly these.
python3 -m pip install -q "transformers==4.56.2" "peft==0.17.1" "trl==0.21.0" "accelerate==1.10.1" "datasets==4.0.0" bitsandbytes
python3 -m pip install -q -e . --no-deps

echo
echo "== data =="
export PYTHONPATH="$PWD"
python3 -m ml.whiteboard_data.harvest --from-manifest
python3 -m ml.whiteboard_data.prepare

echo
echo "== train =="
python3 ml/training/train_whiteboard_lora.py \
  --model "${WB_MODEL:-Qwen/Qwen2.5-Coder-1.5B-Instruct}" \
  --epochs "${WB_EPOCHS:-3}" ${WB_FLAGS:-}

echo
echo "== done =="
cat ml/artifacts/whiteboard_lora/metrics.json
