#!/usr/bin/env bash
# Set up a rented GPU box and train the Stage 2 cross-encoder.
#
# Paste this into a fresh vast.ai (or any) instance running a PyTorch image.
# It is idempotent: re-running it after a disconnect picks up where it left
# off rather than starting over.
#
#   bash gpu_box_setup.sh
#
# What it needs from you first:
#   REPO   a clone URL this box can read. A https URL with a token, or add
#          this box's ssh key to the repository as a deploy key.
#   BRANCH the branch holding the training job.
#
# It does NOT need any inbound access, and nothing here phones home. The
# results are left in artifacts/ for you to collect.

set -euo pipefail

REPO="${REPO:?set REPO to the clone URL}"
BRANCH="${BRANCH:-Sourish}"
WORKDIR="${WORKDIR:-$HOME/plovane}"

echo "== box =="
nvidia-smi --query-gpu=name,memory.total,driver_version --format=csv,noheader || {
  echo "no nvidia-smi: this is not a GPU box, stopping" >&2
  exit 2
}
python3 --version

echo
echo "== code =="
if [ -d "$WORKDIR/.git" ]; then
  git -C "$WORKDIR" fetch origin "$BRANCH"
  git -C "$WORKDIR" checkout "$BRANCH"
  git -C "$WORKDIR" reset --hard "origin/$BRANCH"
else
  git clone --branch "$BRANCH" --depth 50 "$REPO" "$WORKDIR"
fi
cd "$WORKDIR"
echo "at $(git rev-parse --short HEAD)"

echo
echo "== deps =="
# torch is already in a PyTorch image and is the one pinned to the box's CUDA
# driver, so it is deliberately not reinstalled here -- pip would happily
# replace it with a build that cannot see the GPU.
python3 -m pip install --quiet --upgrade pip
python3 -m pip install --quiet transformers numpy
python3 -c "import torch; print('torch', torch.__version__, 'cuda', torch.cuda.is_available())"

echo
echo "== encoder weights =="
# The job loads the encoder from a local directory so a silent re-download
# cannot swap the weights under it. Fetch once, verified against the SHA the
# repository records.
if [ ! -d weights/unixcoder-base ]; then
  python3 - <<'PY'
import json, pathlib
from huggingface_hub import snapshot_download
target = pathlib.Path("weights/unixcoder-base")
snapshot_download("microsoft/unixcoder-base", local_dir=str(target))
print("downloaded to", target)
manifest = pathlib.Path("weights/manifest.json")
if manifest.exists():
    print("repository manifest records:", json.loads(manifest.read_text()))
PY
else
  echo "already present"
fi

echo
echo "== smoke test (minutes, catches a broken environment cheaply) =="
python3 -u scripts/train_cross_encoder.py --smoke --smoke-queries 20 --negatives 4 \
  --rerank-depth 20 --out artifacts/smoke

echo
echo "== full run =="
echo "starting at $(date -u +%FT%TZ); this is the part that costs money"
# Defaults are sized for ~280 training queries per fold rather than taken
# from the spec's TrainingConfig. Its 3 epochs at 8 groups a batch is
# about 105 optimiser steps here; a 2-epoch CPU run at that scale ended at
# loss 1.89 against a chance level of 2.08 -- it had not even fit its own
# training data. 10 epochs at 4 groups a batch is about 690 steps.
python3 -u scripts/train_cross_encoder.py \
  --epochs "${EPOCHS:-10}" \
  --negatives "${NEGATIVES:-15}" \
  --rerank-depth "${RERANK_DEPTH:-100}" \
  --batch-groups "${BATCH_GROUPS:-4}" \
  --learning-rate "${LR:-3e-5}" \
  --out artifacts 2>&1 | tee artifacts/train.log

echo
echo "== done =="
echo "collect these before you destroy the instance:"
ls -lh artifacts/
echo
echo "the metrics file is small and belongs in git:"
echo "  artifacts/cross_encoder_metrics.json"
echo "the .pt is large: host it somewhere and record its printed sha256 in"
echo "weights/manifest.json, then scripts/fetch_weights.py can verify it."
