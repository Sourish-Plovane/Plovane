"""User config for the plovane CLI, stored at ``~/.plovane/config.json``.

Kept as JSON (not TOML) so M1 has zero third-party deps for read/write.
A later milestone can migrate to ``config.toml`` without breaking callers:
only this module touches disk.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

CONFIG_DIR = Path(os.path.expanduser("~")) / ".plovane"
CONFIG_PATH = CONFIG_DIR / "config.json"

DEFAULTS: dict[str, Any] = {
    "user": os.environ.get("PLOVANE_USER", "local"),
    "endpoints": {
        "local": "http://127.0.0.1:8701",
        "cloud": "http://127.0.0.1:8500",
        "serving": "http://127.0.0.1:8600",
    },
    "settings": {
        "theme": "yellow",
        "ranker": {
            "requested_count": 3,
            "cross_encoder": True,
            "stage1_weight": 1.0,
            "stage3_weight": 1.0,
            "language": "python",
        },
    },
}


# Input: none
# Output: dict[str, Any]
# Processing: Load ~/.plovane/config.json, merging defaults for missing keys.
# Changed by: Sourish
# Date: 2026-10-31
def load() -> dict[str, Any]:
    if not CONFIG_PATH.exists():
        return _deep_copy(DEFAULTS)
    with CONFIG_PATH.open("r", encoding="utf-8") as handle:
        raw = json.load(handle)
    merged = _deep_copy(DEFAULTS)
    _deep_merge(merged, raw)
    return merged


# Input: dst (dict[str, Any]), src (dict[str, Any])
# Output: None
# Processing: Recursively overlay ``src`` on ``dst``, preserving nested defaults.
# Changed by: Sourish
# Date: 2026-10-31
def _deep_merge(dst: dict[str, Any], src: dict[str, Any]) -> None:
    for key, value in src.items():
        if isinstance(value, dict) and isinstance(dst.get(key), dict):
            _deep_merge(dst[key], value)
        else:
            dst[key] = value


# Input: config (dict[str, Any])
# Output: Path
# Processing: Write the config to ~/.plovane/config.json, creating the dir.
# Changed by: Sourish
# Date: 2026-10-31
def save(config: dict[str, Any]) -> Path:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    with CONFIG_PATH.open("w", encoding="utf-8") as handle:
        json.dump(config, handle, indent=2, sort_keys=True)
    return CONFIG_PATH


# Input: value (Any)
# Output: Any
# Processing: Deep-copy nested dict/list/scalar via json round-trip.
# Changed by: Sourish
# Date: 2026-10-31
def _deep_copy(value: Any) -> Any:
    return json.loads(json.dumps(value))
