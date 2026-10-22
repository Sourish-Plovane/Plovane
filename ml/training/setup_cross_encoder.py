"""Make the trained Stage 2 cross-encoder usable on this machine, and prove it.

One command from a fresh clone:

    GITHUB_TOKEN=... python ml/training/setup_cross_encoder.py

1. Fetches the base encoder (``microsoft/unixcoder-base``) into
   ``ml/weights/unixcoder-base`` if it is not there. The trained checkpoint is a
   ``state_dict``; the tokenizer, config and architecture come from here.
2. Fetches the fine-tuned weights named ``cross-encoder-stage2`` in
   ``ml/weights/manifest.json`` from the project's private GitHub release. That
   needs a token with read access to the repository in ``GITHUB_TOKEN``,
   ``GH_TOKEN`` or ``GH_RESULTS_TOKEN``.
3. Checks both files against the SHA-256 the manifest records, then loads the
   model and scores one query against a right and a wrong entry. Exits
   non-zero unless the right one wins.

Then, in code::

    from ml.ranking.cross_encoder import load_cross_encoder
    pipeline = RecommendationPipeline(
        ..., cross_encoder=load_cross_encoder("ml/weights/cross-encoder-stage2/cross_encoder_final.pt"))
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
import time
import urllib.error
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
WEIGHTS = Path(__file__).resolve().parents[1] / "weights"
sys.path.insert(0, str(ROOT))  # the ml package, without needing PYTHONPATH

from fetch_weights import download  # noqa: E402  (ml/training/ is sys.path[0])

MANIFEST = WEIGHTS / "manifest.json"
BASE_DIR = WEIGHTS / "unixcoder-base"
TRAINED_NAME = "cross-encoder-stage2"
TRAINED_FILE = "cross_encoder_final.pt"


# Input: path (Path)
# Output: str
# Processing: SHA-256 of a file, streamed.
# Changed by: Claude Code
# Date: 2026-10-18
def sha256_of(path: Path) -> str:
    """SHA-256 of a file, streamed so a 500 MB checkpoint is not read at once."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1 << 20):
            digest.update(chunk)
    return digest.hexdigest()


# Input: manifest (dict)
# Output: None
# Processing: Fetch the base encoder if missing and verify its weights.
# Changed by: Claude Code
# Date: 2026-10-18
def ensure_base_encoder(manifest: dict) -> None:
    """Fetch ``unixcoder-base`` if it is missing, and verify its weights."""
    files = manifest["weights"]["unixcoder-base"]["files"]
    weights = BASE_DIR / "pytorch_model.bin"
    if not all((BASE_DIR / name).exists() for name in files):
        # Xet transfers fail behind some proxies; plain HTTPS works everywhere.
        os.environ.setdefault("HF_HUB_DISABLE_XET", "1")
        from huggingface_hub import hf_hub_download

        for name in files:
            if not (BASE_DIR / name).exists():
                hf_hub_download("microsoft/unixcoder-base", name, local_dir=str(BASE_DIR))
    expected = files["pytorch_model.bin"]["sha256"]
    if sha256_of(weights) != expected:
        raise SystemExit(f"{weights} does not match the manifest's SHA-256; delete it and rerun")
    ensure_safetensors(weights)
    print(f"OK  base encoder      {BASE_DIR.relative_to(ROOT)}")


# Input: weights (Path)
# Output: None
# Processing: Write model.safetensors next to a verified pytorch_model.bin.
# Changed by: Claude Code
# Date: 2026-10-18
def ensure_safetensors(weights: Path) -> None:
    """Write ``model.safetensors`` next to the verified ``.bin``.

    transformers refuses to ``torch.load`` a ``.bin`` on torch < 2.6, and 2.2.2
    is the last torch built for Intel Macs. It prefers a safetensors file when
    one is present, which loads without torch.load, so the same base weights
    work on every torch. Converted only after the .bin's hash has passed.
    """
    target = weights.with_name("model.safetensors")
    if target.exists():
        return
    import torch
    from safetensors.torch import save_file

    state = torch.load(weights, map_location="cpu", weights_only=True)
    # clone(): tied weights share storage, which save_file rejects.
    save_file({k: v.contiguous().clone() for k, v in state.items()}, str(target))
    print(f"OK  converted         {target.relative_to(ROOT)} (for torch < 2.6)")


# Input: manifest (dict)
# Output: Path
# Processing: Fetch the fine-tuned checkpoint if missing and verify it.
# Changed by: Claude Code
# Date: 2026-10-18
def ensure_trained_weights(manifest: dict) -> Path:
    """Fetch the fine-tuned checkpoint if missing or wrong, and verify it."""
    record = manifest["weights"][TRAINED_NAME]["files"][TRAINED_FILE]
    path = WEIGHTS / TRAINED_NAME / TRAINED_FILE
    if not path.exists() or sha256_of(path) != record["sha256"]:
        # download() verifies the hash itself and removes a mismatch.
        try:
            download(record["url"], path, expected_sha256=record["sha256"])
        except urllib.error.HTTPError as exc:
            # A private repository answers 404, not 401, to a missing token.
            raise SystemExit(
                f"could not download {record['url']} ({exc.code}). The release is in a "
                "private repository: set GITHUB_TOKEN to a token that can read "
                "Sourish-Plovane/Plovane and rerun."
            ) from None
    print(f"OK  trained weights   {path.relative_to(ROOT)}  sha256 {record['sha256'][:12]}...")
    return path


# Input: weights (Path)
# Output: bool
# Processing: Load the model and check it prefers the right entry for a query.
# Changed by: Claude Code
# Date: 2026-10-18
def check(weights: Path) -> bool:
    """Load the model and score one query against a right and a wrong entry."""
    from ml.ranking.cross_encoder import load_cross_encoder
    from ml.schemas import Candidate, Entry, EntryType

    # Input: name (str), signature (str), docstring (str), code (str)
    # Output: Entry
    # Processing: A JavaScript library entry for the self-check.
    # Changed by: Claude Code
    # Date: 2026-10-22
    def entry(name: str, signature: str, docstring: str, code: str) -> Entry:
        return Entry(
            id=f"check:{name}", type=EntryType.HELPER, name=name, description=docstring,
            category="check", language="javascript", signature=signature,
            docstring=docstring, code=code,
        )

    right = entry(
        "combineURLs", "function combineURLs(baseURL, relativeURL)",
        "Creates a new URL by combining the specified URLs",
        "return relativeURL ? baseURL.replace(/\\/?\\/$/, '') + '/' + "
        "relativeURL.replace(/^\\/+/, '') : baseURL;",
    )
    wrong = entry(
        "debounce", "function debounce(func, wait)",
        "Delays invoking func until wait milliseconds have passed since the last call",
        "let timer; return (...args) => { clearTimeout(timer); "
        "timer = setTimeout(() => func(...args), wait); };",
    )

    started = time.time()
    model = load_cross_encoder(weights, encoder_name=str(BASE_DIR))
    loaded = time.time() - started
    query = "join a base url and a relative path into one url"
    scores = model.score_candidates(query, [Candidate(entry=right), Candidate(entry=wrong)])
    device = next(model.parameters()).device
    print(f"OK  loaded on {device} in {loaded:.1f}s")
    print(f"    {query!r}: combineURLs {scores[0]:+.2f}, debounce {scores[1]:+.2f}")
    return bool(scores[0] > scores[1])


# Input: none
# Output: int
# Processing: CLI entry point.
# Changed by: Claude Code
# Date: 2026-10-18
def main() -> int:
    manifest = json.loads(MANIFEST.read_text())
    ensure_base_encoder(manifest)
    weights = ensure_trained_weights(manifest)
    if not check(weights):
        print("FAIL  the model ranked the wrong entry first")
        return 1
    print("OK  the cross-encoder is usable")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
