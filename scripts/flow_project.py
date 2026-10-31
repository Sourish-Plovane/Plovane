"""Integration flow: GUI -> B03 -> B05 -> B09 (with B10 stub).

    python scripts/flow_project.py --local http://127.0.0.1:8701

Submits a tiny §5.5 project to B03, then fetches the §5.4 diagram. B09's
``diagram_client`` is the stub by default, so the diagram is one file-node per
submitted file and no edges. No real B10 is required.
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


# Input: method (str), url (str), body (dict | None)
# Output: dict
# Processing: Small JSON HTTP helper used by this script.
# Changed by: Nirmal
# Date: 2026-10-31
def _call(method: str, url: str, body: dict | None = None) -> dict:
    data = None
    headers = {"Accept": "application/json"}
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    with urllib.request.urlopen(request, timeout=30) as reply:
        return json.loads(reply.read())


# Input: argv (list[str] | None)
# Output: int
# Processing: POST a §5.5 submission, then GET the §5.4 diagram.
# Changed by: Nirmal
# Date: 2026-10-31
def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Exercise the project submission flow")
    parser.add_argument("--local", default="http://127.0.0.1:8701")
    parser.add_argument("--project", default="proj_demo")
    args = parser.parse_args(argv)

    submission = {
        "project_id": args.project,
        "files": [
            {"path": "src/main.py", "content": "def main():\n    print('hi')\n"},
            {"path": "src/utils.py", "content": "def helper():\n    return 42\n"},
        ],
    }
    print(json.dumps(_call("POST", args.local + "/api/v1/projects", submission), indent=2))
    print("---")
    print(json.dumps(_call("GET", f"{args.local}/api/v1/projects/{args.project}/diagram"),
                     indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
