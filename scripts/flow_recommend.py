"""Integration flow: GUI -> B03 -> B05 -> B06.

    python scripts/flow_recommend.py --cloud http://127.0.0.1:8500 \\
                                     --local http://127.0.0.1:8701

Hits B03's ``/api/v1/recommendations`` with a canonical §5.1 body and prints
the §5.2 data from the envelope. Everything up the stack must be running:
B05 (``python -m backend.cloud_server.app``) and B06
(``python -m ml.serving.service.app``). Nothing is written to disk.
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


# Input: argv (list[str] | None)
# Output: int
# Processing: POST a §5.1 recommendation request and print the §5.2 result.
# Changed by: Nirmal
# Date: 2026-10-31
def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Exercise the recommendation flow")
    parser.add_argument("--local", default="http://127.0.0.1:8701")
    parser.add_argument("--user", default="demo_user")
    args = parser.parse_args(argv)

    body = {
        "project_id": "proj_demo",
        "description": "Add CSV parsing and validation",
        "language": "python",
        "tech_stack": ["python", "pandas"],
        "context_code": "def load_data(path):\n    pass",
        "requested_count": 3,
    }
    request = urllib.request.Request(
        args.local + "/api/v1/recommendations",
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json", "X-Plovane-User": args.user},
    )
    with urllib.request.urlopen(request, timeout=30) as reply:
        print(json.dumps(json.loads(reply.read()), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
